# Graphtec Headless Data Link Server (Raspberry Pi + Balena)

This project runs a Graphtec Data Link server with no UI.

You drop a vector cut PDF into a network folder, and the service does the rest:
- reads the file
- converts vector cut lines into cutter commands
- stores a job by barcode
- serves that job when the cutter asks for it

This is designed for hands-off production.

## What problem this solves

In many print-and-cut lines, your prepress tool creates two files:
- a print file for the printer
- a cut file for the cutter

This service is the missing bridge on the Pi. It receives the cut file, keeps jobs ready, and responds to the Graphtec Data Link flow.

## How the flow works

1. PDF Toolbox sends the print file to the printer.
2. PDF Toolbox sends the cut PDF (and optional JSON sidecar) to the Pi hot folder.
3. This service ingests the file and stores a cutter job.
4. The operator scans the printed barcode at the cutter.
5. The cutter asks for matching jobs and receives the cut data.

## Repo layout

- `dls-pi/` app source code
- `docker-compose.yml` Balena deployment compose (Pi production)
- `docker-compose.local.yml` local Docker compose (easy testing)
- `.env.example` editable settings template

## Quick start (local, no Pi needed)

### Prerequisites

- Docker Desktop (or Docker Engine)
- Git

### 1. Clone and enter the repo

```bash
git clone https://github.com/Fieldkit-Print/graphtec-sdk.git
cd graphtec-sdk
```

### 2. Create local settings

```bash
cp .env.example .env
```

If you do not have a cutter connected yet, keep `DLS_ENABLED=false` in `.env`.

### 3. Start the service

```bash
docker compose -f docker-compose.local.yml up --build -d
```

### 4. Verify health

```bash
curl http://localhost:8080/health
```

You should see `"ok": true` and `"mode": "headless"`.

### 5. Drop a cut file

Put files in:
- `./data/inbox/cut` (created automatically)

On success, files move to:
- `./data/processed`

On failure, files move to:
- `./data/errors`

## Deploy to Raspberry Pi with Balena

### 1. Create a Balena app for your Pi fleet

Use Balena Cloud to create an app for your Pi device type.

### 2. Set key environment values in Balena

Set these in Balena app/device variables:
- `CUTTER_HOST` = cutter IP address on your LAN
- `CUTTER_PORT` = usually `9100`
- `DLS_ENABLED` = `true`
- `INGEST_ENABLED` = `true`

### 3. Deploy

From repo root:

```bash
balena push <your-balena-app-name>
```

Balena uses `docker-compose.yml` in this repo.

## Input file rules

This service expects vector cut data on page 1 of the PDF.

### Supported today

- stroked lines
- stroked rectangles
- stroked curves (flattened to short line segments)

### Rejected today

- raster/scanned cut files in strict mode
- fill-only shapes with no stroke path

## Sidecar JSON (recommended)

For each `job.pdf`, place an optional JSON file with one of these names:
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
- If no sidecar is present, barcode is inferred from filename when possible.

## Headless API endpoints

No UI pages are enabled. These API routes are available for status and automation.

- `GET /health`
- `GET /dls/status`
- `GET /ingest/status`
- `POST /dls/start`
- `POST /dls/stop`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `POST /jobs/import-json`
- `POST /jobs/import-pdf`

## Example API import (optional)

```bash
curl -X POST http://localhost:8080/jobs/import-pdf \
  -F "file=@/absolute/path/to/cut.pdf" \
  -F "name=sample-job" \
  -F "barcode_link_info=G0100ABCD" \
  -F "command_type=0"
```

## Troubleshooting

- `health` is up but no jobs load:
  check folder path and make sure files are true vector PDFs.
- jobs move to `errors`:
  open the matching `.error.txt` file in the errors folder.
- cutter does not receive jobs:
  verify `CUTTER_HOST`, `CUTTER_PORT`, and network reachability from Pi to cutter.
- wrong job list on cutter:
  verify barcode content is exactly 9 alphanumeric characters.

## Security and operations notes

- This service has no login/auth layer by default. Put it on a trusted network.
- Keep regular backups of `/data/jobs.db` if job history matters.
- Keep Pi and Balena host up to date with security patches.

## SDK files

This public repo ships only the app code and deployment files.
Graphtec SDK documents and binaries are intentionally excluded.

## Current status

This repo is production-usable for vector-only cut workflows and is still growing.
Planned improvements include smarter vector filtering and stronger file validation profiles for different cut pipelines.
