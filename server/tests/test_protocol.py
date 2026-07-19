import socket
import time

import pytest

from app.protocol import (
    DataLinkClient,
    ResponseError,
    SendError,
    clean_response,
    is_allowed_transition,
    parse_int_response,
    sanitize_job_name,
    validate_job_name,
)
from tests.fake_cutter import FakeCutter


def make_client(port: int, **overrides) -> DataLinkClient:
    values = dict(
        host="127.0.0.1",
        port=port,
        timeout_seconds=1.0,
        retry_total_ms=200,
        retry_interval_ms=50,
    )
    values.update(overrides)
    return DataLinkClient(**values)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# -- pure helpers ----------------------------------------------------------


def test_clean_response_trims_etx_and_space() -> None:
    raw = "  2\x03\r\n"
    assert clean_response(raw) == "2"


def test_parse_int_response_handles_empty() -> None:
    assert parse_int_response("\x03") is None


def test_allowed_transition_known_value() -> None:
    assert is_allowed_transition(1, 2) is True
    assert is_allowed_transition(5, 2) is False


def test_allowed_transition_unknown_future_status_is_ignored() -> None:
    assert is_allowed_transition(0, 99) is True


def test_allowed_transition_esc_key_cancel_paths() -> None:
    # Rev 1.02: the panel ESC key cancels from Requesting/Selecting back
    # to Stopped; these must not be flagged as another server's traffic.
    assert is_allowed_transition(2, 0) is True
    assert is_allowed_transition(3, 0) is True


def test_validate_job_name_rules() -> None:
    assert validate_job_name("front-label-001") == "front-label-001"
    with pytest.raises(ValueError):
        validate_job_name("")
    with pytest.raises(ValueError):
        validate_job_name("x" * 26)
    with pytest.raises(ValueError):
        validate_job_name("bad\x1ename")  # RS would split the ESC.d3 framing
    with pytest.raises(ValueError):
        validate_job_name("café")  # non-ASCII


def test_sanitize_job_name() -> None:
    assert sanitize_job_name("bad\x1ename") == "badname"
    assert sanitize_job_name("x" * 40) == "x" * 25
    assert sanitize_job_name("\x02\x03", fallback="fallback") == "fallback"


# -- wire behavior against the fake cutter ---------------------------------


def test_get_status_round_trip() -> None:
    with FakeCutter() as cutter:
        cutter.status = 5
        client = make_client(cutter.port)
        assert client.get_data_link_status() == 5


def test_each_command_uses_a_fresh_connection() -> None:
    with FakeCutter() as cutter:
        client = make_client(cutter.port)
        client.get_data_link_status()
        client.get_barcode_link_info()
        client.send_job_list([])
        assert cutter.connection_count == 3


def test_job_list_framing_and_name_validation() -> None:
    with FakeCutter() as cutter:
        client = make_client(cutter.port)
        client.send_job_list(["JOB-A", "JOB-B"])
        assert cutter.received_job_lists == [["JOB-A", "JOB-B"]]

        client.send_job_list([])
        assert cutter.received_job_lists[-1] == []

        with pytest.raises(ValueError):
            client.send_job_list(["ok", "bad\x1ename"])
        with pytest.raises(ValueError):
            client.send_job_list(["x"] * 9)


def test_missing_etx_raises_response_error_without_retry() -> None:
    with FakeCutter() as cutter:
        cutter.omit_etx = True
        client = make_client(cutter.port)
        with pytest.raises(ResponseError):
            client.get_data_link_status()
        # Response-phase failures must not be blindly retried: a retry
        # would re-send non-idempotent commands like ESC.d5/d6.
        assert cutter.connection_count == 1


def test_truncated_reply_is_not_mistaken_for_no_selection() -> None:
    with FakeCutter() as cutter:
        cutter.close_without_reply = True
        client = make_client(cutter.port)
        # Old behavior parsed the empty read as "no job selected" (-1) and
        # silently abandoned a job the user had selected.
        with pytest.raises(ResponseError):
            client.get_selected_job_index()


def test_empty_d4_reply_means_no_selection() -> None:
    with FakeCutter() as cutter:
        cutter.selected_reply = ""
        client = make_client(cutter.port)
        assert client.get_selected_job_index() is None


def test_send_retry_uses_cumulative_wait_budget() -> None:
    port = free_port()  # nothing listening: connect fails instantly
    client = make_client(port, retry_total_ms=200, retry_interval_ms=50)
    started = time.monotonic()
    with pytest.raises(SendError):
        client.get_data_link_status()
    elapsed_ms = (time.monotonic() - started) * 1000.0
    # Instant failures must still consume the whole 200 ms wait budget
    # (~5 attempts), not exit after a single attempt.
    assert elapsed_ms >= 200.0


def test_regmark_and_command_type_syntax() -> None:
    with FakeCutter() as cutter:
        client = make_client(cutter.port)
        client.set_regmark_position(10, 20, 30, 40)
        client.set_command_type(0)
        assert cutter.received_regmarks == [b"\x1b.d5;0;10;20;30;40:"]
        assert cutter.received_command_types == [b"\x1b.d6;0:"]


def test_command_sequence_sent_verbatim_with_no_response() -> None:
    with FakeCutter() as cutter:
        client = make_client(cutter.port)
        payload = b"J1\x03M0,0\x03D10,10\x03M0,0\x03"
        client.send_command_sequence(payload)
        cutter.wait_for(lambda: len(cutter.received_sequences) == 1)
        assert cutter.received_sequences == [payload]
