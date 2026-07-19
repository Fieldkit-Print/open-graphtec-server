from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any, Optional

from .config import Settings
from .converters.pdf_to_gpgl import convert_pdf_to_gpgl
from .protocol import sanitize_job_name
from .storage import JobStore


logger = logging.getLogger(__name__)


BARCODE_PATTERN = re.compile(r"[A-Z0-9]{9}")
# A 9-char run not embedded in a longer alphanumeric run, so a filename like
# "ORDER12345678" cannot contribute a bogus barcode substring.
BARCODE_TOKEN_PATTERN = re.compile(r"(?<![A-Z0-9])[A-Z0-9]{9}(?![A-Z0-9])")


@dataclass
class IngestSnapshot:
    is_running: bool
    processed_count: int
    error_count: int
    last_error: Optional[str]
    last_processed_file: Optional[str]
    last_processed_time_utc: Optional[str]
    inbox_dir: str
    processed_dir: str
    error_dir: str


class HeadlessIngestWorker:
    def __init__(self, *, settings: Settings, store: JobStore) -> None:
        self._settings = settings
        self._store = store
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self._is_running = False
        self._processed_count = 0
        self._error_count = 0
        self._last_error: Optional[str] = None
        self._last_processed_file: Optional[str] = None
        self._last_processed_time_utc: Optional[str] = None

    def start(self) -> bool:
        with self._lifecycle_lock:
            thread = self._thread
            if thread and thread.is_alive():
                if not self._stop_event.is_set():
                    return True
                thread.join(timeout=10.0)
                if thread.is_alive():
                    logger.error(
                        "Cannot restart ingest worker: previous thread has "
                        "not terminated yet."
                    )
                    return False

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="headless-ingest-worker",
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
                    return False
            return True

    def get_status(self) -> dict[str, object]:
        with self._state_lock:
            snapshot = IngestSnapshot(
                is_running=self._is_running,
                processed_count=self._processed_count,
                error_count=self._error_count,
                last_error=self._last_error,
                last_processed_file=self._last_processed_file,
                last_processed_time_utc=self._last_processed_time_utc,
                inbox_dir=str(self._settings.ingest_inbox_dir),
                processed_dir=str(self._settings.ingest_processed_dir),
                error_dir=str(self._settings.ingest_error_dir),
            )
        return snapshot.__dict__

    def _run(self) -> None:
        with self._state_lock:
            self._is_running = True
            self._last_error = None
        logger.info(
            "Headless ingest worker started (inbox=%s).",
            self._settings.ingest_inbox_dir,
        )

        try:
            while not self._stop_event.is_set():
                try:
                    self._poll_once()
                except Exception as exc:  # noqa: BLE001
                    # The hot folder must never silently die: a vanished
                    # file or a permission blip is not fatal.
                    logger.exception("Ingest poll failed: %s", exc)
                    with self._state_lock:
                        self._last_error = f"Ingest poll failed: {exc}"
                self._stop_event.wait(
                    self._settings.ingest_poll_interval_seconds
                )
        finally:
            with self._state_lock:
                self._is_running = False
            logger.info("Headless ingest worker stopped.")

    def _poll_once(self) -> None:
        inbox = self._settings.ingest_inbox_dir
        now = time.time()

        candidates: list[tuple[float, Path]] = []
        for path in inbox.glob("*.pdf"):
            try:
                if not path.is_file():
                    continue
                candidates.append((path.stat().st_mtime, path))
            except FileNotFoundError:
                continue

        for mtime, pdf_path in sorted(candidates):
            if self._stop_event.is_set():
                return
            min_age = self._settings.ingest_file_min_age_seconds
            if now - mtime < min_age:
                continue

            sidecar_path = self._find_sidecar_json(pdf_path)
            if sidecar_path is not None:
                # The pair is only stable once the sidecar has also settled;
                # otherwise we can read a half-written JSON.
                try:
                    sidecar_age = now - sidecar_path.stat().st_mtime
                except FileNotFoundError:
                    sidecar_age = 0.0
                if sidecar_age < min_age:
                    continue
            else:
                # Give a late-arriving sidecar one extra poll interval
                # before falling back to filename-derived metadata.
                grace = min_age + self._settings.ingest_poll_interval_seconds
                if now - mtime < grace:
                    continue

            self._process_single_file(pdf_path, sidecar_path)

    def _process_single_file(
        self, pdf_path: Path, sidecar_path: Optional[Path]
    ) -> None:
        try:
            metadata = self._load_metadata(sidecar_path)
            barcode_link_info = self._resolve_barcode(pdf_path, metadata)
            job_name = sanitize_job_name(
                str(metadata.get("name") or pdf_path.stem),
                fallback=pdf_path.stem[:25] or "job",
            )
            command_type = int(metadata.get("command_type", 0))
            if command_type != 0:
                # The converter only emits GP-GL. Declaring HP-GL via ESC.d6
                # would make an AUTO-mode cutter mis-parse the job.
                raise ValueError(
                    "command_type must be 0 (GP-GL) for PDF ingest: the "
                    "converter emits GP-GL only."
                )

            regmark_fx = int(metadata.get("regmark_fx", 0))
            regmark_fy = int(metadata.get("regmark_fy", 0))
            regmark_rx = int(metadata.get("regmark_rx", 0))
            regmark_ry = int(metadata.get("regmark_ry", 0))

            pdf_size = pdf_path.stat().st_size
            if pdf_size > self._settings.max_upload_bytes:
                raise ValueError(
                    f"PDF is {pdf_size} bytes; maximum allowed is "
                    f"{self._settings.max_upload_bytes}."
                )
            pdf_bytes = pdf_path.read_bytes()
            command_bytes, details = convert_pdf_to_gpgl(
                pdf_bytes, steps_per_mm=self._settings.gpgl_steps_per_mm
            )

            # Move the files out of the inbox BEFORE inserting the job: if
            # the insert then fails, the files sit in processed/ and no job
            # exists (recoverable); the reverse order duplicates jobs when a
            # crash lands between insert and move.
            self._move_to_folder(
                source=pdf_path,
                target_folder=self._settings.ingest_processed_dir,
            )
            if sidecar_path and sidecar_path.exists():
                self._move_to_folder(
                    source=sidecar_path,
                    target_folder=self._settings.ingest_processed_dir,
                )

            job_id = self._store.create_job(
                name=job_name,
                barcode_link_info=barcode_link_info,
                command_type=command_type,
                regmark_fx=regmark_fx,
                regmark_fy=regmark_fy,
                regmark_rx=regmark_rx,
                regmark_ry=regmark_ry,
                command_sequence=command_bytes,
            )

            with self._state_lock:
                self._processed_count += 1
                self._last_error = None
                self._last_processed_file = str(pdf_path.name)
                self._last_processed_time_utc = (
                    datetime.now(timezone.utc).isoformat()
                )

            logger.info(
                "Imported cut file %s as job %s (job_id=%s, segments=%s).",
                pdf_path.name,
                job_name,
                job_id,
                details.get("segment_count"),
            )
        except Exception as exc:  # noqa: BLE001
            self._handle_ingest_error(pdf_path, sidecar_path, exc)

    def _find_sidecar_json(self, pdf_path: Path) -> Optional[Path]:
        candidates = [
            pdf_path.with_suffix(".job.json"),
            pdf_path.with_suffix(".meta.json"),
            pdf_path.with_suffix(".json"),
        ]
        for candidate in candidates:
            try:
                if candidate.exists() and candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    @staticmethod
    def _load_metadata(sidecar_path: Optional[Path]) -> dict[str, Any]:
        if sidecar_path is None:
            return {}
        text = sidecar_path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Sidecar JSON must be an object.")
        return data

    def _resolve_barcode(self, pdf_path: Path, metadata: dict[str, Any]) -> str:
        raw_barcode = metadata.get("barcode_link_info")
        if raw_barcode is None:
            # Fallback: infer barcode from filename.
            match = BARCODE_TOKEN_PATTERN.search(pdf_path.stem.upper())
            if match:
                raw_barcode = match.group(0)

        if raw_barcode is None:
            raise ValueError(
                "barcode_link_info is missing. "
                "Provide it in sidecar JSON or include a 9-char barcode in filename."
            )

        barcode = str(raw_barcode).strip().upper()
        if not BARCODE_PATTERN.fullmatch(barcode):
            raise ValueError(
                f"Invalid barcode_link_info: {barcode!r}. Must be 9 alphanumeric chars."
            )
        return barcode

    def _handle_ingest_error(
        self,
        pdf_path: Path,
        sidecar_path: Optional[Path],
        exc: Exception,
    ) -> None:
        error_message = f"{type(exc).__name__}: {exc}"
        logger.warning("Failed to import %s: %s", pdf_path.name, error_message)

        try:
            if pdf_path.exists():
                self._move_to_folder(
                    source=pdf_path,
                    target_folder=self._settings.ingest_error_dir,
                )
            if sidecar_path and sidecar_path.exists():
                self._move_to_folder(
                    source=sidecar_path,
                    target_folder=self._settings.ingest_error_dir,
                )

            error_log_path = (
                self._settings.ingest_error_dir
                / f"{pdf_path.stem}.error.txt"
            )
            error_log_path.write_text(
                error_message + "\n", encoding="utf-8"
            )
        except Exception as move_exc:  # noqa: BLE001
            logger.exception(
                "Failed while moving %s to error folder: %s",
                pdf_path.name,
                move_exc,
            )

        with self._state_lock:
            self._error_count += 1
            self._last_error = f"{pdf_path.name}: {error_message}"

    def _move_to_folder(self, *, source: Path, target_folder: Path) -> None:
        target_folder.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target_name = f"{timestamp}_{source.name}"
        target_path = target_folder / target_name
        counter = 1
        while target_path.exists():
            target_path = target_folder / f"{timestamp}_{counter}_{source.name}"
            counter += 1
        shutil.move(str(source), str(target_path))
