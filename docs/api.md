# HTTP API

Base URL: `http://<server>:8080`. When `API_KEY` is set, send it as the
`X-API-Key` header on every endpoint except `/`, `/health`, and
`/version`.

## Status & monitoring

### `GET /health` *(open)*
Full snapshot: per-cutter Data Link status (`cutters` array), hot-folder
workers (`ingest`, `print_prepare`), webhook delivery counters, database
path. The status UI polls this.

### `GET /version` *(open)*
`{"name", "version", "server_time_utc"}` — for uptime probes.

### `GET /dls/status[?cutter=name]`
All cutters (array) or one. Each status includes `cutter_name`,
`is_running`, `standby_mode`, `current_status` (the raw Data Link code),
`current_status_label`, `last_error`, `cutter_model`,
`cutter_step_size_mm`, and `recent_events` (last 50).

### `GET /dls/events[?cutter=name]`
The merged activity log across cutters, oldest first, each entry
`{"time", "kind", "message", "cutter"}`.

### `GET /cutter/info[?cutter=name]`
Queries the machine live: model/firmware, step size, command setting.
Returns **409** while that cutter is mid scan-cycle (the cutter accepts
one connection at a time), **503** if it doesn't respond.

## Worker control

### `POST /dls/start[?cutter=name]` · `POST /dls/stop[?cutter=name]`
Start/stop Data Link polling — all cutters, or one. Returns the resulting
statuses with `start_accepted` / `stop_completed` flags.

### `POST /ingest/start` · `POST /ingest/stop` · `GET /ingest/status`
The cut-file hot-folder worker.

### `POST /print/start` · `POST /print/stop` · `GET /print/status`
The print-preparation hot-folder worker.

## Print preparation

### `POST /print/prepare`
Multipart form: `file` (the print PDF), optional `name` (job name,
defaults to the filename), optional `barcode_link_info` (9 chars; omitted
= server-minted).

Returns the **marked print PDF** with headers:

| Header | Meaning |
|---|---|
| `X-Job-Id` | Registered cut job id |
| `X-Barcode` | The 9-char barcode link info |
| `X-Cut-Source` | `processing-steps`, `spot-color`, or `mixed` |
| `X-Cut-Segments` | Number of cut segments extracted |
| `X-Warnings` | Present when e.g. legacy spot naming was used |

Errors: **400** with a precise reason (no cut paths; artwork in a
keep-clear zone; cut geometry outside the marks; page too small),
**413** over the size cap.

```bash
curl -X POST http://localhost:8080/print/prepare \
     -F "file=@labels.pdf" -F "name=front-labels" \
     -o labels_marked.pdf -D -
```

## Jobs

### `GET /jobs?barcode_link_info=...&limit=100`
Job summaries, newest first.

### `GET /jobs/{id}`
Details including the regmark vector and a command preview.

### `GET /jobs/{id}/gpgl`
Downloads the stored command sequence byte-exact — what the cutter will
receive.

### `DELETE /jobs/{id}`
Removes a job; cutters are no longer offered it.

### `POST /jobs/import-json`
Register a raw GP-GL job:

```json
{
  "name": "my-job",                  // <= 25 printable ASCII chars
  "barcode_link_info": "A0100ABCD",  // 9 chars, [0-9A-Z], not G-initial
  "command_type": 0,                 // 0 = GP-GL
  "regmark_fx": 60, "regmark_fy": 400,
  "regmark_rx": 0,  "regmark_ry": 0, // rear-edge barcode only
  "command_sequence": "...",         // plain or base64
  "command_sequence_encoding": "plain",
  "append_etx": true
}
```

### `POST /jobs/import-pdf`
Multipart: `file` + `name` + `barcode_link_info` (+ regmark fields).
Converts a vector cut PDF to GP-GL and registers it. `command_type` must
be 0 — the converter emits GP-GL only.

## Behavior notes

- When a cutter scans a barcode, the server offers the **newest 8 jobs**
  matching it. One match skips the cutter's selection screen entirely.
- `/health`'s shape changed in v0.4: per-cutter data lives in the
  `cutters` array (previously a single `dls` object).
