from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


def _bool_from_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_data_dir: Path
    database_path: Path
    lock_file_path: Path
    ingest_inbox_dir: Path
    ingest_processed_dir: Path
    ingest_error_dir: Path

    api_host: str
    api_port: int
    log_level: str

    dls_enabled: bool
    dls_poll_interval_seconds: float
    dls_timeout_seconds: float

    cutter_host: str
    cutter_port: int
    send_retry_total_ms: int
    send_retry_interval_ms: int

    ingest_enabled: bool
    ingest_poll_interval_seconds: float
    ingest_file_min_age_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        app_data_dir = Path(os.getenv("APP_DATA_DIR", "./data")).resolve()
        app_data_dir.mkdir(parents=True, exist_ok=True)

        database_path = Path(
            os.getenv("DATABASE_PATH", str(app_data_dir / "jobs.db"))
        ).resolve()
        lock_file_path = Path(
            os.getenv("LOCK_FILE_PATH", str(app_data_dir / "dls.lock"))
        ).resolve()
        ingest_inbox_dir = Path(
            os.getenv("INGEST_INBOX_DIR", str(app_data_dir / "inbox" / "cut"))
        ).resolve()
        ingest_processed_dir = Path(
            os.getenv(
                "INGEST_PROCESSED_DIR", str(app_data_dir / "processed")
            )
        ).resolve()
        ingest_error_dir = Path(
            os.getenv("INGEST_ERROR_DIR", str(app_data_dir / "errors"))
        ).resolve()

        ingest_inbox_dir.mkdir(parents=True, exist_ok=True)
        ingest_processed_dir.mkdir(parents=True, exist_ok=True)
        ingest_error_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            app_data_dir=app_data_dir,
            database_path=database_path,
            lock_file_path=lock_file_path,
            ingest_inbox_dir=ingest_inbox_dir,
            ingest_processed_dir=ingest_processed_dir,
            ingest_error_dir=ingest_error_dir,
            api_host=os.getenv("API_HOST", "0.0.0.0"),
            api_port=int(os.getenv("API_PORT", "8080")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            dls_enabled=_bool_from_env("DLS_ENABLED", True),
            dls_poll_interval_seconds=float(
                os.getenv("DLS_POLL_INTERVAL_SECONDS", "1.0")
            ),
            dls_timeout_seconds=float(os.getenv("DLS_TIMEOUT_SECONDS", "5.0")),
            cutter_host=os.getenv("CUTTER_HOST", "127.0.0.1"),
            cutter_port=int(os.getenv("CUTTER_PORT", "9100")),
            send_retry_total_ms=int(os.getenv("SEND_RETRY_TOTAL_MS", "3000")),
            send_retry_interval_ms=int(
                os.getenv("SEND_RETRY_INTERVAL_MS", "50")
            ),
            ingest_enabled=_bool_from_env("INGEST_ENABLED", True),
            ingest_poll_interval_seconds=float(
                os.getenv("INGEST_POLL_INTERVAL_SECONDS", "2.0")
            ),
            ingest_file_min_age_seconds=float(
                os.getenv("INGEST_FILE_MIN_AGE_SECONDS", "2.0")
            ),
        )
