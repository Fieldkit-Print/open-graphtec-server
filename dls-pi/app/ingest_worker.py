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
from .storage import JobStore


logger = logging.getLogger(__name__)


BARCODE_PATTERN = re.compile(r"[A-Z0-9]{9}")


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
        self._state_lock = threading.Lock()

        self._is_running = False
        self._processed_count = 0
        self._error_count = 0
        self._last_error: Optional[str] = None
        self._last_processed_file: Optional[str] = None
        self._last_processed_time_utc: Optional[str] = None

    def start(self) -> bool:
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return True

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="headless-ingest-worker",
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
                self._poll_once()
                time.sleep(self._settings.ingest_poll_interval_seconds)
        finally:
            with self._state_lock:
                self._is_running = False
            logger.info("Headless ingest worker stopped.")

    def _poll_once(self) -> None:
        inbox = self._settings.ingest_inbox_dir
        pdf_files = sorted(
            [p for p in inbox.glob("*.pdf") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
        )
        now = time.time()

        for pdf_path in pdf_files:
            file_age = now - pdf_path.stat().st_mtime
            if file_age < self._settings.ingest_file_min_age_seconds:
                continue
            self._process_single_file(pdf_path)

    def _process_single_file(self, pdf_path: Path) -> None:
        sidecar_path = self._find_sidecar_json(pdf_path)
        try:
            metadata = self._load_metadata(sidecar_path)
            barcode_link_info = self._resolve_barcode(pdf_path, metadata)
            job_name = str(metadata.get("name") or pdf_path.stem).strip()
            command_type = int(metadata.get("command_type", 0))
            if command_type not in (0, 1):
                raise ValueError(
                    "command_type must be 0 (GP-GL) or 1 (HP-GL)."
                )

            regmark_fx = int(metadata.get("regmark_fx", 0))
            regmark_fy = int(metadata.get("regmark_fy", 0))
            regmark_rx = int(metadata.get("regmark_rx", 0))
            regmark_ry = int(metadata.get("regmark_ry", 0))

            pdf_bytes = pdf_path.read_bytes()
            command_bytes, details = convert_pdf_to_gpgl(pdf_bytes)
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

            self._move_to_folder(
                source=pdf_path,
                target_folder=self._settings.ingest_processed_dir,
            )
            if sidecar_path and sidecar_path.exists():
                self._move_to_folder(
                    source=sidecar_path,
                    target_folder=self._settings.ingest_processed_dir,
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
            if candidate.exists() and candidate.is_file():
                return candidate
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
            match = BARCODE_PATTERN.search(pdf_path.stem.upper())
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

