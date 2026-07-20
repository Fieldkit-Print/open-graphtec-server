# Print preparation

The server turns a pre-imposed print PDF into two artifacts from **one
layout computation**:

1. the same PDF with the Graphtec barcode and registration marks
   overlaid (the file you print), and
2. a registered cut job carrying the matching geometry (the job the
   cutter receives when it scans that barcode).

Because both come from the same computation, the printed marks and the
stored registration vector can never drift apart.

## What you send

A **single-page, pre-imposed** print PDF. The server deliberately does
not impose or step-and-repeat — the page you send is the page that
prints. Cut paths must be identified one of two ways:

### Preferred: ISO 19593-1 processing steps

Cut content on a PDF layer (Optional Content Group) whose dictionary
carries:

- `GTS_ProcStepsGroup` = `Structural`
- `GTS_ProcStepsType` = `Cutting` (through-cut) or `PartialCutting`
  (kiss-cut)

The metadata is honored directly on the OCG, nested under
`GTS_Metadata`, and through OCMD membership dictionaries. This is the
modern standard for finishing data; callas pdfToolbox can tag files this
way (and convert legacy spot-color files into it), so a pdfToolbox
preflight upstream pairs naturally with this server.

### Fallback: named spot color

Paths stroked or filled in a Separation (or DeviceN) color space whose
colorant matches `CUT_SPOT_COLORS` (default `CutContour`,
case-insensitive). Files using this path succeed with a warning
recommending processing steps.

## What the server guarantees

- **Integrity** — the original page objects are never re-rendered or
  rewritten. Marks and barcode are added as an overlay content stream;
  fonts, color, transparency pass through untouched. Cut-contour paths
  stay in the output (RIPs are conventionally configured not to image
  them).
- **Sensor-grade marks** — the overlay is drawn in 100% K device CMYK.
- **Zone validation** — the barcode band at the leading edge and the
  four mark corners must be free of artwork. Violations are rejected
  with the object position in the message. Cut geometry must sit at
  least 1 mm inside the mark rectangle.
- **Minimum page** — the barcode run needs ~193 mm of width and the mark
  rectangle at least 60×60 mm; smaller pages are rejected with the
  required dimensions.

## Barcode assignment

- Omit the barcode and the server mints one: `BARCODE_PREFIX` (default
  `F`) + a base36 counter — unique for the life of the database.
- Pass `barcode_link_info` explicitly (API) or put a 9-character token in
  the filename (hot folder) to control it — e.g. derive it from your
  order number so print, cut, and MIS all agree.

## Delivery

- **API**: `POST /print/prepare` returns the marked PDF; job id and
  barcode in headers. See [api.md](api.md).
- **Hot folder**: drop into `inbox/print/`; collect
  `<name>_<barcode>.pdf` from `outbox/print/`; failures land in the
  error folder with a `.error.txt`.

## Printing the result

Print at **100% scale** on the media the cutter will load. The layout
places the barcode along the leading edge (Start Mark on the right as
the operator faces the machine) and expects that edge to enter the
cutter first. See [barcode-and-marks.md](barcode-and-marks.md) for the
exact geometry and loading orientation.
