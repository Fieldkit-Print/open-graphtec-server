#!/usr/bin/env python3
"""Roll Media Barcode test sheet for the FC9000 (Continuous Operation format).

Rev 2 after first hardware attempt: Start Mark moved to the RIGHT side of
the sheet (where the FC9000's sensor hunts), and the barcode band pulled
20 mm in from the leading edge so it clears the unreachable front margin.
The bar sequence is mirrored accordingly: the scan runs right-to-left as
you look at the printed page, start code nearest the Start Mark.

Spec basis: Barcode Print Layout Specification Rev 1.10 ch.4 (front-edge
Roll Media Barcode: Start Mark 9 mm feed x 50 mm width, quiet zones 7 mm,
CODE39 string '*' + link info + 'F' + mod-43 check + '*'; text area
trimmed to fit sheet width per the spec's note). Reg marks: Type 2, 20 mm,
4-point, 45 mm side margins, 6 mm barcode margin.

Outputs: barcode_cut_test_sheet_roll.pdf, test_job_roll.gpgl,
import_test_job_roll.sh
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT_DIR = Path(__file__).resolve().parent

LINK_INFO = "A0200ROLL"
POSITION_INFO = "F"

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

DATA_CHARS = LINK_INFO + POSITION_INFO
CHECK_DIGIT = CODE39_VALUES[
    sum(CODE39_VALUES.index(ch) for ch in DATA_CHARS) % 43
]
BARCODE_STRING = f"*{DATA_CHARS}{CHECK_DIGIT}*"

# --- layout (mm, page coords; page bottom = leading/front edge) -----------
PAGE_W, PAGE_H = 215.9, 279.4
SIDE_MARGIN = 10.0
FRONT_MARGIN = 20.0               # leading-edge margin (was 10; the sensor
                                  # could not reach the band at 10 mm)

NARROW = 0.5
WIDE = NARROW * 2.5
QUIET = 7.0

BAND_Y0 = FRONT_MARGIN            # 9 mm barcode band in feed direction
BAND_Y1 = FRONT_MARGIN + 9.0
START_MARK_X1 = PAGE_W - SIDE_MARGIN        # right edge of Start Mark
START_MARK_LEN = 50.0
START_MARK_X0 = START_MARK_X1 - START_MARK_LEN
BARS_X_START = START_MARK_X0 - QUIET        # bars grow leftward from here

MARK_ARM = 20.0
MARK_THICK = 0.7
MARK_SIDE_MARGIN = 50.0
MARK1_X = PAGE_W - MARK_SIDE_MARGIN         # mark 1 = right side (nearest
MARK1_Y = BAND_Y1 + 6.0                     # Start Mark), 6 mm barcode margin
LY = 116.0                                  # marks at x 165.9 and 49.9
LX = 160.0

# d5 vector — VALIDATED on FC9000-140 hardware (2026-07-19): the anchor is
# the Start Mark corner at the SCAN-START end (outer end of the block, at
# the media side edge) on the interior side of the band; fy is measured
# along the scan direction and is positive toward the far side.
D5_FX_MM = MARK1_Y - BAND_Y1                          # 6 mm
D5_FY_MM = START_MARK_X1 - MARK1_X                    # +40 mm


def barcode_elements():
    elements = []
    for index, ch in enumerate(BARCODE_STRING):
        if index:
            elements.append((NARROW, False))
        for pos, kind in enumerate(CODE39_PATTERNS[ch]):
            elements.append((WIDE if kind == "w" else NARROW, pos % 2 == 0))
    return elements


def draw_sheet(path: Path) -> float:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFillColorRGB(0, 0, 0)

    def rect(x, y, w, h):
        c.rect(x * mm, y * mm, w * mm, h * mm, stroke=0, fill=1)

    # Start Mark on the right, touching side print margin
    rect(START_MARK_X0, BAND_Y0, START_MARK_LEN, BAND_Y1 - BAND_Y0)

    # Bars run right -> left (mirrored scan direction)
    x = BARS_X_START
    for width, is_bar in barcode_elements():
        x -= width
        if is_bar:
            rect(x, BAND_Y0, width, BAND_Y1 - BAND_Y0)
    bars_end = x

    # Human readable beyond the far quiet zone
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString((bars_end - QUIET) * mm, (BAND_Y0 + 2.0) * mm,
                      f"{LINK_INFO}-{POSITION_INFO}")

    # Reg marks (Type 2, arms inward)
    c.setLineWidth(MARK_THICK * mm)
    x1, y1 = MARK1_X, MARK1_Y
    corners = [
        (x1, y1, -1, +1),                 # mark 1: right/front, arms up+left
        (x1 - LY, y1, +1, +1),
        (x1, y1 + LX, -1, -1),
        (x1 - LY, y1 + LX, +1, -1),
    ]
    for cx, cy, sx, sy in corners:
        c.line(cx * mm, cy * mm, (cx + sx * MARK_ARM) * mm, cy * mm)
        c.line(cx * mm, cy * mm, cx * mm, (cy + sy * MARK_ARM) * mm)

    # Printed test shapes. Mark frame: origin mark 1, X = feed (page up),
    # Y = scan direction (page LEFT on this mirrored sheet).
    c.setLineWidth(0.4 * mm)

    def to_page(mx, my):
        return (x1 - my, y1 + mx)

    sq = [to_page(30, 15), to_page(30, 55), to_page(70, 55), to_page(70, 15)]
    p = c.beginPath()
    p.moveTo(sq[0][0] * mm, sq[0][1] * mm)
    for px, py in sq[1:]:
        p.lineTo(px * mm, py * mm)
    p.close()
    c.drawPath(p)

    tri = [to_page(30, 65), to_page(30, 105), to_page(70, 65)]
    p = c.beginPath()
    p.moveTo(tri[0][0] * mm, tri[0][1] * mm)
    for px, py in tri[1:]:
        p.lineTo(px * mm, py * mm)
    p.close()
    c.drawPath(p)

    ccx, ccy = to_page(115, 58)
    c.circle(ccx * mm, ccy * mm, 15 * mm, stroke=1, fill=0)

    # Labels
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(108 * mm, 262 * mm,
                        "Open Graphtec Server - Roll Media Barcode Test v3")
    c.setFont("Helvetica", 8)
    c.drawCentredString(108 * mm, 256 * mm,
                        f"Link info {LINK_INFO} ({POSITION_INFO}-edge)"
                        f" | check digit {CHECK_DIGIT} | marks: Type 2,"
                        f" 20 mm, 4-point, X {LX:.0f} x Y {LY:.0f} mm")
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.35, 0.35, 0.35)
    for i, line in enumerate([
        "LOAD: barcode edge toward the OPERATOR (front), printed side up;",
        "black Start Mark on the RIGHT. Print at 100% scale.",
        "Cut should trace the printed outlines.",
    ]):
        c.drawString(85 * mm, (218 - 5 * i) * mm, line)

    c.showPage()
    c.save()
    return bars_end


def build_gpgl_job() -> bytes:
    ETX = "\x03"

    def circle_points(cx, cy, r, segments=36):
        from math import cos, pi, sin
        return [
            (round(cx + r * cos(2 * pi * i / segments)),
             round(cy + r * sin(2 * pi * i / segments)))
            for i in range(segments + 1)
        ]

    parts = [
        f"TB99{ETX}",
        f"TB51,{int(MARK_ARM * 10)}{ETX}",
        f"TB52,2{ETX}",
        f"TB54,0,0{ETX}",
        f"TB55,1{ETX}",
        f"TB24,{int(LX * 10)},{int(LY * 10)}{ETX}",
        f"TB99{ETX}",
        f"J1{ETX}",
        f"M300,150{ETX}D700,150,700,550,300,550,300,150{ETX}",
        f"M300,650{ETX}D300,1050,700,650,300,650{ETX}",
    ]
    pts = circle_points(1150, 580, 150)
    parts.append(f"M{pts[0][0]},{pts[0][1]}{ETX}")
    parts.append("D" + ",".join(f"{x},{y}" for x, y in pts[1:]) + ETX)
    parts.append(f"M0,0{ETX}")
    parts.append(f"TB0{ETX}")
    return "".join(parts).encode("ascii")


def main() -> None:
    pdf_path = OUT_DIR / "barcode_cut_test_sheet_roll.pdf"
    bars_end = draw_sheet(pdf_path)

    job = build_gpgl_job()
    (OUT_DIR / "test_job_roll.gpgl").write_bytes(job)

    def payload(name, fy_mm):
        return {
            "name": name,
            "barcode_link_info": LINK_INFO,
            "command_type": 0,
            "regmark_fx": int(round(D5_FX_MM * 10)),
            "regmark_fy": int(round(fy_mm * 10)),
            "regmark_rx": 0,
            "regmark_ry": 0,
            "command_sequence": base64.b64encode(job).decode("ascii"),
            "command_sequence_encoding": "base64",
            "append_etx": False,
        }

    jobs = [payload("roll-barcode-test", D5_FY_MM)]
    script = "#!/bin/sh\n# Import the roll media barcode test job.\nSERVER=\"${1:-http://localhost:8080}\"\n"
    for p in jobs:
        script += ("curl -sS -X POST \"$SERVER/jobs/import-json\" "
                   "-H 'Content-Type: application/json' -d '"
                   + json.dumps(p) + "'\necho\n")
    script_path = OUT_DIR / "import_test_job_roll.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)

    print(f"barcode string : {BARCODE_STRING}")
    print(f"bars span x    : {bars_end:.2f} .. {BARS_X_START:.1f} mm "
          f"(right-to-left)")
    print(f"band y         : {BAND_Y0:.0f} .. {BAND_Y1:.0f} mm from leading edge")
    print(f"d5 vector      : fx={int(D5_FX_MM*10)} fy={int(D5_FY_MM*10)} "
          f"(0.1 mm, hardware-validated)")
    print(f"wrote          : {pdf_path.name}, test_job_roll.gpgl, "
          f"import_test_job_roll.sh")


if __name__ == "__main__":
    main()
