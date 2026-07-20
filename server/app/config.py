from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re


def _bool_from_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class CutterConfig:
    """One Graphtec cutter on the network."""

    name: str
    host: str
    port: int = 9100


def _parse_cutters(raw: str) -> tuple[CutterConfig, ...]:
    """Parse CUTTERS="name=host[:port],name2=host2[:port]"."""
    cutters: list[CutterConfig] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ValueError(
                f"CUTTERS entry {entry!r} must look like name=host[:port]."
            )
        name, _, address = entry.partition("=")
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
            raise ValueError(
                f"Cutter name {name!r} must be alphanumeric/dash/underscore."
            )
        host, _, port_text = address.strip().partition(":")
        port = int(port_text) if port_text else 9100
        cutters.append(CutterConfig(name=name, host=host, port=port))
    names = [c.name for c in cutters]
    if len(names) != len(set(names)):
        raise ValueError("Cutter names in CUTTERS must be unique.")
    if not cutters:
        raise ValueError("CUTTERS was set but contained no cutters.")
    return tuple(cutters)


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

    cutters: tuple[CutterConfig, ...]
    send_retry_total_ms: int
    send_retry_interval_ms: int

    ingest_enabled: bool
    ingest_poll_interval_seconds: float
    ingest_file_min_age_seconds: float

    api_key: str
    max_upload_bytes: int
    gpgl_steps_per_mm: int

    cut_spot_colors: tuple[str, ...]
    barcode_prefix: str
    print_ingest_enabled: bool
    print_inbox_dir: Path
    print_outbox_dir: Path
    print_error_dir: Path

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

        print_inbox_dir = Path(
            os.getenv("PRINT_INBOX_DIR", str(app_data_dir / "inbox" / "print"))
        ).resolve()
        print_outbox_dir = Path(
            os.getenv("PRINT_OUTBOX_DIR", str(app_data_dir / "outbox" / "print"))
        ).resolve()
        print_error_dir = Path(
            os.getenv("PRINT_ERROR_DIR", str(app_data_dir / "errors"))
        ).resolve()

        for folder in (
            ingest_inbox_dir, ingest_processed_dir, ingest_error_dir,
            print_inbox_dir, print_outbox_dir, print_error_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)

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
            # Off by default: the same default as the compose files, and a
            # bare-metal run should not poll 127.0.0.1:9100 unasked.
            dls_enabled=_bool_from_env("DLS_ENABLED", False),
            # Spec ranges (DLS guideline): poll 0.5-2.0 s, timeout 3-10 s.
            dls_poll_interval_seconds=_clamp(
                float(os.getenv("DLS_POLL_INTERVAL_SECONDS", "1.0")), 0.5, 2.0
            ),
            dls_timeout_seconds=_clamp(
                float(os.getenv("DLS_TIMEOUT_SECONDS", "5.0")), 3.0, 10.0
            ),
            # CUTTERS="left=192.168.1.104:9100,right=192.168.1.105" wins;
            # otherwise fall back to the single-cutter CUTTER_HOST/PORT.
            cutters=(
                _parse_cutters(os.environ["CUTTERS"])
                if os.getenv("CUTTERS", "").strip()
                else (
                    CutterConfig(
                        name=os.getenv("CUTTER_NAME", "cutter"),
                        host=os.getenv("CUTTER_HOST", "127.0.0.1"),
                        port=int(os.getenv("CUTTER_PORT", "9100")),
                    ),
                )
            ),
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
            # Empty string = auth disabled (trusted-network mode).
            api_key=os.getenv("API_KEY", "").strip(),
            max_upload_bytes=int(
                os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))
            ),
            # Steps per mm of the cutter's GP-GL STEP SIZE setting
            # (10 = 0.1 mm factory default; also 20, 40, or 100).
            gpgl_steps_per_mm=int(os.getenv("GPGL_STEPS_PER_MM", "10")),
            # Spot color names accepted as legacy cut-line markers
            # (ISO 19593-1 processing steps are always accepted).
            cut_spot_colors=tuple(
                n.strip() for n in
                os.getenv("CUT_SPOT_COLORS", "CutContour").split(",")
                if n.strip()
            ),
            # First char(s) of server-minted barcodes; must not start
            # with 'G' (reserved by Graphtec).
            barcode_prefix=os.getenv("BARCODE_PREFIX", "F").strip().upper(),
            print_ingest_enabled=_bool_from_env("PRINT_INGEST_ENABLED", True),
            print_inbox_dir=print_inbox_dir,
            print_outbox_dir=print_outbox_dir,
            print_error_dir=print_error_dir,
        )
