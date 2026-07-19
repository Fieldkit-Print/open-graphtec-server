import json
import os
import time

import pytest

from app.ingest_worker import HeadlessIngestWorker
from app.storage import JobStore
from tests.conftest import build_pdf, make_settings


BARCODE = "A12345678"
CUT_PDF = build_pdf("10 10 m 100 100 l S", height=300)


@pytest.fixture()
def store(tmp_path):
    store = JobStore(str(tmp_path / "jobs.db"))
    yield store
    store.close()


def make_worker(tmp_path, store, **overrides):
    settings = make_settings(tmp_path, **overrides)
    return HeadlessIngestWorker(settings=settings, store=store), settings


def age_file(path, seconds=60):
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def test_pdf_with_sidecar_pair_is_ingested(tmp_path, store) -> None:
    worker, settings = make_worker(tmp_path, store)
    pdf_path = settings.ingest_inbox_dir / "label.pdf"
    pdf_path.write_bytes(CUT_PDF)
    sidecar = settings.ingest_inbox_dir / "label.job.json"
    sidecar.write_text(json.dumps({
        "name": "front-label-001",
        "barcode_link_info": BARCODE,
        "command_type": 0,
        "regmark_fx": 100,
        "regmark_fy": 200,
    }))
    age_file(pdf_path)
    age_file(sidecar)

    worker._poll_once()

    jobs = store.find_job_metas_for_barcode(BARCODE)
    assert len(jobs) == 1
    assert jobs[0].name == "front-label-001"
    assert jobs[0].regmark_fx == 100
    assert not pdf_path.exists()
    assert not sidecar.exists()
    assert len(list(settings.ingest_processed_dir.iterdir())) == 2


def test_fresh_sidecar_defers_pickup(tmp_path, store) -> None:
    worker, settings = make_worker(
        tmp_path, store, ingest_file_min_age_seconds=30.0
    )
    pdf_path = settings.ingest_inbox_dir / "label.pdf"
    pdf_path.write_bytes(CUT_PDF)
    sidecar = settings.ingest_inbox_dir / "label.job.json"
    sidecar.write_text(json.dumps({"barcode_link_info": BARCODE}))
    age_file(pdf_path, seconds=60)  # PDF is old enough
    # Sidecar just landed: the pair is not stable yet.

    worker._poll_once()

    assert pdf_path.exists()
    assert store.find_job_metas_for_barcode(BARCODE) == []


def test_missing_sidecar_gets_grace_before_filename_fallback(
    tmp_path, store
) -> None:
    worker, settings = make_worker(
        tmp_path, store,
        ingest_file_min_age_seconds=30.0,
        ingest_poll_interval_seconds=30.0,
    )
    pdf_path = settings.ingest_inbox_dir / f"cut_{BARCODE}.pdf"
    pdf_path.write_bytes(CUT_PDF)
    age_file(pdf_path, seconds=45)  # older than min age, younger than grace

    worker._poll_once()
    assert pdf_path.exists(), "should wait one extra poll for a sidecar"

    age_file(pdf_path, seconds=90)
    worker._poll_once()
    assert not pdf_path.exists()
    assert len(store.find_job_metas_for_barcode(BARCODE)) == 1


def test_barcode_not_extracted_from_longer_run(tmp_path, store) -> None:
    worker, settings = make_worker(tmp_path, store)
    # 14 alphanumeric chars: no valid 9-char token may be carved out.
    pdf_path = settings.ingest_inbox_dir / "ORDER1234567890.pdf"
    pdf_path.write_bytes(CUT_PDF)
    age_file(pdf_path)

    worker._poll_once()

    error_files = list(settings.ingest_error_dir.iterdir())
    assert any(f.name.endswith(".error.txt") for f in error_files)
    assert store.list_jobs() == []


def test_hpgl_command_type_is_rejected(tmp_path, store) -> None:
    worker, settings = make_worker(tmp_path, store)
    pdf_path = settings.ingest_inbox_dir / "label.pdf"
    pdf_path.write_bytes(CUT_PDF)
    sidecar = settings.ingest_inbox_dir / "label.job.json"
    sidecar.write_text(json.dumps({
        "barcode_link_info": BARCODE,
        "command_type": 1,
    }))
    age_file(pdf_path)
    age_file(sidecar)

    worker._poll_once()

    assert store.list_jobs() == []
    error_texts = [
        f.read_text()
        for f in settings.ingest_error_dir.iterdir()
        if f.name.endswith(".error.txt")
    ]
    assert any("GP-GL only" in text for text in error_texts)


def test_long_job_name_is_sanitized_not_rejected(tmp_path, store) -> None:
    worker, settings = make_worker(tmp_path, store)
    pdf_path = settings.ingest_inbox_dir / "label.pdf"
    pdf_path.write_bytes(CUT_PDF)
    sidecar = settings.ingest_inbox_dir / "label.job.json"
    sidecar.write_text(json.dumps({
        "name": "a-very-long-job-name-that-exceeds-the-panel-limit",
        "barcode_link_info": BARCODE,
    }))
    age_file(pdf_path)
    age_file(sidecar)

    worker._poll_once()

    jobs = store.find_job_metas_for_barcode(BARCODE)
    assert len(jobs) == 1
    assert len(jobs[0].name) == 25


def test_worker_thread_survives_poll_exceptions(tmp_path, store) -> None:
    worker, settings = make_worker(tmp_path, store)

    calls = {"count": 0}
    original = worker._poll_once

    def flaky() -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise FileNotFoundError("file vanished mid-scan")
        original()

    worker._poll_once = flaky
    assert worker.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and calls["count"] < 3:
            time.sleep(0.02)
        assert calls["count"] >= 3, "loop must keep polling after an exception"
        assert worker.get_status()["is_running"] is True
    finally:
        worker.stop()


def test_ingested_files_move_even_when_conversion_fails(
    tmp_path, store
) -> None:
    worker, settings = make_worker(tmp_path, store)
    pdf_path = settings.ingest_inbox_dir / f"cut_{BARCODE}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 not really a pdf")
    age_file(pdf_path)

    worker._poll_once()

    assert not pdf_path.exists()
    assert store.list_jobs() == []
    assert any(
        f.name.endswith(".error.txt")
        for f in settings.ingest_error_dir.iterdir()
    )
