# Open Graphtec Server

**A headless, open-source Data Link Server for Graphtec cutting plotters.**

Send vector cut PDFs to a hot folder or the HTTP API. The server converts
them to GP-GL, stores them by barcode, and serves the right job the moment
your cutter scans that barcode — no Windows box, no Graphtec Studio, no
operator at a PC.

Validated end-to-end on an FC9000-140 over the network: barcode scan →
job handoff → 4-point ARMS registration → cut.

```
┌──────────┐  cut PDF   ┌─────────────────────┐  ESC.d1–d6  ┌─────────────┐
│ prepress │ ─────────► │ Open Graphtec Server │ ◄─────────► │ FC9000 /    │
│ RIP / API│  hot folder│  convert · store ·   │  TCP 9100   │ CE7000 ...  │
└──────────┘            │  serve by barcode    │             │ scans, cuts │
                        └─────────────────────┘             └─────────────┘
```

## Why

Barcode-driven cutting ("Data Link") normally requires Graphtec's desktop
software running somewhere. This server speaks the Data Link protocol
directly, so the cut side of a print-and-cut workflow becomes an unattended
service: print jobs and cut jobs stay matched by a 9-character barcode, and
the cutter pulls its own work.

- **Headless first** — runs anywhere Docker (or Python 3.11+) runs: a
  server, a NAS, a single-board computer.
- **Status UI included** — a zero-build shop-floor page at `/` shows the
  cutter, Data Link state, recent activity, and loaded jobs.
- **Tested against a fake cutter** — the test suite includes a TCP
  implementation of the cutter side of the protocol, with fault injection.

## Supported hardware

Data Link–capable Graphtec models (FC9000, CE7000 and newer grit-rolling
models) connected via LAN on TCP 9100. Multiple cutters can share one
server and one job library: each machine is offered the jobs matching
the barcode it scanned. Development and validation were done
on an FC9000-140 (firmware V1.39).

## Quick start

```bash
git clone https://github.com/Fieldkit-Print/open-graphtec-server.git
cd open-graphtec-server
cp .env.example .env          # set CUTTER_HOST; keep DLS_ENABLED=false to try without a cutter
docker compose up --build -d
open http://localhost:8080/   # status UI
```

Drop a vector cut PDF (with optional sidecar JSON) into the hot folder —
`/data/inbox/cut` inside the container — or POST it to the API. When the
cutter scans the matching barcode, the job is delivered automatically.

To run bare-metal instead of Docker:

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
CUTTER_HOST=192.168.1.50 DLS_ENABLED=true uvicorn app.main:app --port 8080
```

## Input files

The hot folder expects vector cut data on page 1 of a PDF: stroked lines,
rectangles, and curves (flattened to segments). Raster images and fill-only
shapes are rejected in strict mode. An optional sidecar (`job.job.json`,
`job.meta.json`, or `job.json`) carries metadata:

```json
{
  "name": "front-label-001",
  "barcode_link_info": "A0100ABCD",
  "command_type": 0,
  "regmark_fx": 60,
  "regmark_fy": 400
}
```

- `barcode_link_info`: exactly 9 characters `0-9 A-Z`; the first character
  must not be `G` (reserved by Graphtec). Missing sidecar? A 9-character
  token in the filename is used.
- `regmark_fx/fy`: offset from the barcode Start Mark to the first
  registration mark, in 0.1 mm units.
- `command_type`: `0` = GP-GL (HP-GL input is not converted yet).

## HTTP API

All endpoints except `/` and `/health` require an `X-API-Key` header when
`API_KEY` is set (leave it empty on a trusted network).

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Status UI (HTML) |
| GET | `/health` | Full service health snapshot |
| GET | `/dls/status` | Per-cutter Data Link state (`?cutter=name` for one) |
| GET | `/dls/events` | Activity log across cutters |
| POST | `/dls/start` · `/dls/stop` | Control Data Link workers (all, or `?cutter=name`) |
| GET | `/ingest/status` | Hot-folder worker state |
| POST | `/ingest/start` · `/ingest/stop` | Control the hot-folder worker |
| GET | `/jobs` | List jobs (filter: `barcode_link_info`) |
| GET | `/jobs/{id}` | Job details |
| GET | `/jobs/{id}/gpgl` | Download the stored command bytes |
| DELETE | `/jobs/{id}` | Delete a job |
| GET | `/cutter/info` | Live cutter identity/settings query |
| GET | `/version` | Service name, version, server time |
| POST | `/print/prepare` | Add barcode + marks to a print PDF, register the cut job |
| GET/POST | `/print/status` · `/print/start` · `/print/stop` | Print-prepare hot-folder worker |
| POST | `/jobs/import-pdf` | Upload a PDF for conversion |
| POST | `/jobs/import-json` | Import a raw GP-GL job |

## Configuration

Set in `.env` (see `.env.example` for the full list):

| Variable | Meaning |
|---|---|
| `CUTTER_HOST` / `CUTTER_PORT` | Single-cutter address (port is normally 9100) |
| `CUTTERS` | Multi-cutter setup: `left=10.0.0.4:9100,right=10.0.0.5` (overrides `CUTTER_HOST`) |
| `DLS_ENABLED` | Poll the cutter (enable when one is connected) |
| `INGEST_ENABLED` | Watch the hot folder |
| `API_KEY` | Require `X-API-Key` on the API (empty = open) |
| `GPGL_STEPS_PER_MM` | Cutter's GP-GL step size (10 = 0.1 mm default) |
| `MAX_UPLOAD_BYTES` | Upload / conversion size cap |

On startup the server reads the cutter's model and step size and warns
loudly if the configured scale does not match the machine.

## Print preparation

Send a pre-imposed print PDF whose cut paths are on an **ISO 19593-1
processing-steps layer** (`Structural`/`Cutting`) — or, as a legacy
fallback, in a spot color named `CutContour` — and the server returns the
same PDF with the Graphtec barcode and registration marks overlaid
(original content untouched), while registering the matching cut job under
a server-minted barcode. One computation produces both artifacts, so the
printed marks and the stored job geometry cannot drift.

- `POST /print/prepare` — marked PDF back in the response
  (`X-Barcode`, `X-Job-Id` headers)
- hot folder — drop into `inbox/print/`, collect
  `<name>_<barcode>.pdf` from `outbox/print/`

Artwork must keep clear of the barcode band at the leading edge and the
mark corners; violations are rejected with a precise reason.

## Test sheets

`test-sheet/` contains generators for printable barcode + registration-mark
test sheets and their matching jobs — the same sheets used to validate this
project on hardware. Print at 100% scale, import the job, scan, and the
cutter should trace the printed shapes.

## Development

```bash
cd server
pip install -r requirements-dev.txt
python -m pytest
```

The suite (62 tests) includes `tests/fake_cutter.py`, a scriptable TCP
implementation of the cutter side of the ESC.d1–d6 protocol with fault
injection, plus golden-file converter tests built on in-memory PDFs.

## Security

No authentication is enabled by default; the service is designed for
trusted internal networks. Set `API_KEY` for anything beyond that, and put
a reverse proxy in front if you need TLS.

## License

MIT — see [LICENSE](LICENSE). Not affiliated with or endorsed by Graphtec
Corporation. Graphtec SDK documents are not included in this repository.
