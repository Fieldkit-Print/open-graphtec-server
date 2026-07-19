from datetime import timezone

import pytest

from app.storage import JobStore


BARCODE = "A12345678"


@pytest.fixture()
def store(tmp_path):
    store = JobStore(str(tmp_path / "jobs.db"))
    yield store
    store.close()


def add_job(store, name, barcode=BARCODE):
    return store.create_job(
        name=name,
        barcode_link_info=barcode,
        command_type=0,
        regmark_fx=0,
        regmark_fy=0,
        regmark_rx=0,
        regmark_ry=0,
        command_sequence=b"J1\x03M0,0\x03",
    )


def test_barcode_lookup_returns_newest_first_capped_at_limit(store) -> None:
    for i in range(10):
        add_job(store, f"job-{i:02d}")

    metas = store.find_job_metas_for_barcode(BARCODE, limit=8)
    assert len(metas) == 8
    # Newest first: a re-submitted job must not be unreachable behind
    # eight stale ones.
    assert metas[0].name == "job-09"
    assert metas[-1].name == "job-02"


def test_metas_carry_no_command_blob_but_full_job_does(store) -> None:
    job_id = add_job(store, "job-a")
    metas = store.find_job_metas_for_barcode(BARCODE)
    assert not hasattr(metas[0], "command_sequence")

    full = store.get_job(job_id)
    assert full is not None
    assert full.command_sequence == b"J1\x03M0,0\x03"


def test_created_at_is_timezone_aware(store) -> None:
    job_id = add_job(store, "job-a")
    job = store.get_job(job_id)
    assert job.created_at.tzinfo is not None
    assert job.created_at.utcoffset().total_seconds() == 0


def test_legacy_naive_timestamps_are_read_as_utc(store) -> None:
    with store._lock:
        store._conn.execute(
            """
            INSERT INTO jobs (
                name, barcode_link_info, command_type, command_sequence,
                created_at
            ) VALUES ('legacy', ?, 0, X'414243', '2026-01-15 10:30:00')
            """,
            (BARCODE,),
        )
    metas = store.find_job_metas_for_barcode(BARCODE)
    assert metas[0].created_at.tzinfo == timezone.utc
