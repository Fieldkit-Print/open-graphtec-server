import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.dls_worker import DataLinkServerWorker
from app.storage import JobStore
from app.webhook import WebhookNotifier
from tests.conftest import make_settings
from tests.fake_cutter import FakeCutter


class _Receiver:
    """Tiny HTTP server capturing webhook deliveries."""

    def __init__(self) -> None:
        received = self.received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                received.append({
                    "body": body,
                    "json": json.loads(body),
                    "signature": self.headers.get("X-OGS-Signature"),
                })
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):  # silence
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}/hook"
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def wait_for(self, count: int, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.received) >= count:
                return True
            time.sleep(0.02)
        return False


@pytest.fixture()
def receiver():
    r = _Receiver()
    yield r
    r.stop()


def test_notifier_delivers_signed_payload(receiver) -> None:
    notifier = WebhookNotifier(receiver.url, secret="shh")
    notifier.notify("job.sent", cutter="left", job_id=7)
    assert receiver.wait_for(1)
    delivery = receiver.received[0]
    assert delivery["json"]["event"] == "job.sent"
    assert delivery["json"]["job_id"] == 7
    assert "timestamp" in delivery["json"]
    expected = hmac.new(b"shh", delivery["body"], hashlib.sha256).hexdigest()
    assert delivery["signature"] == f"sha256={expected}"
    assert notifier.get_status()["delivered"] == 1


def test_disabled_notifier_is_a_no_op(receiver) -> None:
    notifier = WebhookNotifier("")
    notifier.notify("job.sent")
    assert not notifier.enabled
    assert notifier.get_status()["configured"] is False


def test_worker_fires_job_sent_and_no_match(tmp_path, receiver) -> None:
    store = JobStore(str(tmp_path / "jobs.db"))
    store.create_job(
        name="HOOKED-JOB", barcode_link_info="A0900HOOK",
        command_type=0, regmark_fx=0, regmark_fy=0, regmark_rx=0,
        regmark_ry=0, command_sequence=b"J1\x03M0,0\x03",
    )
    notifier = WebhookNotifier(receiver.url)
    with FakeCutter() as cutter:
        cutter.barcode = "A0900HOOK"
        cutter.status = 0
        settings = make_settings(tmp_path, cutter_port=cutter.port)
        worker = DataLinkServerWorker(
            settings=settings, store=store, notifier=notifier
        )
        worker.start()
        try:
            cutter.status = 1
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and (
                worker.get_status()["standby_mode"]
            ):
                time.sleep(0.02)
            cutter.selected_reply = "0"
            cutter.status = 2
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not cutter.received_job_lists:
                time.sleep(0.02)
            cutter.status = 4
            assert receiver.wait_for(1, timeout=8)
            event = receiver.received[0]["json"]
            assert event["event"] == "job.sent"
            assert event["cutter"] == "test"
            assert event["name"] == "HOOKED-JOB"
            assert event["barcode_link_info"] == "A0900HOOK"
            assert event["command_bytes"] == len(b"J1\x03M0,0\x03")

            # Second cycle with an unknown barcode -> barcode.no_match
            cutter.status = 5
            time.sleep(0.2)
            cutter.status = 0
            time.sleep(0.2)
            cutter.barcode = "A0900MISS"
            cutter.status = 1
            time.sleep(0.2)
            cutter.status = 2
            assert receiver.wait_for(2, timeout=8)
            miss = receiver.received[1]["json"]
            assert miss["event"] == "barcode.no_match"
            assert miss["barcode_link_info"] == "A0900MISS"
        finally:
            worker.stop()
            store.close()
