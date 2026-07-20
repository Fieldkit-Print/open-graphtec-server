"""Hot-folder worker for print preparation.

Drop a pre-imposed print PDF (with cut paths on an ISO 19593-1 layer or in
a CutContour spot color) into inbox/print/. The marked, print-ready file
appears in outbox/print/ named <original>_<barcode>.pdf, and the matching
cut job is registered. Failures move to the error folder with a .error.txt.

A 9-character barcode token in the filename is honored; otherwise the
server mints one.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import shutil
import threading
import time
from typing import Optional

from .config import Settings
from .ingest_worker import BARCODE_TOKEN_PATTERN
from .print_prepare import prepare_print
from .storage import JobStore


logger = logging.getLogger(__name__)


class PrintPrepareWorker:
    def __init__(self, *, settings: Settings, store: JobStore) -> None:
        self._settings = settings
        self._store = store
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self._is_running = False
        self._prepared_count = 0
        self._error_count = 0
        self._last_error: Optional[str] = None
        self._last_prepared_file: Optional[str] = None

    def start(self) -> bool:
        with self._lifecycle_lock:
            thread = self._thread
            if thread and thread.is_alive():
                if not self._stop_event.is_set():
                    return True
                thread.join(timeout=10.0)
                if thread.is_alive():
                    return False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="print-prepare-worker", daemon=True
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
            return {
                "is_running": self._is_running,
                "prepared_count": self._prepared_count,
                "error_count": self._error_count,
                "last_error": self._last_error,
                "last_prepared_file": self._last_prepared_file,
                "inbox_dir": str(self._settings.print_inbox_dir),
                "outbox_dir": str(self._settings.print_outbox_dir),
            }

    def _run(self) -> None:
        with self._state_lock:
            self._is_running = True
            self._last_error = None
        logger.info(
            "Print prepare worker started (inbox=%s).",
            self._settings.print_inbox_dir,
        )
        try:
            while not self._stop_event.is_set():
                try:
                    self._poll_once()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Print prepare poll failed: %s", exc)
                    with self._state_lock:
                        self._last_error = f"Poll failed: {exc}"
                self._stop_event.wait(
                    self._settings.ingest_poll_interval_seconds
                )
        finally:
            with self._state_lock:
                self._is_running = False
            logger.info("Print prepare worker stopped.")

    def _poll_once(self) -> None:
        now = time.time()
        candidates: list[tuple[float, Path]] = []
        for path in self._settings.print_inbox_dir.glob("*.pdf"):
            try:
                if path.is_file():
                    candidates.append((path.stat().st_mtime, path))
            except FileNotFoundError:
                continue
        for mtime, pdf_path in sorted(candidates):
            if self._stop_event.is_set():
                return
            if now - mtime < self._settings.ingest_file_min_age_seconds:
                continue
            self._process(pdf_path)

    def _process(self, pdf_path: Path) -> None:
        try:
            size = pdf_path.stat().st_size
            if size > self._settings.max_upload_bytes:
                raise ValueError(
                    f"PDF is {size} bytes; maximum is "
                    f"{self._settings.max_upload_bytes}."
                )
            match = BARCODE_TOKEN_PATTERN.search(pdf_path.stem.upper())
            result = prepare_print(
                pdf_path.read_bytes(),
                settings=self._settings,
                store=self._store,
                name=pdf_path.stem,
                barcode_link_info=match.group(0) if match else None,
            )
            out_name = f"{pdf_path.stem}_{result.barcode_link_info}.pdf"
            out_path = self._settings.print_outbox_dir / out_name
            self._settings.print_outbox_dir.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(result.pdf_bytes)
            self._move_away(pdf_path, self._settings.ingest_processed_dir)
            with self._state_lock:
                self._prepared_count += 1
                self._last_error = None
                self._last_prepared_file = out_name
            logger.info(
                "Prepared %s -> %s (job %s, barcode %s, %d cut segments).",
                pdf_path.name, out_name, result.job_id,
                result.barcode_link_info, result.segment_count,
            )
            for warning in result.warnings:
                logger.warning("%s: %s", pdf_path.name, warning)
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"
            logger.warning("Print prepare failed for %s: %s",
                           pdf_path.name, message)
            try:
                if pdf_path.exists():
                    self._move_away(pdf_path, self._settings.print_error_dir)
                (self._settings.print_error_dir /
                 f"{pdf_path.stem}.error.txt").write_text(
                    message + "\n", encoding="utf-8"
                )
            except Exception:  # noqa: BLE001
                logger.exception("Could not move %s to errors.", pdf_path)
            with self._state_lock:
                self._error_count += 1
                self._last_error = f"{pdf_path.name}: {message}"

    @staticmethod
    def _move_away(source: Path, target_folder: Path) -> None:
        target_folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = target_folder / f"{stamp}_{source.name}"
        counter = 1
        while target.exists():
            target = target_folder / f"{stamp}_{counter}_{source.name}"
            counter += 1
        shutil.move(str(source), str(target))
