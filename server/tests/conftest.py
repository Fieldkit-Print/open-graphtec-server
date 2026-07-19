from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings


def build_pdf(
    content_stream: str, *, width: float = 300, height: float = 300
) -> bytes:
    """Assemble a minimal single-page PDF around a raw content stream."""
    # The trailing newline is part of the stream data: without a delimiter
    # after the final operator, pdfminer's tokenizer drops it (PSEOF).
    stream_bytes = (content_stream + "\n").encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 {width:g} {height:g}] /Contents 4 0 R >>"
        ).encode("ascii"),
        b"<< /Length "
        + str(len(stream_bytes)).encode("ascii")
        + b" >>\nstream\n"
        + stream_bytes
        + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii")
        out += body
        out += b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Build Settings directly (bypassing from_env and its range clamps) so
    tests can use fast poll intervals."""
    data_dir = tmp_path / "data"
    inbox = data_dir / "inbox" / "cut"
    processed = data_dir / "processed"
    errors = data_dir / "errors"
    for folder in (data_dir, inbox, processed, errors):
        folder.mkdir(parents=True, exist_ok=True)

    values = dict(
        app_data_dir=data_dir,
        database_path=data_dir / "jobs.db",
        lock_file_path=data_dir / "dls.lock",
        ingest_inbox_dir=inbox,
        ingest_processed_dir=processed,
        ingest_error_dir=errors,
        api_host="127.0.0.1",
        api_port=8080,
        log_level="INFO",
        dls_enabled=False,
        dls_poll_interval_seconds=0.05,
        dls_timeout_seconds=1.0,
        cutter_host="127.0.0.1",
        cutter_port=9,
        send_retry_total_ms=200,
        send_retry_interval_ms=50,
        ingest_enabled=False,
        ingest_poll_interval_seconds=0.05,
        ingest_file_min_age_seconds=0.0,
        api_key="",
        max_upload_bytes=20 * 1024 * 1024,
        gpgl_steps_per_mm=10,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)
