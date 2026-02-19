from app.protocol import clean_response, is_allowed_transition, parse_int_response


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

