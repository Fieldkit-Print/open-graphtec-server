# Getting started

## Requirements

- A Data Link–capable Graphtec cutter (FC9000, CE7000, or newer
  grit-rolling model) reachable on your network, TCP port 9100.
- Docker, or Python 3.11+ for a bare-metal run.

## Install and run

### Docker (recommended)

```bash
git clone https://github.com/Fieldkit-Print/open-graphtec-server.git
cd open-graphtec-server
cp .env.example .env
# edit .env: set CUTTER_HOST to your cutter's IP, DLS_ENABLED=true
docker compose up --build -d
```

Open `http://localhost:8080/` — the status page shows the server, your
cutter(s), recent activity, and loaded jobs. On startup the server reads
the cutter's model and GP-GL step size and warns if the configured scale
doesn't match the machine.

To keep the data folders on the host instead of a named volume, use
`docker compose -f docker-compose.local.yml up --build -d` (paths under
`./data`).

### Bare metal

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
CUTTER_HOST=192.168.1.50 DLS_ENABLED=true uvicorn app.main:app --port 8080
```

## Cutter panel setup (one-time)

1. Connect the cutter to the LAN and note its IP address.
2. In the LINK / Data Link menu, set the destination to **SERVER (LAN)**.
3. Set ARMS to **MARK TYPE 2, 4-point** (matches the marks this server
   prints).
4. Command setting **GP-GL** or **AUTO** (AUTO recommended).

## Your first barcode cut

1. Produce a print PDF with the cut paths marked — see
   [Print preparation](print-preparation.md). For a quick test you can use
   the generators in `test-sheet/`.
2. Send it through the server:
   ```bash
   curl -X POST http://localhost:8080/print/prepare \
        -F "file=@labels.pdf" -o labels_marked.pdf -D headers.txt
   ```
   The `X-Barcode` header tells you the assigned code; the cut job is
   already registered.
3. **Print `labels_marked.pdf` at 100% scale** — never "fit to page".
4. Load the sheet: printed side up, **barcode edge toward the operator,
   the solid black Start Mark block on the right**.
5. Start the barcode scan from the cutter panel. The cutter reads the
   code, pulls the job from the server, scans the four corner marks, and
   cuts along your contours.

Watch it live on the status page — the cutter tile walks through the
Data Link states and the activity feed logs the barcode request and the
job handoff.

## Where things live

| Path (in `APP_DATA_DIR`, `/data` under Docker) | Purpose |
|---|---|
| `jobs.db` | SQLite job library (jobs are stored complete, GP-GL included) |
| `inbox/print/` | hot folder: print PDFs to prepare |
| `outbox/print/` | prepared print PDFs (`<name>_<barcode>.pdf`) |
| `inbox/cut/` | hot folder: cut-only PDFs (no print output) |
| `processed/` | consumed input files, timestamped |
| `errors/` | rejected files + `<name>.error.txt` with the reason |
