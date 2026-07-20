# Development

## Setup and tests

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
```

CI (GitHub Actions) runs the suite on Python 3.11 and 3.12.

## The fake cutter

`tests/fake_cutter.py` is a scriptable TCP implementation of the cutter
side of the Data Link protocol — status codes you can flip mid-test,
recorded job lists and command sequences, and fault injection (missing
ETX terminators, closed-before-reply connections, busy ports). Worker
tests drive complete barcode-scan cycles against it, including two fake
cutters at once for the multi-cutter path. If you touch `protocol.py` or
`dls_worker.py`, extend these tests first — they caught on a laptop most
of what would otherwise have been discovered standing at the machine.

## Fixture PDFs

Tests build their PDFs in-memory rather than shipping binaries:

- `tests/conftest.py::build_pdf` — minimal hand-assembled PDFs for
  converter tests (note: content streams must end with a newline or
  pdfminer drops the final operator).
- `tests/test_print_prepare.py` — a reportlab fixture with a real
  `CutContour` Separation, and a pikepdf-built fixture with a genuine
  ISO 19593-1 tagged OCG (when hand-building with pikepdf, dictionary
  keyword keys take no leading slash).

## Test sheets for hardware sessions

`test-sheet/` contains the generators used to validate the geometry on a
real FC9000-140: printable barcode + mark sheets whose matching jobs you
import with the accompanying scripts. Print at 100%, scan, and the
cutter should trace the printed shapes — the fastest way to verify a
geometry change end to end.

## Conventions

- **Geometry lives in `app/marks.py` only.** If a change needs the
  printed layout and the job data to agree, put the computation there
  and consume it from both sides.
- Job names: ≤25 printable ASCII (cutter panel limit) — use
  `protocol.sanitize_job_name` / `validate_job_name`.
- Workers follow the same lifecycle pattern (exception-proof loop,
  `start()`/`stop()` with a lifecycle lock, status snapshot under a
  state lock); copy an existing worker when adding one.
- The status UI is one static file (`app/static/index.html`), no build
  step — keep it that way.

## Versioning note

`/health`'s per-cutter array (`cutters`) replaced the single `dls`
object in v0.4.0; external consumers should target that shape.
