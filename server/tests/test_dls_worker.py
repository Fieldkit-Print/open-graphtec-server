import time

import pytest

from app.config import CutterConfig
from app.dls_worker import DataLinkServerWorker
from app.storage import JobStore
from tests.conftest import make_settings
from tests.fake_cutter import FakeCutter


BARCODE = "A12345678"
JOB_BYTES = b"J1\x03M0,0\x03D100,100\x03M0,0\x03"


@pytest.fixture()
def store(tmp_path):
    store = JobStore(str(tmp_path / "jobs.db"))
    yield store
    store.close()


def make_worker(tmp_path, store, port):
    settings = make_settings(tmp_path, cutter_port=port)
    return DataLinkServerWorker(settings=settings, store=store)


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_full_barcode_cut_cycle(tmp_path, store) -> None:
    store.create_job(
        name="JOB1",
        barcode_link_info=BARCODE,
        command_type=0,
        regmark_fx=10,
        regmark_fy=20,
        regmark_rx=0,
        regmark_ry=0,
        command_sequence=JOB_BYTES,
    )

    with FakeCutter() as cutter:
        cutter.barcode = BARCODE
        cutter.status = 0
        worker = make_worker(tmp_path, store, cutter.port)
        assert worker.start()
        try:
            assert wait_until(lambda: cutter.d1_count >= 1)
            # Standby until the cutter starts a scan cycle.
            assert worker.get_status()["standby_mode"] is True

            cutter.status = 1
            assert wait_until(
                lambda: worker.get_status()["standby_mode"] is False
            )

            cutter.status = 2
            assert wait_until(lambda: len(cutter.received_job_lists) == 1)
            assert cutter.received_job_lists[0] == ["JOB1"]

            cutter.selected_reply = "0"
            cutter.status = 4
            assert wait_until(lambda: len(cutter.received_sequences) == 1)

            assert cutter.received_regmarks == [b"\x1b.d5;0;10;20;0;0:"]
            assert cutter.received_command_types == [b"\x1b.d6;0:"]
            assert cutter.received_sequences == [JOB_BYTES]

            cutter.status = 5
            assert wait_until(
                lambda: worker.get_status()["current_status"] == 5
            )
            cutter.status = 0
            assert wait_until(
                lambda: worker.get_status()["current_status"] == 0
            )
            status = worker.get_status()
            assert status["last_error"] is None

            # The synthesized event log captured the whole cycle.
            kinds = [e["kind"] for e in status["recent_events"]]
            messages = " | ".join(e["message"] for e in status["recent_events"])
            assert "cutter" in kinds and "barcode" in kinds and "job" in kinds
            assert "JOB1" in messages
            assert status["current_status_label"] == "Stopped"
        finally:
            worker.stop()


def test_unknown_barcode_sends_empty_job_list(tmp_path, store) -> None:
    with FakeCutter() as cutter:
        cutter.barcode = "Z99999999"
        cutter.status = 1
        worker = make_worker(tmp_path, store, cutter.port)
        worker.start()
        try:
            assert wait_until(
                lambda: worker.get_status()["standby_mode"] is False
            )
            cutter.status = 2
            assert wait_until(lambda: len(cutter.received_job_lists) == 1)
            # The spec requires sending the empty list, not staying silent.
            assert cutter.received_job_lists[0] == []
        finally:
            worker.stop()


def test_illegal_transition_enters_standby(tmp_path, store) -> None:
    with FakeCutter() as cutter:
        cutter.status = 0
        worker = make_worker(tmp_path, store, cutter.port)
        worker.start()
        try:
            assert wait_until(lambda: cutter.d1_count >= 1)
            # 0 -> 4 means another server drove states 1-3.
            cutter.status = 4
            assert wait_until(
                lambda: worker.get_status()["current_status"] == 4
            )
            status = worker.get_status()
            assert status["standby_mode"] is True
            assert "transition" in (status["last_error"] or "")
            # In standby the worker must not have sent d4/d5/d6.
            assert cutter.received_regmarks == []
        finally:
            worker.stop()


def test_empty_selection_enters_standby_with_cause(tmp_path, store) -> None:
    store.create_job(
        name="JOB1",
        barcode_link_info=BARCODE,
        command_type=0,
        regmark_fx=0,
        regmark_fy=0,
        regmark_rx=0,
        regmark_ry=0,
        command_sequence=JOB_BYTES,
    )
    with FakeCutter() as cutter:
        cutter.barcode = BARCODE
        cutter.status = 1
        worker = make_worker(tmp_path, store, cutter.port)
        worker.start()
        try:
            assert wait_until(
                lambda: worker.get_status()["standby_mode"] is False
            )
            cutter.status = 2
            assert wait_until(lambda: len(cutter.received_job_lists) == 1)

            cutter.selected_reply = ""  # user picked a job we did not send
            cutter.status = 4
            assert wait_until(
                lambda: worker.get_status()["standby_mode"] is True
            )
            assert "no selected job" in worker.get_status()["last_error"]
            assert cutter.received_sequences == []
        finally:
            worker.stop()


def test_negative_status_maps_to_internal_sentinel(tmp_path, store) -> None:
    with FakeCutter() as cutter:
        cutter.status = -2
        worker = make_worker(tmp_path, store, cutter.port)
        worker.start()
        try:
            assert wait_until(lambda: cutter.d1_count >= 1)
            status = worker.get_status()
            assert wait_until(
                lambda: "status error" in (worker.get_status()["last_error"] or "")
            )
            status = worker.get_status()
            # Stored as -999 so recovery to 0 is not an "illegal transition".
            assert status["current_status"] == -999
            assert status["standby_mode"] is True

            cutter.status = 0
            assert wait_until(
                lambda: worker.get_status()["current_status"] == 0
            )
        finally:
            worker.stop()


def test_startup_handshake_reads_model_and_step_size(tmp_path, store) -> None:
    with FakeCutter() as cutter:
        cutter.status = 0
        cutter.model_info = "FAKE-9000, V1.00"
        cutter.step_size_code = 1  # 0.1 mm, matches default 10 steps/mm
        worker = make_worker(tmp_path, store, cutter.port)
        worker.start()
        try:
            assert wait_until(
                lambda: worker.get_status()["cutter_model"] is not None
            )
            status = worker.get_status()
            assert status["cutter_model"] == "FAKE-9000, V1.00"
            assert status["cutter_step_size_mm"] == 0.1
            assert status["last_error"] is None
        finally:
            worker.stop()


def test_startup_handshake_flags_step_size_mismatch(tmp_path, store) -> None:
    with FakeCutter() as cutter:
        cutter.status = 0
        cutter.step_size_code = 3  # 0.025 mm -> 40 steps/mm
        worker = make_worker(tmp_path, store, cutter.port)  # config: 10/mm
        worker.start()
        try:
            assert wait_until(
                lambda: "wrong scale" in (worker.get_status()["last_error"] or "")
            )
            assert worker.get_status()["cutter_step_size_mm"] == 0.025
            # Mismatch is a loud warning, not fatal: polling continues.
            before = cutter.d1_count
            assert wait_until(lambda: cutter.d1_count > before)
        finally:
            worker.stop()


def test_stop_start_cycle_restarts_cleanly(tmp_path, store) -> None:
    with FakeCutter() as cutter:
        cutter.status = 0
        worker = make_worker(tmp_path, store, cutter.port)
        assert worker.start()
        assert wait_until(lambda: worker.get_status()["is_running"])
        assert worker.stop()
        assert wait_until(lambda: not worker.get_status()["is_running"])
        # A start right after stop must yield a live worker, not silently
        # report success while the old thread winds down.
        assert worker.start()
        assert wait_until(lambda: worker.get_status()["is_running"])
        before = cutter.d1_count
        assert wait_until(lambda: cutter.d1_count > before)
        worker.stop()


def test_worker_survives_store_exceptions(tmp_path, store) -> None:
    with FakeCutter() as cutter:
        cutter.barcode = BARCODE
        cutter.status = 1
        worker = make_worker(tmp_path, store, cutter.port)

        def boom(*args, **kwargs):
            raise RuntimeError("database exploded")

        worker._store = type("BrokenStore", (), {
            "find_job_metas_for_barcode": staticmethod(boom),
            "get_job": staticmethod(boom),
        })()

        worker.start()
        try:
            assert wait_until(
                lambda: worker.get_status()["standby_mode"] is False
            )
            cutter.status = 2
            assert wait_until(
                lambda: worker.get_status()["standby_mode"] is True
            )
            # Still polling: the worker never dies on error.
            before = cutter.d1_count
            assert wait_until(lambda: cutter.d1_count > before)
        finally:
            worker.stop()


def test_two_cutters_share_one_job_store(tmp_path, store) -> None:
    """Two workers, two fake cutters, one store: each machine gets the
    job matching the barcode IT scanned."""
    store.create_job(
        name="LEFT-JOB", barcode_link_info="A00000LEFT"[:9],
        command_type=0, regmark_fx=0, regmark_fy=0, regmark_rx=0,
        regmark_ry=0, command_sequence=b"J1\x03M1,1\x03M0,0\x03",
    )
    store.create_job(
        name="RIGHT-JOB", barcode_link_info="A0000RIGHT"[:9],
        command_type=0, regmark_fx=0, regmark_fy=0, regmark_rx=0,
        regmark_ry=0, command_sequence=b"J1\x03M2,2\x03M0,0\x03",
    )

    with FakeCutter() as left, FakeCutter() as right:
        left.barcode = "A00000LEF"
        right.barcode = "A0000RIGH"
        left.status = right.status = 0
        settings = make_settings(
            tmp_path,
            cutters=(
                CutterConfig("left", "127.0.0.1", left.port),
                CutterConfig("right", "127.0.0.1", right.port),
            ),
        )
        workers = [
            DataLinkServerWorker(
                settings=settings, store=store, cutter=cutter
            )
            for cutter in settings.cutters
        ]
        for w in workers:
            assert w.start()
        try:
            names = [w.get_status()["cutter_name"] for w in workers]
            assert names == ["left", "right"]

            for cutter in (left, right):
                cutter.status = 1
            assert wait_until(
                lambda: all(
                    not w.get_status()["standby_mode"] for w in workers
                )
            )
            for cutter in (left, right):
                cutter.selected_reply = "0"
                cutter.status = 2
            assert wait_until(
                lambda: left.received_job_lists and right.received_job_lists
            )
            assert left.received_job_lists[0] == ["LEFT-JOB"]
            assert right.received_job_lists[0] == ["RIGHT-JOB"]

            for cutter in (left, right):
                cutter.status = 4
            assert wait_until(
                lambda: left.received_sequences and right.received_sequences
            )
            assert b"M1,1" in left.received_sequences[0]
            assert b"M2,2" in right.received_sequences[0]
        finally:
            for w in workers:
                w.stop()
