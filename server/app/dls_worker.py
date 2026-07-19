from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import fcntl
import logging
from pathlib import Path
import threading
import time
from typing import Optional

from .config import CutterConfig, Settings
from .models import CutJobMeta
from .protocol import (
    DLS_STATUS_LABELS,
    STEP_SIZE_CODE_TO_MM,
    DataLinkClient,
    is_allowed_transition,
)
from .storage import JobStore


logger = logging.getLogger(__name__)


class _ProcessLock:
    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._handle = None

    def acquire(self) -> bool:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._lock_path, "w", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        handle.write(str(threading.get_native_id()))
        handle.flush()
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


class DataLinkServerWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        store: JobStore,
        cutter: Optional[CutterConfig] = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._cutter = cutter or settings.cutters[0]
        self._client = DataLinkClient(
            host=self._cutter.host,
            port=self._cutter.port,
            timeout_seconds=settings.dls_timeout_seconds,
            retry_total_ms=settings.send_retry_total_ms,
            retry_interval_ms=settings.send_retry_interval_ms,
        )
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # Guards start/stop so they cannot interleave; never held during I/O.
        self._lifecycle_lock = threading.Lock()
        # Guards the runtime state fields below. RLock so helpers can be
        # called both with and without the lock already held.
        self._runtime_lock = threading.RLock()
        # One lock file per cutter: N workers in one process are fine, but
        # two processes must not both talk to the same machine.
        self._process_lock = _ProcessLock(
            settings.lock_file_path.with_name(
                f"dls-{self._cutter.name}.lock"
            )
        )

        self._is_running = False
        self._standby_mode = True
        self._last_error: Optional[str] = None
        self._current_status = -999
        self._previous_status = -999
        self._latest_job_metas: list[CutJobMeta] = []
        self._cutter_model: Optional[str] = None
        self._cutter_step_size_mm: Optional[float] = None
        # The cutter never pushes events; this log is synthesized from the
        # polling loop so the API/UI can show recent activity.
        self._events: deque[dict[str, str]] = deque(maxlen=50)

    def start(self) -> bool:
        with self._lifecycle_lock:
            thread = self._thread
            if thread and thread.is_alive():
                if not self._stop_event.is_set():
                    return True
                # A stop is in flight; wait for the old thread to finish so
                # a stop()/start() interleaving cannot leave the worker
                # silently stopped with both endpoints reporting success.
                thread.join(timeout=10.0)
                if thread.is_alive():
                    logger.error(
                        "Cannot restart DLS worker: previous thread has not "
                        "terminated yet."
                    )
                    return False

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name=f"graphtec-dls-{self._cutter.name}",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> bool:
        with self._lifecycle_lock:
            self._stop_event.set()
            thread = self._thread
            if thread and thread.is_alive():
                thread.join(timeout=10.0)
                if thread.is_alive():
                    logger.warning(
                        "DLS worker did not stop within timeout; it will exit "
                        "after its current network operation."
                    )
                    return False
            return True

    def get_status(self) -> dict[str, object]:
        with self._runtime_lock:
            return {
                "cutter_name": self._cutter.name,
                "is_running": self._is_running,
                "standby_mode": self._standby_mode,
                "current_status": self._current_status,
                "previous_status": self._previous_status,
                "last_error": self._last_error,
                "loaded_job_count": len(self._latest_job_metas),
                "cutter_host": self._cutter.host,
                "cutter_port": self._cutter.port,
                "cutter_model": self._cutter_model,
                "cutter_step_size_mm": self._cutter_step_size_mm,
                "current_status_label": DLS_STATUS_LABELS.get(
                    self._current_status, f"Unknown ({self._current_status})"
                ),
                "recent_events": list(self._events),
            }

    def _record_event(self, kind: str, message: str) -> None:
        with self._runtime_lock:
            self._events.append({
                "time": datetime.now(timezone.utc).isoformat(),
                "kind": kind,
                "message": message,
            })

    def _run(self) -> None:
        if not self._process_lock.acquire():
            message = (
                "Could not start DLS worker because lock file is already held."
            )
            with self._runtime_lock:
                self._is_running = False
                self._last_error = message
            logger.error(message)
            return

        with self._runtime_lock:
            self._is_running = True
            self._standby_mode = True
            self._last_error = None
            self._current_status = -999
            self._previous_status = -999
            self._latest_job_metas = []

        logger.info(
            "DLS worker '%s' started (host=%s port=%s).",
            self._cutter.name,
            self._cutter.host,
            self._cutter.port,
        )

        self._read_cutter_configuration()

        try:
            while not self._stop_event.is_set():
                try:
                    self._poll_once()
                except Exception as exc:  # noqa: BLE001
                    # The DLS never terminates on error (guideline 3-4):
                    # record it, go to Standby, keep polling.
                    self._enter_standby(f"Unexpected worker error: {exc}")
                self._stop_event.wait(self._settings.dls_poll_interval_seconds)
        finally:
            self._process_lock.release()
            with self._runtime_lock:
                self._is_running = False
            logger.info("DLS worker stopped.")

    def _read_cutter_configuration(self) -> None:
        """Best-effort startup handshake: identify the cutter and verify its
        GP-GL step size matches the converter's scale.

        A mismatch means every job would cut at the wrong size (e.g. a
        0.025 mm cutter renders 0.1 mm-scaled data at quarter size), so it
        is surfaced as an error — but the worker keeps running: the cutter
        may simply be offline right now.
        """
        try:
            model = self._client.get_model_info()
            step_code = self._client.get_step_size_code()
        except Exception as exc:  # noqa: BLE001
            logger.info("Cutter configuration read skipped (%s).", exc)
            return

        step_size_mm = STEP_SIZE_CODE_TO_MM.get(step_code)
        with self._runtime_lock:
            self._cutter_model = model or None
            self._cutter_step_size_mm = step_size_mm
        logger.info(
            "Cutter identified: %s (step size %s mm).",
            model or "unknown",
            step_size_mm if step_size_mm is not None else "unknown",
        )
        self._record_event(
            "cutter",
            f"Cutter identified: {model or 'unknown'} "
            f"(step size {step_size_mm if step_size_mm is not None else '?'} mm).",
        )

        if step_size_mm is None:
            self._set_error(
                f"Cutter returned unknown step size code {step_code!r}; "
                "cannot verify GP-GL scale."
            )
            return
        expected_steps_per_mm = round(1.0 / step_size_mm)
        if expected_steps_per_mm != self._settings.gpgl_steps_per_mm:
            self._set_error(
                f"Cutter step size is {step_size_mm} mm "
                f"({expected_steps_per_mm} steps/mm) but GPGL_STEPS_PER_MM "
                f"is {self._settings.gpgl_steps_per_mm}: jobs would cut at "
                "the wrong scale. Align the cutter setting or the config."
            )

    def _poll_once(self) -> None:
        try:
            new_status = self._client.get_data_link_status()
        except Exception as exc:  # noqa: BLE001
            new_status = -999
            self._set_error(f"Communication error while reading status: {exc}")

        if new_status < 0 and new_status != -999:
            # The cutter keeps returning this same error code to this server
            # until the true DLS cycles back to 0; store -999 so recovery is
            # not misread as an illegal transition from the error code.
            self._enter_standby(f"Cutter returned status error: {new_status}")
            new_status = -999

        with self._runtime_lock:
            previous = self._current_status
        if new_status != previous:
            self._on_status_transition(
                prev_status=previous, new_status=new_status
            )

        with self._runtime_lock:
            self._previous_status = self._current_status
            self._current_status = new_status

    def _on_status_transition(self, *, prev_status: int, new_status: int) -> None:
        logger.info("DLS status changed: %s -> %s", prev_status, new_status)
        self._record_event(
            "status",
            DLS_STATUS_LABELS.get(new_status, f"Status {new_status}"),
        )

        if not is_allowed_transition(prev_status, new_status):
            self._enter_standby(
                f"Unexpected DLS transition detected: {prev_status} -> "
                f"{new_status} (another Data Link server may be active)."
            )

        if new_status == 1:
            with self._runtime_lock:
                self._standby_mode = False
                self._latest_job_metas = []
                self._last_error = None
            return

        with self._runtime_lock:
            standby = self._standby_mode

        if new_status == 2 and not standby:
            self._handle_requesting_job_list()
            return

        if new_status == 4 and not standby:
            self._handle_job_determined()

    def _handle_requesting_job_list(self) -> None:
        try:
            barcode_link_info = self._client.get_barcode_link_info().upper()
            if not barcode_link_info:
                # Benign abort: the cutter left state 2 before answering.
                return
            if len(barcode_link_info) != 9 or not barcode_link_info.isalnum():
                self._enter_standby(
                    f"Cutter sent invalid barcode link info: "
                    f"{barcode_link_info!r} (expected 9 alphanumeric chars)."
                )
                return

            metas = self._store.find_job_metas_for_barcode(
                barcode_link_info, limit=8
            )
            with self._runtime_lock:
                self._latest_job_metas = metas
            names = [meta.name for meta in metas]
            self._record_event(
                "barcode",
                f"Barcode {barcode_link_info}: offering "
                f"{len(names)} job(s)" + (f" ({', '.join(names)})" if names else ""),
            )
            response = self._client.send_job_list(names)
            if response < 0:
                self._enter_standby(
                    f"Job list was rejected by cutter with response {response}."
                )
        except Exception as exc:  # noqa: BLE001
            self._enter_standby(f"Failed while sending job list: {exc}")

    def _handle_job_determined(self) -> None:
        try:
            selected_index = self._client.get_selected_job_index()
            if selected_index is None:
                self._enter_standby(
                    "Cutter reported no selected job (user chose a job that "
                    "was not sent by this server)."
                )
                return
            if selected_index < 0:
                self._enter_standby(
                    f"Cutter returned error {selected_index} for job selection."
                )
                return
            with self._runtime_lock:
                metas = list(self._latest_job_metas)
            if selected_index >= len(metas):
                self._enter_standby(
                    "Selected job index is out of range for latest job list."
                )
                return

            selected_meta = metas[selected_index]
            selected_job = self._store.get_job(selected_meta.id)
            if selected_job is None:
                self._enter_standby(
                    f"Selected job {selected_meta.id} no longer exists."
                )
                return

            regmark_response = self._client.set_regmark_position(
                selected_job.regmark_fx,
                selected_job.regmark_fy,
                selected_job.regmark_rx,
                selected_job.regmark_ry,
            )
            command_type_response = self._client.set_command_type(
                selected_job.command_type
            )
            if regmark_response < 0 or command_type_response < 0:
                self._enter_standby(
                    "Cutter rejected regmark or command-type command."
                )
                return
            self._client.send_command_sequence(selected_job.command_sequence)
            self._record_event(
                "job",
                f"Sent job '{selected_job.name}' to cutter "
                f"({len(selected_job.command_sequence)} bytes).",
            )
        except Exception as exc:  # noqa: BLE001
            self._enter_standby(f"Failed while sending selected job: {exc}")

    def _enter_standby(self, message: str) -> None:
        with self._runtime_lock:
            self._standby_mode = True
            self._last_error = message
        self._record_event("standby", message)
        logger.warning(message)

    def _set_error(self, message: str) -> None:
        with self._runtime_lock:
            self._last_error = message
        logger.warning(message)
