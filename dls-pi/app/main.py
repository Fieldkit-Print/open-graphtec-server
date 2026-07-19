from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import re
from typing import Optional

from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse

from .config import Settings
from .converters.pdf_to_gpgl import convert_pdf_to_gpgl
from .dls_worker import DataLinkServerWorker
from .ingest_worker import HeadlessIngestWorker
from .models import (
    ImportJobResponse,
    ImportJsonJobRequest,
    JobDetails,
    JobSummary,
)
from .protocol import ETX, validate_job_name
from .storage import JobStore


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _validate_barcode_link_info(value: str) -> str:
    barcode = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{9}", barcode):
        raise HTTPException(
            status_code=400,
            detail=(
                "barcode_link_info must be 9 alphanumeric characters "
                "(example: A0100ABCD)."
            ),
        )
    return barcode


def _validate_job_name_or_400(value: str) -> str:
    name = value.strip()
    try:
        return validate_job_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _decode_command_sequence(
    command_sequence: str, encoding: str, append_etx: bool
) -> bytes:
    if encoding == "base64":
        try:
            raw = base64.b64decode(command_sequence.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400, detail=f"Invalid base64 command_sequence: {exc}"
            ) from exc
    else:
        raw = command_sequence.encode("utf-8", errors="ignore")

    etx = ETX.encode("ascii")
    if append_etx and not raw.endswith(etx):
        raw += etx

    if not raw or raw.strip(etx) == b"":
        raise HTTPException(status_code=400, detail="command_sequence is empty.")
    return raw


def _job_to_summary(job) -> JobSummary:
    return JobSummary(
        id=job.id,
        name=job.name,
        barcode_link_info=job.barcode_link_info,
        command_type=job.command_type,
        created_at=job.created_at,
        command_length=len(job.command_sequence),
    )


def _job_to_details(job) -> JobDetails:
    preview = job.command_sequence[:180].decode("ascii", errors="ignore")
    if len(job.command_sequence) > 180:
        preview += "..."
    return JobDetails(
        id=job.id,
        name=job.name,
        barcode_link_info=job.barcode_link_info,
        command_type=job.command_type,
        regmark_fx=job.regmark_fx,
        regmark_fy=job.regmark_fy,
        regmark_rx=job.regmark_rx,
        regmark_ry=job.regmark_ry,
        created_at=job.created_at,
        command_length=len(job.command_sequence),
        command_preview=preview,
    )


settings = Settings.from_env()
_configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

store = JobStore(str(settings.database_path))
dls_worker = DataLinkServerWorker(settings=settings, store=store)
ingest_worker = HeadlessIngestWorker(settings=settings, store=store)


async def require_api_key(request: Request) -> None:
    """Reject requests without the configured API key.

    When API_KEY is unset the service runs open (trusted-network mode),
    matching the documented deployment model.
    """
    if not settings.api_key:
        return
    provided = request.headers.get("X-API-Key", "")
    if provided != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "API startup at %s", datetime.now(timezone.utc).isoformat()
    )
    if not settings.api_key:
        logger.warning(
            "API_KEY is not set: all endpoints are unauthenticated. "
            "Run only on a trusted network."
        )
    if settings.ingest_enabled:
        ingest_worker.start()
        logger.info("Headless ingest worker autostart is enabled.")
    if settings.dls_enabled:
        dls_worker.start()
        logger.info("DLS worker autostart is enabled.")
    yield
    ingest_worker.stop()
    dls_worker.stop()
    store.close()
    logger.info("API shutdown complete.")


app = FastAPI(
    title="Open Graphtec Server",
    version="0.2.0",
    description=(
        "Headless Graphtec Data Link service with network cut-file intake."
    ),
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


_STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/", include_in_schema=False)
def status_page() -> FileResponse:
    """Shop-floor status page. Reads only /health (open); job listing in
    the page prompts for the API key when one is configured."""
    return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "headless",
        "dls": dls_worker.get_status(),
        "ingest": ingest_worker.get_status(),
        "database_path": str(settings.database_path),
    }


@app.get("/ingest/status", dependencies=[Depends(require_api_key)])
def ingest_status() -> dict[str, object]:
    return ingest_worker.get_status()


@app.post("/ingest/start", dependencies=[Depends(require_api_key)])
def ingest_start() -> dict[str, object]:
    ingest_worker.start()
    return ingest_worker.get_status()


@app.post("/ingest/stop", dependencies=[Depends(require_api_key)])
def ingest_stop() -> dict[str, object]:
    ingest_worker.stop()
    return ingest_worker.get_status()


@app.get("/dls/status", dependencies=[Depends(require_api_key)])
def dls_status() -> dict[str, object]:
    return dls_worker.get_status()


@app.post("/dls/start", dependencies=[Depends(require_api_key)])
def dls_start() -> dict[str, object]:
    started = dls_worker.start()
    status = dls_worker.get_status()
    status["start_accepted"] = started
    return status


@app.post("/dls/stop", dependencies=[Depends(require_api_key)])
def dls_stop() -> dict[str, object]:
    stopped = dls_worker.stop()
    status = dls_worker.get_status()
    status["stop_completed"] = stopped
    return status


@app.post(
    "/jobs/import-json",
    response_model=ImportJobResponse,
    dependencies=[Depends(require_api_key)],
)
def import_json_job(request: ImportJsonJobRequest) -> ImportJobResponse:
    name = _validate_job_name_or_400(request.name)
    barcode = _validate_barcode_link_info(request.barcode_link_info)
    command_bytes = _decode_command_sequence(
        request.command_sequence,
        request.command_sequence_encoding,
        request.append_etx,
    )
    if len(command_bytes) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"command_sequence is {len(command_bytes)} bytes; maximum "
                f"allowed is {settings.max_upload_bytes}."
            ),
        )

    job_id = store.create_job(
        name=name,
        barcode_link_info=barcode,
        command_type=request.command_type,
        regmark_fx=request.regmark_fx,
        regmark_fy=request.regmark_fy,
        regmark_rx=request.regmark_rx,
        regmark_ry=request.regmark_ry,
        command_sequence=command_bytes,
    )

    return ImportJobResponse(
        job_id=job_id,
        name=name,
        barcode_link_info=barcode,
        command_type=request.command_type,
        command_length=len(command_bytes),
    )


@app.post(
    "/jobs/import-pdf",
    response_model=ImportJobResponse,
    dependencies=[Depends(require_api_key)],
)
async def import_pdf_job(
    file: UploadFile = File(...),
    name: str = Form(...),
    barcode_link_info: str = Form(...),
    command_type: int = Form(0),
    regmark_fx: int = Form(0),
    regmark_fy: int = Form(0),
    regmark_rx: int = Form(0),
    regmark_ry: int = Form(0),
) -> ImportJobResponse:
    if command_type != 0:
        # The converter emits GP-GL only; declaring HP-GL via ESC.d6 would
        # make an AUTO-mode cutter mis-parse the job.
        raise HTTPException(
            status_code=400,
            detail=(
                "command_type must be 0 (GP-GL) for PDF import: the "
                "converter emits GP-GL only."
            ),
        )
    job_name = _validate_job_name_or_400(name)
    barcode = _validate_barcode_link_info(barcode_link_info)

    raw_pdf = await file.read()
    if not raw_pdf:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty.")
    if len(raw_pdf) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Uploaded PDF is {len(raw_pdf)} bytes; maximum allowed is "
                f"{settings.max_upload_bytes}."
            ),
        )

    try:
        # pdfplumber parsing is CPU-bound; keep it off the event loop.
        command_bytes, details = await asyncio.to_thread(
            convert_pdf_to_gpgl,
            raw_pdf,
            steps_per_mm=settings.gpgl_steps_per_mm,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job_id = store.create_job(
        name=job_name,
        barcode_link_info=barcode,
        command_type=command_type,
        regmark_fx=regmark_fx,
        regmark_fy=regmark_fy,
        regmark_rx=regmark_rx,
        regmark_ry=regmark_ry,
        command_sequence=command_bytes,
    )

    return ImportJobResponse(
        job_id=job_id,
        name=job_name,
        barcode_link_info=barcode,
        command_type=command_type,
        command_length=len(command_bytes),
        notes=(
            f"Converted PDF to GP-GL sequence, "
            f"segments={details['segment_count']}"
        ),
    )


@app.get(
    "/jobs",
    response_model=list[JobSummary],
    dependencies=[Depends(require_api_key)],
)
def list_jobs(
    barcode_link_info: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[JobSummary]:
    barcode: Optional[str] = None
    if barcode_link_info:
        barcode = _validate_barcode_link_info(barcode_link_info)

    jobs = store.list_jobs(barcode_link_info=barcode, limit=limit)
    return [_job_to_summary(job) for job in jobs]


@app.get(
    "/jobs/{job_id}",
    response_model=JobDetails,
    dependencies=[Depends(require_api_key)],
)
def get_job(job_id: int) -> JobDetails:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _job_to_details(job)
