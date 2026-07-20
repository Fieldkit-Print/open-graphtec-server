# Barcodes & registration marks

This page describes the printed geometry the server generates and how
job matching works. The layout was validated end-to-end on an FC9000-140
(barcode scan → job handoff → 4-point ARMS registration → cut).

## The barcode link info

Jobs and printed sheets are matched by a **9-character code** using
`0-9 A-Z`. Rules:

- exactly 9 characters;
- the first character must **not** be `G` (reserved by Graphtec's own
  software) — the server's default prefix is `F`;
- the printed code and the stored job code must be byte-identical —
  that equality *is* the matching mechanism;
- one code should map to variants of the *same* design only (a recut or
  force-adjusted version), never to different designs.

On media the code is wrapped as a CODE39 **Roll Media Barcode** string:
`*` + link info + position char (`F` = front edge) + mod-43 check digit
+ `*`. The cutter strips the wrapper and reports only the 9 characters
when it asks the server for jobs. The human-readable text printed beside
the bars shows `<link info>-F`.

## Printed layout (per page)

All positions derive from the page size; distances in mm:

- **Barcode band**: 9 mm tall, its bottom **20 mm from the leading
  edge**. The solid black **Start Mark** (50 mm long) sits at the right
  end (10 mm side margin); bars run right-to-left; narrow bar 0.5 mm,
  wide 2.5×; 7 mm quiet zones.
- **Registration marks**: Type 2 (arms inward), 20 mm arms, 0.7 mm line,
  at the corners of a rectangle **50 mm in from each side edge**; the
  front pair 6 mm behind the barcode band, the rear pair 50 mm from the
  trailing edge.
- **Keep-clear zones**: the full-width leading strip through the barcode
  band (+3 mm) and a square around each mark corner (arm + 6 mm).
  Artwork or cut paths inside these are rejected at prepare time.

## Loading orientation

Load the printed sheet **face up, barcode edge toward the operator, the
black Start Mark block on the right** as you face the machine. Position
the tool near the Start Mark if the panel asks, then start the barcode
scan. If the start mark is not detected, the two most likely causes are
orientation (rotate the sheet 180°) and print scaling (measure: the
Start Mark must be exactly 50 × 9 mm).

## How the cutter finds the marks

The job carries a **registration vector** (`regmark_fx/fy`, 0.1 mm
units): the offset from the Start Mark's scan-start corner to the first
registration mark. After reading the barcode, the cutter applies this
vector to find mark 1, then walks the mark rectangle using the
distances embedded in the job's TB commands, measuring all four marks
to correct position, rotation, and scale before cutting. Cut coordinates
are expressed in the mark frame (origin at mark 1), so registration
accuracy is independent of where the sheet sits in the machine.

## Hardware-validated facts (FC9000-140, firmware V1.39)

These were established empirically and are encoded in the server:

- The Data Link barcode flow expects the **Roll Media** format;
  Standard-format (12-char) barcodes are rejected with error E05021.
- The sensor cannot reach a barcode band only 10 mm from the leading
  edge; 20 mm scans reliably.
- The registration vector is anchored at the Start Mark's **scan-start
  corner**, with the width component positive **along the scan
  direction**.
- Marks (including their arms and the sensor's search window) must stay
  roughly 50 mm clear of sheet edges, or the cutter reports E04017
  ("moving destination is out of area").
- The machine refuses a second TCP connection outright while one is
  open (rather than replying "Already connected") — the server's
  send-retry loop absorbs this.
