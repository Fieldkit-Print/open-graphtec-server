# Hot folders

Both hot folders exist so prepress tools that speak "watched folder"
natively (RIPs, pdfToolbox Server, Enfocus Switch) integrate with zero
glue code. The HTTP API remains the underlying contract — the folders
are adapters over the same engine.

## Print hot folder — `inbox/print/`

Drop a pre-imposed print PDF with marked cut paths (see
[print-preparation.md](print-preparation.md)).

- Output: `outbox/print/<name>_<barcode>.pdf` — the marked file to
  print. The cut job is registered at the same moment.
- A 9-character barcode token in the filename is honored
  (`labels_A0812XYZ1.pdf` → barcode `A0812XYZ1`); otherwise the server
  mints one.
- The consumed input moves to `processed/` (timestamped); failures move
  to the error folder with `<name>.error.txt` explaining the rejection.

## Cut hot folder — `inbox/cut/`

For cut-only jobs (no print output): a vector PDF whose page-1 stroked
paths *are* the cut lines. An optional sidecar JSON carries metadata:

```json
{
  "name": "front-label-001",
  "barcode_link_info": "A0100ABCD",
  "command_type": 0,
  "regmark_fx": 60,
  "regmark_fy": 400
}
```

Sidecar lookup order: `<stem>.job.json`, `<stem>.meta.json`,
`<stem>.json`. Without a sidecar, a 9-character token in the filename
supplies the barcode. `regmark_fx/fy` is the Start-Mark-to-first-mark
vector in 0.1 mm units — if you use the print hot folder instead, this
is computed for you, which is the main reason to prefer it.

## Pickup discipline (both folders)

- A file must sit **unchanged for `INGEST_FILE_MIN_AGE_SECONDS`**
  (default 2 s) before pickup, so half-copied files are never read.
- In the cut folder, a PDF additionally waits one extra poll for a
  late-arriving sidecar before falling back to the filename.
- Files are processed oldest-first; inputs are always moved out of the
  inbox (to `processed/` or the error folder) so nothing is handled
  twice.
- If the server is down, files simply wait in the inbox — the folder is
  a durable queue.

## Watching for problems

The status page header shows the hot-folder state and error count; the
`.error.txt` next to a rejected file always contains the precise
reason. Worker status is also at `GET /ingest/status` and
`GET /print/status`.
