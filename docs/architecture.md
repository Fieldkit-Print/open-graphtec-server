# Architecture

## Data flow

```
                         ┌──────────────────────────────────────────────┐
 print PDF w/ cut paths  │                Open Graphtec Server           │
──────────────────────►  │                                              │
 POST /print/prepare     │  cut_extract ──► marks.compute_layout ──┐    │
 or inbox/print/         │  (ISO 19593-1 /      │                  │    │
                         │   CutContour)        ▼                  ▼    │
      marked print PDF ◄─┤              overlay (pikepdf)   GP-GL + TB  │
                         │                                        │     │
 cut-only PDF            │  pdf_to_gpgl ────────────────────────► │     │
──────────────────────►  │                                        ▼     │
 /jobs/* or inbox/cut/   │                                   jobs.db    │
                         │                                        │     │
                         │  DataLinkServerWorker (one per cutter) │     │
                         │  ESC.d1 poll ─ barcode ─ job list ─────┘     │
                         └───────────────┬──────────────────┬───────────┘
                                         │ TCP 9100         │ webhooks
                                    Graphtec cutter(s)   your systems
```

## Modules (`server/app/`)

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app: endpoints, worker lifecycle, auth |
| `config.py` | Environment-driven settings incl. multi-cutter parsing |
| `protocol.py` | Data Link wire protocol (ESC.d1–d6), retry discipline, job-name/status validation, cutter query commands |
| `dls_worker.py` | Per-cutter polling state machine, standby handling, event log, startup handshake |
| `marks.py` | Barcode/mark layout math, CODE39, print overlay, TB-prefixed GP-GL builder — the single source of geometry truth |
| `converters/cut_extract.py` | Content-stream walker extracting cut paths (processing steps + spot colors) |
| `converters/pdf_to_gpgl.py` | Vector-PDF → GP-GL for cut-only ingestion |
| `print_prepare.py` | Orchestrates extraction → layout → validation → overlay → job |
| `print_worker.py` / `ingest_worker.py` | Hot-folder workers |
| `storage.py` | SQLite job library + barcode counter |
| `webhook.py` | Outbound event notifications |
| `static/index.html` | The status UI (single file, no build step) |

## Storage

One SQLite database (`jobs.db`, WAL mode). The `jobs` table stores each
job **complete** — name, barcode, registration vector, and the full
GP-GL command sequence as a BLOB — so a job is self-contained and served
byte-exact. A `counters` table backs barcode minting. Jobs persist until
deleted via `DELETE /jobs/{id}`.

## Design principles

- **One geometry brain.** Everything that must agree — printed marks,
  barcode, registration vector, TB distances, cut coordinates — is
  computed in one place (`marks.py`) in one pass. There is no second
  implementation to drift.
- **Never rewrite customer artwork.** Print preparation overlays; it
  does not re-render. Extraction reads; it does not modify.
- **The cutter is polled, never trusted to push.** All liveliness
  (status, events, webhooks) is synthesized server-side from the poll
  loop.
- **Fail loudly and precisely.** Rejections (zone intrusions, missing
  cut paths, scale mismatches) carry the exact reason; hot-folder
  failures leave a `.error.txt`.
- **Workers never die silently.** Poll loops are exception-proof; errors
  surface in status and the activity log while polling continues.

## Concurrency model

FastAPI serves HTTP on the event loop (CPU-heavy PDF work is pushed to
threads). Each cutter has a daemon polling thread; each hot folder has
one. SQLite access is serialized behind a lock. Per-cutter `flock` lock
files prevent two processes from driving the same machine.
