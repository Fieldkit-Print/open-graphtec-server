#!/usr/bin/env python3
"""Generate the Graphtec barcode cut test sheet + matching Data Link job.

Implements the *Standard Barcode* layout from Graphtec's
"Barcode Print Layout Specification" Rev 1.10 (SDK folder 5) for a
sheet-fed test on an FC9000, plus the GP-GL job (with TB registration-mark
commands per the FC9000 Command Specification) that the Open Graphtec
Server will hand to the cutter when it scans the barcode.

Outputs (same folder):
  barcode_cut_test_sheet.pdf   - print at 100% scale on Letter
  test_job.gpgl                - raw GP-GL command sequence for the job
  import_test_job.sh           - POSTs the job to the running server

Geometry summary (page coords, portrait Letter, mm):
  - Barcode column on the LEFT edge, scanned bottom-to-top (+X = feed).
    Start Mark 5x9 mm (page: 9 wide x 5 tall) at x[18,27] y[30,35].
    CODE39, narrow 0.5 mm, wide 2.5x, quiet zones 7 mm, bars band 9 mm.
  - Reg marks: Type 2, 20 mm arms, 0.7 mm line, 4-point rectangle
    lx=200 mm (feed axis) x ly=150 mm (carriage axis), mark 1 at (45,40).
  - d5 vector (Start Mark top-left -> mark 1, cutter frame): +10, +18 mm.
"""
from __future__ import annotations

import base64
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT_DIR = Path(__file__).resolve().parent

# ----------------------------------------------------------------------------
# Barcode content (XPF File Format Spec 7-5: * + link info + check digit + *)
# ----------------------------------------------------------------------------

LINK_INFO = "A0100ABCD"  # 9 alphanumeric, first char must not be 'G'

CODE39_VALUES = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-. $/+%"

# 9 elements per char (bar,space,bar,... starting and ending with a bar),
# n = narrow, w = wide. Standard CODE39 table.
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


def code39_check_digit(data: str) -> str:
    return CODE39_VALUES[sum(CODE39_VALUES.index(ch) for ch in data) % 43]


CHECK_DIGIT = code39_check_digit(LINK_INFO)
BARCODE_STRING = f"*{LINK_INFO}{CHECK_DIGIT}*"

# ----------------------------------------------------------------------------
# Layout constants (mm, page coordinates: origin bottom-left of portrait page)
# ----------------------------------------------------------------------------

PAGE_W, PAGE_H = 215.9, 279.4  # Letter

NARROW = 0.5
WIDE = NARROW * 2.5           # spec: wide = 2.5 x narrow
QUIET = 7.0                    # spec: always 7 mm
BAR_BAND_X0, BAR_BAND_X1 = 18.0, 27.0   # bars/start-mark band (9 mm wide)
TEXT_BAND_X0 = 10.0                     # human-readable band (8 mm wide)

START_MARK_Y0 = 30.0          # bottom of start mark (5 mm tall on page)
START_MARK_H = 5.0            # spec: 5 mm in scan direction
BARS_Y0 = START_MARK_Y0 + START_MARK_H + QUIET

# Reg marks: Type 2, 4-point rectangle
MARK_ARM = 20.0               # TB51,200 (20 mm)
MARK_THICK = 0.7
MARK1 = (45.0, 40.0)          # page (x, y) of mark-1 vertex (nearest barcode)
LX = 200.0                    # distance between marks along feed axis (page y)
LY = 150.0                    # distance between marks along carriage (page x)

# d5 vector: cutter frame (X = page up, Y = page right), from Start Mark
# "top-left" vertex (= page bottom-right corner of the start mark:
# x=27, y=30) to mark-1 vertex (45, 40).
D5_FX_MM = MARK1[1] - START_MARK_Y0          # +10 mm along feed
D5_FY_MM = MARK1[0] - BAR_BAND_X1            # +18 mm along carriage


def barcode_elements() -> list[tuple[float, bool]]:
    """(width_mm, is_bar) for the full barcode string, start code first."""
    elements: list[tuple[float, bool]] = []
    for index, ch in enumerate(BARCODE_STRING):
        if index:
            elements.append((NARROW, False))  # inter-character gap
        for pos, kind in enumerate(CODE39_PATTERNS[ch]):
            width = WIDE if kind == "w" else NARROW
            elements.append((width, pos % 2 == 0))
    return elements


def draw_sheet(path: Path) -> float:
    c = canvas.Canvas(str(path), pagesize=letter)

    def rect(x_mm, y_mm, w_mm, h_mm, fill=1):
        c.rect(x_mm * mm, y_mm * mm, w_mm * mm, h_mm * mm, stroke=0, fill=fill)

    c.setFillColorRGB(0, 0, 0)

    # --- Start Mark (black, 9 mm across band x 5 mm along scan) ---
    rect(BAR_BAND_X0, START_MARK_Y0, BAR_BAND_X1 - BAR_BAND_X0, START_MARK_H)

    # --- Barcode bars, growing upward from BARS_Y0 (+X scan direction) ---
    y = BARS_Y0
    for width, is_bar in barcode_elements():
        if is_bar:
            rect(BAR_BAND_X0, y, BAR_BAND_X1 - BAR_BAND_X0, width)
        y += width
    bars_top = y

    # --- Human readable (link info only, no asterisks / check digit),
    #     12 pt, rotated to read along the scan direction ---
    c.saveState()
    c.setFont("Helvetica-Bold", 12)
    c.translate((TEXT_BAND_X0 + 6.0) * mm, BARS_Y0 * mm)
    c.rotate(90)
    c.drawString(0, 0, LINK_INFO)
    c.restoreState()

    # --- Reg marks: Type 2, arms along the rectangle edges, inward ---
    c.setLineWidth(MARK_THICK * mm)
    c.setLineCap(0)
    x1, y1 = MARK1
    corners = [
        (x1, y1, +1, +1),                # mark 1: arms up / right
        (x1 + LY, y1, -1, +1),           # mark 2 (+Y): arms up / left
        (x1, y1 + LX, +1, -1),           # mark 3 (+X): arms down / right
        (x1 + LY, y1 + LX, -1, -1),      # mark 4: arms down / left
    ]
    for cx, cy, sx, sy in corners:
        c.line(cx * mm, cy * mm, (cx + sx * MARK_ARM) * mm, cy * mm)
        c.line(cx * mm, cy * mm, cx * mm, (cy + sy * MARK_ARM) * mm)

    # --- Printed test shapes (the cut should trace these outlines) ---
    # Shape coords are defined in the MARK frame (origin mark 1,
    # X = page up, Y = page right); converted here to page coords.
    c.setLineWidth(0.4 * mm)

    def to_page(mx, my):
        return (x1 + my, y1 + mx)

    # Square: X 40..80, Y 30..70
    sq = [to_page(40, 30), to_page(40, 70), to_page(80, 70), to_page(80, 30)]
    p = c.beginPath()
    p.moveTo(sq[0][0] * mm, sq[0][1] * mm)
    for px, py in sq[1:]:
        p.lineTo(px * mm, py * mm)
    p.close()
    c.drawPath(p, stroke=1, fill=0)

    # Right triangle (asymmetry reveals mirror/rotation errors):
    # (40,90) -> (40,130) -> (80,90)
    tri = [to_page(40, 90), to_page(40, 130), to_page(80, 90)]
    p = c.beginPath()
    p.moveTo(tri[0][0] * mm, tri[0][1] * mm)
    for px, py in tri[1:]:
        p.lineTo(px * mm, py * mm)
    p.close()
    c.drawPath(p, stroke=1, fill=0)

    # Circle: center (130, 75) mark frame, r 15
    ccx, ccy = to_page(130, 75)
    c.circle(ccx * mm, ccy * mm, 15 * mm, stroke=1, fill=0)

    # --- Labels (kept >= 15 mm away from mark vertices) ---
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(120 * mm, 262 * mm,
                        "Open Graphtec Server - Barcode Cut Test Sheet")
    c.setFont("Helvetica", 8)
    c.drawCentredString(120 * mm, 256 * mm,
                        f"Link info {LINK_INFO}  |  check digit {CHECK_DIGIT}"
                        f"  |  marks: Type 2, 20 mm, 4-point,"
                        f" X {LX:.0f} mm x Y {LY:.0f} mm")
    c.setFont("Helvetica", 9)
    c.drawString(45 * mm, 14 * mm,
                 "LOAD: top edge of this sheet into the cutter first;"
                 " barcode column on the LEFT as you face the machine.")
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.35, 0.35, 0.35)
    lines = [
        "Cut should trace the printed outlines.",
        "FC9000 panel: ARMS MARK TYPE 2, 4-point.",
        "Print at 100% scale - do NOT fit to page.",
    ]
    for i, line in enumerate(lines):
        c.drawString(95 * mm, (215 - 5 * i) * mm, line)

    c.showPage()
    c.save()
    return bars_top


def build_gpgl_job() -> bytes:
    """GP-GL job in the mark-aligned frame, PU = 0.1 mm.

    Follows the command-sequence pattern from the Barcode Print Layout
    Specification Appendix A, minus the roll-media-only commands
    (TB57/TB59/TB44), using FC9000 Command Specification 3.2.x syntax.
    """
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
        f"TB51,{int(MARK_ARM * 10)}{ETX}",      # mark size 20 mm
        f"TB52,2{ETX}",                          # mark type 2
        f"TB54,0,0{ETX}",                        # no mark offset
        f"TB55,1{ETX}",                          # distance adjustment on
        f"TB24,{int(LX * 10)},{int(LY * 10)}{ETX}",  # scan 4 marks
        f"TB99{ETX}",
        f"J1{ETX}",
        # Square (PU): X 400..800, Y 300..700
        f"M400,300{ETX}D800,300,800,700,400,700,400,300{ETX}",
        # Triangle: (400,900) (400,1300) (800,900)
        f"M400,900{ETX}D400,1300,800,900,400,900{ETX}",
    ]
    pts = circle_points(1300, 750, 150)
    parts.append(f"M{pts[0][0]},{pts[0][1]}{ETX}")
    coords = ",".join(f"{x},{y}" for x, y in pts[1:])
    parts.append(f"D{coords}{ETX}")
    parts.append(f"M0,0{ETX}")
    parts.append(f"TB0{ETX}")
    return "".join(parts).encode("ascii")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = OUT_DIR / "barcode_cut_test_sheet.pdf"
    bars_top = draw_sheet(pdf_path)

    job = build_gpgl_job()
    (OUT_DIR / "test_job.gpgl").write_bytes(job)

    payload = {
        "name": "barcode-cut-test",
        "barcode_link_info": LINK_INFO,
        "command_type": 0,
        "regmark_fx": int(round(D5_FX_MM * 10)),
        "regmark_fy": int(round(D5_FY_MM * 10)),
        "regmark_rx": 0,
        "regmark_ry": 0,
        "command_sequence": base64.b64encode(job).decode("ascii"),
        "command_sequence_encoding": "base64",
        "append_etx": False,
    }
    import json
    script = f"""#!/bin/sh
# Import the barcode cut test job into the Open Graphtec Server.
# Usage: ./import_test_job.sh [server-url]
SERVER="${{1:-http://localhost:8080}}"
curl -sS -X POST "$SERVER/jobs/import-json" \\
  -H 'Content-Type: application/json' \\
  -d '{json.dumps(payload)}'
echo
"""
    script_path = OUT_DIR / "import_test_job.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)

    print(f"barcode string : {BARCODE_STRING}")
    print(f"bars span      : y {BARS_Y0:.1f} .. {bars_top:.1f} mm")
    print(f"d5 vector      : fx={payload['regmark_fx']} fy={payload['regmark_fy']} (0.1 mm)")
    print(f"job bytes      : {len(job)}")
    print(f"wrote          : {pdf_path.name}, test_job.gpgl, import_test_job.sh")


if __name__ == "__main__":
    main()
