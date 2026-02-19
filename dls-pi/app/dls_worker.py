from __future__ import annotations

import fcntl
import logging
from pathlib import Path
import threading
import time
from typing import Optional

from .config import Settings
from .models import CutJob
from .protocol import DataLinkClient, is_allowed_transition
from .storage import JobStore


logger = logging.getLogger(__name__)


class _ProcessLock:
    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._handle: Optional[object] = None

    def acquire(self) -> bool:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self._lock_path, "w", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._handle.write(str(threading.get_native_id()))
            self._handle.flush()
            return True
        except OSError:
            return False

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


class DataLinkServerWorker:
    def __init__(self, *, settings: Settings, store: JobStore) -> None:
        self._settings = settings
        self._store = store
        self._client = DataLinkClient(
            host=settings.cutter_host,
            port=settings.cutter_port,
            timeout_seconds=settings.dls_timeout_seconds,
            retry_total_ms=settings.send_retry_total_ms,
            retry_interval_ms=settings.send_retry_interval_ms,
        )
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._runtime_lock = threading.Lock()
        self._process_lock = _ProcessLock(settings.lock_file_path)

        self._is_running = False
        self._standby_mode = True
        self._last_error: Optional[str] = None
        self._current_status = -999
        self._previous_status = -999
        self._latest_jobs: list[CutJob] = []

    def start(self) -> bool:
        with self._runtime_lock:
            if self._thread and self._thread.is_alive():
                return True

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="graphtec-dls-worker",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5.0)

    def get_status(self) -> dict[str, object]:
        with self._runtime_lock:
            return {
                "is_running": self._is_running,
                "standby_mode": self._standby_mode,
                "current_status": self._current_status,
                "previous_status": self._previous_status,
                "last_error": self._last_error,
                "loaded_job_count": len(self._latest_jobs),
                "cutter_host": self._settings.cutter_host,
                "cutter_port": self._settings.cutter_port,
            }

    def _run(self) -> None:
        if not self._process_lock.acquire():
            with self._runtime_lock:
                self._is_running = False
                self._last_error = (
                    "Could not start DLS worker because lock file is already held."
                )
            logger.error(self._last_error)
            return

        with self._runtime_lock:
            self._is_running = True
            self._standby_mode = True
            self._last_error = None
            self._current_status = -999
            self._previous_status = -999

        logger.info(
            "DLS worker started (host=%s port=%s).",
            self._settings.cutter_host,
            self._settings.cutter_port,
        )

        try:
            while not self._stop_event.is_set():
                self._poll_once()
                time.sleep(self._settings.dls_poll_interval_seconds)
        finally:
            self._process_lock.release()
            with self._runtime_lock:
                self._is_running = False
            logger.info("DLS worker stopped.")

    def _poll_once(self) -> None:
        try:
            new_status = self._client.get_data_link_status()
        except Exception as exc:  # noqa: BLE001
            new_status = -999
            self._set_error(f"Communication error while reading status: {exc}")

        if new_status < 0 and new_status != -999:
            self._standby_mode = True
            self._set_error(f"Cutter returned status error: {new_status}")

        if new_status != self._current_status:
            self._on_status_transition(
                prev_status=self._current_status, new_status=new_status
            )

        self._previous_status = self._current_status
        self._current_status = new_status

    def _on_status_transition(self, *, prev_status: int, new_status: int) -> None:
        logger.info("DLS status changed: %s -> %s", prev_status, new_status)

        if not is_allowed_transition(prev_status, new_status):
            self._standby_mode = True
            self._set_error(
                f"Unexpected DLS transition detected: {prev_status} -> {new_status}"
            )

        if new_status == 1:
            self._standby_mode = False
            self._latest_jobs = []
            self._clear_error()
            return

        if new_status == 2 and not self._standby_mode:
            self._handle_requesting_job_list()
            return

        if new_status == 4 and not self._standby_mode:
            self._handle_job_determined()

    def _handle_requesting_job_list(self) -> None:
        try:
            barcode_link_info = self._client.get_barcode_link_info()
            self._latest_jobs = self._store.find_jobs_for_barcode(
                barcode_link_info
            )[:8]
            names = [job.name for job in self._latest_jobs]
            response = self._client.send_job_list(names)
            if response < 0:
                self._standby_mode = True
                self._set_error(
                    f"Job list was rejected by cutter with response {response}."
                )
        except Exception as exc:  # noqa: BLE001
            self._standby_mode = True
            self._set_error(f"Failed while sending job list: {exc}")

    def _handle_job_determined(self) -> None:
        try:
            selected_index = self._client.get_selected_job_index()
            if selected_index < 0:
                self._standby_mode = True
                self._set_error("No job selected by user.")
                return
            if selected_index >= len(self._latest_jobs):
                self._standby_mode = True
                self._set_error(
                    "Selected job index is out of range for latest job list."
                )
                return

            selected_job = self._latest_jobs[selected_index]
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
                self._standby_mode = True
                self._set_error(
                    "Cutter rejected regmark or command-type command."
                )
                return
            self._client.send_command_sequence(selected_job.command_sequence)
        except Exception as exc:  # noqa: BLE001
            self._standby_mode = True
            self._set_error(f"Failed while sending selected job: {exc}")

    def _set_error(self, message: str) -> None:
        with self._runtime_lock:
            self._last_error = message
        logger.warning(message)

    def _clear_error(self) -> None:
        with self._runtime_lock:
            self._last_error = None

