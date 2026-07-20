# Troubleshooting

Worked through in the order you'll hit them. The status page
(`http://server:8080/`) and the activity feed narrate most failures;
hot-folder rejections always leave a `.error.txt` beside the file in the
error folder.

## Server ↔ cutter

**Cutter tile says "Not communicating" / NO LINK**
The server can't reach the cutter on TCP 9100. Check `CUTTER_HOST` /
`CUTTERS`, that the machine is on and on the network (`ping` it), and
that nothing else (Cutting Master, Graphtec Studio) is connected — the
cutter accepts one connection at a time.

**Startup warning: step size mismatch ("jobs would cut at the wrong scale")**
The cutter's GP-GL STEP SIZE differs from `GPGL_STEPS_PER_MM`. Either
change the cutter's setting (factory default 0.1 mm = `10`) or set the
variable to match. Do not ignore this — every dimension scales.

**`/cutter/info` returns 409**
That cutter is mid scan-cycle; extra traffic is restricted by the
protocol. Try again when its state returns to Stopped.

**Status shows Standby with an "Unexpected DLS transition" error**
The server observed a state jump it didn't drive — usually a second
Data Link server is talking to the same cutter. Make sure only one
server installation points at each machine.

## Barcode scanning at the cutter

**"Cannot detect start mark" (immediate)**
The sensor swept blank paper. Load the sheet with the barcode edge
toward the operator and the black Start Mark block on the **right**;
position the tool near the Start Mark before starting; if it still
fails, rotate the sheet 180°.

**Scan error mentioning scan mode / adjust level**
Detection failed optically. Verify the print is 100% scale (Start Mark
must measure 50 × 9 mm), solid black, on matte media. Glossy/laminated
media may need the panel's mark-scan sensitivity adjustment.

**E05021 — incorrect barcode type**
The code decoded but is the wrong format. This server prints the Roll
Media format the Data Link flow expects; this error usually means the
sheet came from an old generator or a third-party tool using the
12-character Standard format. Re-prepare the file with the server.

**Cutter shows the code but reports no jobs (or `barcode.no_match` webhook)**
No stored job matches those 9 characters exactly. Check `GET /jobs` —
the printed code and the job's `barcode_link_info` must be
byte-identical. Re-drop the cut file or re-run print preparation.

## ARMS mark scanning

**E04017 — "moving destination is out of area"**
A mark-seek target fell outside the cut area. Causes: the sheet is
smaller than the page the file was prepared for; the media was loaded
far off-center; or the push rollers are placed so the marks fall outside
the roller span. Prepare the file at the media's true size and load with
the printed margins respected.

**Marks found but the cut is offset from the print**
Verify print scale first (measure the mark rectangle against the job's
TB distances via `GET /jobs/{id}/gpgl`). If scale is right, the printer
itself may be skewing — ARMS corrects skew and scale within limits only.

## File preparation

**400 / error file: "No cut paths found"**
The PDF has no ISO 19593-1 Structural/Cutting layer and no path in a
`CUT_SPOT_COLORS` colorant. In Illustrator, stroke the contour with a
spot swatch named `CutContour`; in pdfToolbox, apply a processing-steps
fixup.

**"Artwork intrudes into reserved zones"**
Content sits in the barcode band (leading ~32 mm strip) or a mark
corner. Re-impose leaving those clear; the message names the offending
object's position in mm.

**"Cut paths extend outside the registration-mark rectangle"**
Cut geometry must stay ≥1 mm inside the marks. Shrink or re-center the
layout.

**"Page is N mm wide; the barcode run needs at least …"**
The page is too small for the barcode + marks. Minimums: ~193 mm wide,
mark rectangle ≥60×60 mm.

**Hot-folder file vanished without output**
Check `errors/<name>.error.txt`, then `processed/` (it may have
succeeded — print outputs land in `outbox/print/`). The status page
header shows the error count.

## API

**401 Unauthorized** — `API_KEY` is set; send `X-API-Key`.
**413** — the file exceeds `MAX_UPLOAD_BYTES`.
**400 on `/dls/...` "Multiple cutters are configured"** — add
`?cutter=<name>`.
