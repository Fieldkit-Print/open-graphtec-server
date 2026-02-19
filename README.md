# Open Graphtec Server

Open Graphtec Server is a headless, Docker-based service for Graphtec cut jobs.

You send it vector cut PDFs over the network. It converts them into cutter commands, stores jobs by barcode, and serves the right job when the cutter requests it.

No web UI is required for normal production use.

## Why this exists

Many workflows generate:
- a print file for the printer
- a cut file for the cutter

This service handles the cut side automatically so print and cut stay matched by barcode.

## Works anywhere Docker runs

You can run this on:
- Linux servers
- ARM single-board computers
- Windows
- macOS

There is no device-specific logic in the code.

## How it works

1. Your prepress flow sends a cut PDF to the hot folder.
2. The service reads it and converts vector paths to cut commands.
3. The job is saved with barcode metadata.
4. The cutter asks for jobs and receives the matching one.

## Quick start (Docker)

### 1. Clone

```bash
git clone https://github.com/Fieldkit-Print/open-graphtec-server.git
cd open-graphtec-server
```

### 2. Create your settings file

```bash
cp .env.example .env
```

If you are just testing without a real cutter, keep `DLS_ENABLED=false`.

### 3. Start

```bash
docker compose up --build -d
```

### 4. Check health

```bash
curl http://localhost:8080/health
```

### 5. Drop a cut PDF

Put files in:
- Docker volume path: `/data/inbox/cut` inside container

If you prefer a host folder mount for testing, use:

```bash
docker compose -f docker-compose.local.yml up --build -d
```

Then use:
- `./data/inbox/cut`

## Input files

This service expects vector cut data on page 1.

Supported now:
- stroked lines
- stroked rectangles
- stroked curves (flattened into small line segments)

Rejected in strict mode:
- raster/scanned images
- fill-only shapes without stroke paths

## Optional sidecar JSON

For each `job.pdf`, you can provide one sidecar file:
- `job.job.json`
- `job.meta.json`
- `job.json`

Example:

```json
{
  "name": "front-label-001",
  "barcode_link_info": "G0100ABCD",
  "command_type": 0,
  "regmark_fx": 0,
  "regmark_fy": 0,
  "regmark_rx": 0,
  "regmark_ry": 0
}
```

Notes:
- `barcode_link_info` must be exactly 9 letters/numbers.
- `command_type`: `0` = GP-GL, `1` = HP-GL.
- If sidecar is missing, barcode is inferred from filename when possible.

## API endpoints (headless ops)

- `GET /health`
- `GET /dls/status`
- `GET /ingest/status`
- `POST /dls/start`
- `POST /dls/stop`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `POST /jobs/import-json`
- `POST /jobs/import-pdf`

## Common settings

Set these in `.env`:
- `CUTTER_HOST`: IP or hostname of your Graphtec cutter
- `CUTTER_PORT`: usually `9100`
- `DLS_ENABLED`: `true` when a cutter is connected
- `INGEST_ENABLED`: `true` to watch the hot folder

## Troubleshooting

- Files go to `errors`:
  check the `.error.txt` in the same folder for the reason.
- No jobs appear for a scan:
  make sure barcode is 9 alphanumeric characters.
- Service is up but cutter not responding:
  verify `CUTTER_HOST`/`CUTTER_PORT` and network reachability.

## Security note

This service has no built-in authentication.
Run it on a trusted internal network or behind your own access controls.

## SDK files

This public repo includes only this app and deployment files.
Vendor SDK documents and binaries are intentionally excluded.
