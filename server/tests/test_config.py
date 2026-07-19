import pytest

from app.config import CutterConfig, _parse_cutters


def test_parse_single_and_multiple() -> None:
    assert _parse_cutters("main=192.168.1.104") == (
        CutterConfig("main", "192.168.1.104", 9100),
    )
    assert _parse_cutters("left=10.0.0.1:9100, right=10.0.0.2:9200") == (
        CutterConfig("left", "10.0.0.1", 9100),
        CutterConfig("right", "10.0.0.2", 9200),
    )


def test_parse_rejects_bad_entries() -> None:
    with pytest.raises(ValueError):
        _parse_cutters("just-a-host")
    with pytest.raises(ValueError):
        _parse_cutters("a=1.2.3.4,a=5.6.7.8")  # duplicate names
    with pytest.raises(ValueError):
        _parse_cutters("bad name=1.2.3.4")
    with pytest.raises(ValueError):
        _parse_cutters("  ,  ")
