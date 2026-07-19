# dls-pi App

This folder contains the Python service for the headless Graphtec Data Link server.

For full setup and production deployment steps, use the root guide:
- `README.md` at repo root

## Run directly with Python (developer mode)

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Then check:

```bash
curl http://localhost:8080/health
```

## Main modules

- `app/main.py` API entrypoint and startup workers
- `app/ingest_worker.py` hot-folder PDF intake worker
- `app/dls_worker.py` Graphtec Data Link state machine worker
- `app/converters/pdf_to_gpgl.py` vector PDF to GP-GL converter
- `app/storage.py` SQLite job storage
- `app/protocol.py` Graphtec protocol commands and socket client

## Important behavior

- No web UI pages are exposed.
- Jobs are matched by `barcode_link_info` (newest 8 jobs offered first).
- Converter is strict vector-only by default and emits GP-GL only
  (`command_type` must be 0 on PDF paths).
- Job names are limited to 25 printable ASCII characters (cutter panel limit).
- Set `API_KEY` to require an `X-API-Key` header on all endpoints except
  `/health`.
- Set `GPGL_STEPS_PER_MM` to match the cutter's GP-GL STEP SIZE setting
  (10 = 0.1 mm factory default).

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite includes a fake cutter (`tests/fake_cutter.py`) that speaks the
cutter side of the ESC.d1-d6 Data Link protocol over TCP, with fault
injection for truncated responses and missing ETX terminators. Converter
tests build minimal PDFs in-memory and assert on the exact GP-GL output.
