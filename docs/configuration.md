# Configuration

All configuration is via environment variables (see `.env.example`).
Values marked *clamped* are limited to the Graphtec-specified range even
if you set something wilder.

## Cutters

| Variable | Default | Meaning |
|---|---|---|
| `CUTTERS` | — | Multi-cutter setup: `name=host[:port]` entries, comma-separated. Example: `left=192.168.1.104:9100,right=192.168.1.105`. Overrides `CUTTER_HOST`. Names must be unique, alphanumeric/dash/underscore. |
| `CUTTER_HOST` | `127.0.0.1` | Single-cutter fallback address |
| `CUTTER_PORT` | `9100` | Single-cutter fallback port |
| `CUTTER_NAME` | `cutter` | Display name in single-cutter mode |
| `DLS_ENABLED` | `false` | Start Data Link polling at boot. Keep `false` until a cutter is connected. |
| `DLS_POLL_INTERVAL_SECONDS` | `1.0` | Status poll cadence (*clamped* 0.5–2.0) |
| `DLS_TIMEOUT_SECONDS` | `5.0` | Per-command socket timeout (*clamped* 3–10) |
| `SEND_RETRY_TOTAL_MS` | `3000` | Cumulative wait budget for send retries (the mechanism that lets tools time-share the cutter's single connection) |
| `SEND_RETRY_INTERVAL_MS` | `50` | Delay between retry attempts |
| `GPGL_STEPS_PER_MM` | `10` | Must match the cutter's GP-GL STEP SIZE (10 = 0.1 mm factory default; also 20, 40, 100). A mismatch scales every cut; the startup handshake warns loudly if it detects one. |

## API

| Variable | Default | Meaning |
|---|---|---|
| `API_HOST` / `API_PORT` | `0.0.0.0` / `8080` | Bind address |
| `API_KEY` | *(empty)* | When set, every endpoint except `/`, `/health`, and `/version` requires the `X-API-Key` header. Empty = open (trusted network). |
| `MAX_UPLOAD_BYTES` | `20971520` | Size cap for uploaded PDFs and command sequences |
| `LOG_LEVEL` | `INFO` | Python logging level |

## Print preparation

| Variable | Default | Meaning |
|---|---|---|
| `CUT_SPOT_COLORS` | `CutContour` | Comma-separated spot-color names accepted as legacy cut markers. ISO 19593-1 processing steps are always accepted regardless. |
| `BARCODE_PREFIX` | `F` | Prefix for server-minted barcodes (rest is a base36 counter). Must not start with `G` — Graphtec reserves it. |
| `PRINT_INGEST_ENABLED` | `true` | Watch the print hot folder |
| `PRINT_INBOX_DIR` | `$APP_DATA_DIR/inbox/print` | Print hot folder |
| `PRINT_OUTBOX_DIR` | `$APP_DATA_DIR/outbox/print` | Marked print files |
| `PRINT_ERROR_DIR` | `$APP_DATA_DIR/errors` | Rejected print files |

## Cut-file hot folder

| Variable | Default | Meaning |
|---|---|---|
| `INGEST_ENABLED` | `true` | Watch the cut hot folder |
| `INGEST_INBOX_DIR` | `$APP_DATA_DIR/inbox/cut` | Cut hot folder |
| `INGEST_PROCESSED_DIR` | `$APP_DATA_DIR/processed` | Consumed inputs |
| `INGEST_ERROR_DIR` | `$APP_DATA_DIR/errors` | Rejected inputs |
| `INGEST_POLL_INTERVAL_SECONDS` | `2.0` | Folder poll cadence |
| `INGEST_FILE_MIN_AGE_SECONDS` | `2.0` | A file must sit unchanged this long before pickup (guards against half-copied files) |

## Webhooks

| Variable | Default | Meaning |
|---|---|---|
| `WEBHOOK_URL` | *(empty)* | POST target for `job.sent` / `barcode.no_match` events. Empty = disabled. |
| `WEBHOOK_SECRET` | *(empty)* | When set, payloads are signed: `X-OGS-Signature: sha256=<HMAC-SHA256 hex>` |

## Storage

| Variable | Default | Meaning |
|---|---|---|
| `APP_DATA_DIR` | `./data` (`/data` in Docker) | Root of all state |
| `DATABASE_PATH` | `$APP_DATA_DIR/jobs.db` | SQLite job library |
| `LOCK_FILE_PATH` | `$APP_DATA_DIR/dls.lock` | Base name for per-cutter Data Link lock files (`dls-<name>.lock`) |
