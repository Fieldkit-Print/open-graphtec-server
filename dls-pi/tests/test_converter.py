import pytest

from app.converters.pdf_to_gpgl import convert_pdf_to_gpgl
from tests.conftest import build_pdf


ETX = b"\x03"


def steps(value_in_points: float, steps_per_mm: int = 10) -> int:
    return int(round(value_in_points * 25.4 / 72.0 * steps_per_mm))


def commands_of(sequence: bytes) -> list[bytes]:
    parts = sequence.split(ETX)
    assert parts[-1] == b"", "sequence must end with an ETX terminator"
    return [part for part in parts[:-1]]


def points_of(command: bytes) -> list[tuple[int, int]]:
    coords = [int(v) for v in command[1:].split(b",") if v]
    assert len(coords) % 2 == 0
    return list(zip(coords[0::2], coords[1::2]))


def all_draw_points(sequence: bytes) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for command in commands_of(sequence):
        if command.startswith(b"D") or command.startswith(b"M"):
            result.extend(points_of(command))
    return result


def test_negative_slope_line_is_not_mirrored() -> None:
    # A line from (10, 250) down to (110, 150): the pdfplumber bbox
    # (x0,y0,x1,y1) would describe the OPPOSITE diagonal.
    pdf = build_pdf("10 250 m 110 150 l S", height=300)
    sequence, details = convert_pdf_to_gpgl(pdf)
    commands = commands_of(sequence)

    move = next(c for c in commands if c.startswith(b"M"))
    draw = commands[commands.index(move) + 1]
    endpoints = points_of(move) + points_of(draw)
    assert endpoints == [
        (steps(10), steps(250)),
        (steps(110), steps(150)),
    ]
    assert details["line_objects"] == 1


def test_polyline_path_y_axis_is_flipped_to_pdf_space() -> None:
    # An m/l/l "V" path surfaces as a pdfplumber curve whose coordinates
    # are top-based; unflipped, the V would render upside down.
    pdf = build_pdf("20 200 m 60 100 l 100 200 l S", height=300)
    sequence, details = convert_pdf_to_gpgl(pdf)
    drawn = all_draw_points(sequence)

    expected = {
        (steps(20), steps(200)),
        (steps(60), steps(100)),
        (steps(100), steps(200)),
    }
    assert expected.issubset(set(drawn))
    # The un-flipped (top-based) reading of the same path.
    wrong = (steps(60), steps(200))
    assert wrong not in set(drawn)
    assert details["curve_objects"] == 1


def test_bezier_flattens_within_page_region() -> None:
    # Arch from (0,0) to (90,0) bulging to y≈67pt. If curve coordinates
    # were left top-based on a 300pt page, y values would sit near 300pt.
    pdf = build_pdf("0 0 m 30 90 60 90 90 0 c S", height=300)
    sequence, _ = convert_pdf_to_gpgl(pdf)
    drawn = all_draw_points(sequence)

    max_y = max(y for _, y in drawn)
    assert steps(40) < max_y < steps(90)
    assert (0, 0) in drawn
    # Endpoint (90pt, 0): allow 1 step of float rounding slack on x.
    assert any(
        abs(x - steps(90)) <= 1 and y == 0 for x, y in drawn
    ), f"missing bezier endpoint in {drawn}"
    assert len(drawn) > 4, "bezier should flatten into multiple segments"


def test_rect_outline_preserved() -> None:
    pdf = build_pdf("50 50 100 80 re S", height=300)
    sequence, details = convert_pdf_to_gpgl(pdf)
    drawn = set(all_draw_points(sequence))
    corners = {
        (steps(50), steps(50)),
        (steps(150), steps(50)),
        (steps(150), steps(130)),
        (steps(50), steps(130)),
    }
    assert corners.issubset(drawn)
    assert details["rect_objects"] == 1


def test_every_command_is_terminated_and_job_ends_tool_up() -> None:
    pdf = build_pdf("10 10 m 100 100 l S 50 50 150 100 re S", height=300)
    sequence, _ = convert_pdf_to_gpgl(pdf)

    commands = commands_of(sequence)
    assert all(c[:1] in (b"J", b"M", b"D") for c in commands)
    # Ends with a pen-up move to origin so the blade is not left dragging.
    assert commands[-1] == b"M0,0"
    assert sequence.endswith(b"M0,0" + ETX)
    assert commands[0] == b"J1"


def test_step_size_scaling() -> None:
    pdf = build_pdf("0 0 m 72 0 l S", height=300)  # 72pt = 25.4mm wide
    seq_10, _ = convert_pdf_to_gpgl(pdf, steps_per_mm=10)
    seq_40, _ = convert_pdf_to_gpgl(pdf, steps_per_mm=40)

    assert (254, 0) in all_draw_points(seq_10)
    assert (1016, 0) in all_draw_points(seq_40)

    with pytest.raises(ValueError):
        convert_pdf_to_gpgl(pdf, steps_per_mm=0)


def test_rejects_pdf_without_vectors() -> None:
    pdf = build_pdf("BT ET", height=300)
    with pytest.raises(ValueError):
        convert_pdf_to_gpgl(pdf)
