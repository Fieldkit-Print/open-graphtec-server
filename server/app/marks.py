"""Barcode + registration-mark layout and overlay generation.

Implements the Roll Media Barcode layout from Graphtec's Barcode Print
Layout Specification Rev 1.10, using the geometry validated on FC9000-140
hardware (2026-07-19):

- barcode band 20 mm in from the leading edge, Start Mark on the RIGHT
  (operator view), bars scanned right-to-left
- ESC.d5 vector anchored at the Start Mark's scan-start corner, fy
  positive along the scan direction
- Type 2 registration marks, 20 mm arms, kept >= 50 mm from sheet edges

All layout values are millimetres in PAGE coordinates (origin bottom-left,
y up, page bottom = leading edge into the cutter).
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from reportlab.lib.colors import CMYKColor
from reportlab.pdfgen import canvas

from .protocol import ETX

PT_PER_MM = 72 / 25.4

CODE39_VALUES = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-. $/+%"
CODE39_PATTERNS = {
    "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn",
    "4": "nnnwwnnnw", "5": "wnnwwnnnn", "6": "nnwwwnnnn", "7": "nnnwnnwnw",
    "8": "wnnwnnwnn", "9": "nnwwnnwnn", "A": "wnnnnwnnw", "B": "nnwnnwnnw",
    "C": "wnwnnwnnn", "D": "nnnnwwnnw", "E": "wnnnwwnnn", "F": "nnwnwwnnn",
    "G": "nnnnnwwnw", "H": "wnnnnwwnn", "I": "nnwnnwwnn", "J": "nnnnwwwnn",
    "K": "wnnnnnnww", "L": "nnwnnnnww", "M": "wnwnnnnwn", "N": "nnnnwnnww",
    "O": "wnnnwnnwn", "P": "nnwnwnnwn", "Q": "nnnnnnwww", "R": "wnnnnnwwn",
    "S": "nnwnnnwwn", "T": "nnnnwnwwn", "U": "wwnnnnnnw", "V": "nwwnnnnnw",
    "W": "wwwnnnnnn", "X": "nwnnwnnnw", "Y": "wwnnwnnnn", "Z": "nwwnwnnnn",
    "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnwnn", "*": "nwnnwnwnn",
    "$": "nwnwnwnnn", "/": "nwnwnnnwn", "+": "nwnnnwnwn", "%": "nnnwnwnwn",
}

NARROW = 0.5
WIDE = NARROW * 2.5
QUIET = 7.0
SIDE_MARGIN = 10.0
FRONT_MARGIN = 20.0
BAND_H = 9.0
START_MARK_LEN = 50.0
MARK_ARM = 20.0
MARK_THICK = 0.7
MARK_SIDE_MARGIN = 50.0
REAR_MARGIN = 50.0
BARCODE_MARGIN = 6.0


def code39_check_digit(data: str) -> str:
    return CODE39_VALUES[sum(CODE39_VALUES.index(ch) for ch in data) % 43]


def barcode_string(link_info: str, position_info: str = "F") -> str:
    data = link_info + position_info
    return f"*{data}{code39_check_digit(data)}*"


def barcode_elements(text: str) -> list[tuple[float, bool]]:
    """(width_mm, is_bar) sequence for a CODE39 string."""
    elements: list[tuple[float, bool]] = []
    for index, ch in enumerate(text):
        if index:
            elements.append((NARROW, False))
        for pos, kind in enumerate(CODE39_PATTERNS[ch]):
            elements.append((WIDE if kind == "w" else NARROW, pos % 2 == 0))
    return elements


@dataclass(frozen=True)
class MarkLayout:
    """Complete layout for one page, all values in mm (page coords)."""

    page_w: float
    page_h: float
    link_info: str
    # Barcode band
    band_y0: float
    band_y1: float
    start_mark_x0: float
    start_mark_x1: float
    # Registration marks: mark 1 = nearest the Start Mark (right/front)
    mark1_x: float
    mark1_y: float
    lx: float          # feed-axis distance between marks
    ly: float          # width-axis distance between marks
    # ESC.d5 vector, 0.1 mm units
    regmark_fx: int
    regmark_fy: int


def compute_layout(page_w: float, page_h: float, link_info: str) -> MarkLayout:
    """Lay out barcode + marks for a page of page_w x page_h mm."""
    bars_len = sum(w for w, _ in barcode_elements(barcode_string(link_info)))
    barcode_run = START_MARK_LEN + QUIET + bars_len + QUIET
    min_w = SIDE_MARGIN + barcode_run + 25.0  # + human readable
    if page_w < min_w:
        raise ValueError(
            f"Page is {page_w:.0f} mm wide; the barcode run needs at least "
            f"{min_w:.0f} mm."
        )
    mark1_y = FRONT_MARGIN + BAND_H + BARCODE_MARGIN
    lx = (page_h - REAR_MARGIN) - mark1_y
    ly = page_w - 2 * MARK_SIDE_MARGIN
    if lx < 60.0 or ly < 60.0:
        raise ValueError(
            f"Page {page_w:.0f}x{page_h:.0f} mm leaves a mark rectangle of "
            f"{ly:.0f}x{lx:.0f} mm; at least 60x60 mm is required."
        )

    start_mark_x1 = page_w - SIDE_MARGIN
    start_mark_x0 = start_mark_x1 - START_MARK_LEN
    mark1_x = page_w - MARK_SIDE_MARGIN
    return MarkLayout(
        page_w=page_w,
        page_h=page_h,
        link_info=link_info,
        band_y0=FRONT_MARGIN,
        band_y1=FRONT_MARGIN + BAND_H,
        start_mark_x0=start_mark_x0,
        start_mark_x1=start_mark_x1,
        mark1_x=mark1_x,
        mark1_y=mark1_y,
        lx=lx,
        ly=ly,
        # Validated on hardware: anchor = scan-start corner of the Start
        # Mark (outer end, interior side of the band); fy along the scan.
        regmark_fx=round((mark1_y - (FRONT_MARGIN + BAND_H)) * 10),
        regmark_fy=round((start_mark_x1 - mark1_x) * 10),
    )


def keep_clear_zones(layout: MarkLayout) -> list[tuple[float, float, float, float]]:
    """(x0, y0, x1, y1) mm rectangles that must contain no artwork."""
    zones = [
        # Leading band strip: barcode + quiet space above it
        (0.0, 0.0, layout.page_w, layout.band_y1 + 3.0),
    ]
    pad = MARK_ARM + 6.0
    for mx in (layout.mark1_x, layout.mark1_x - layout.ly):
        for my in (layout.mark1_y, layout.mark1_y + layout.lx):
            zones.append((mx - pad, my - pad, mx + pad, my + pad))
    return zones


def page_to_mark_frame(layout: MarkLayout, x_mm: float, y_mm: float) -> tuple[float, float]:
    """Page mm -> mark frame mm (origin mark 1, X = feed, Y = scan dir)."""
    return (y_mm - layout.mark1_y, layout.mark1_x - x_mm)


def build_overlay_pdf(layout: MarkLayout) -> bytes:
    """A one-page PDF (same size) carrying only marks + barcode, drawn in
    100% K device CMYK for sensor contrast."""
    buffer = io.BytesIO()
    c = canvas.Canvas(
        buffer, pagesize=(layout.page_w * PT_PER_MM, layout.page_h * PT_PER_MM)
    )
    black = CMYKColor(0, 0, 0, 1)
    c.setFillColor(black)
    c.setStrokeColor(black)
    mm = PT_PER_MM

    def rect(x, y, w, h):
        c.rect(x * mm, y * mm, w * mm, h * mm, stroke=0, fill=1)

    # Start Mark + bars (right-to-left scan)
    rect(layout.start_mark_x0, layout.band_y0,
         START_MARK_LEN, BAND_H)
    x = layout.start_mark_x0 - QUIET
    for width, is_bar in barcode_elements(barcode_string(layout.link_info)):
        x -= width
        if is_bar:
            rect(x, layout.band_y0, width, BAND_H)
    # Human readable beyond the far quiet zone
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString((x - QUIET) * mm, (layout.band_y0 + 2.0) * mm,
                      f"{layout.link_info}-F")

    # Type 2 registration marks, arms inward along the rectangle edges
    c.setLineWidth(MARK_THICK * mm)
    x1, y1 = layout.mark1_x, layout.mark1_y
    corners = [
        (x1, y1, -1, +1),
        (x1 - layout.ly, y1, +1, +1),
        (x1, y1 + layout.lx, -1, -1),
        (x1 - layout.ly, y1 + layout.lx, +1, -1),
    ]
    for cx, cy, sx, sy in corners:
        c.line(cx * mm, cy * mm, (cx + sx * MARK_ARM) * mm, cy * mm)
        c.line(cx * mm, cy * mm, cx * mm, (cy + sy * MARK_ARM) * mm)

    c.showPage()
    c.save()
    return buffer.getvalue()


def build_gpgl_job(
    layout: MarkLayout,
    segments_mm: list[tuple[float, float, float, float]],
    *,
    steps_per_mm: int,
) -> bytes:
    """TB-prefixed GP-GL job from cut segments in the MARK frame (mm)."""
    from .converters.pdf_to_gpgl import _build_gpgl_command_from_segments

    int_segments: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for x0, y0, x1, y1 in segments_mm:
        seg = (
            round(x0 * steps_per_mm), round(y0 * steps_per_mm),
            round(x1 * steps_per_mm), round(y1 * steps_per_mm),
        )
        if (seg[0], seg[1]) == (seg[2], seg[3]):
            continue
        key = min(seg, (seg[2], seg[3], seg[0], seg[1]))
        if key in seen:
            continue
        seen.add(key)
        int_segments.append(seg)
    if not int_segments:
        raise ValueError("No usable cut segments after conversion.")

    drawing = _build_gpgl_command_from_segments(int_segments)
    prefix = (
        f"TB99{ETX}"
        f"TB51,{int(MARK_ARM * steps_per_mm)}{ETX}"
        f"TB52,2{ETX}"
        f"TB54,0,0{ETX}"
        f"TB55,1{ETX}"
        f"TB24,{round(layout.lx * steps_per_mm)},{round(layout.ly * steps_per_mm)}{ETX}"
        f"TB99{ETX}"
    ).encode("ascii")
    return prefix + drawing + f"TB0{ETX}".encode("ascii")
