from __future__ import annotations

import base64
from datetime import datetime
import logging
import re
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile

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
from .protocol import ETX
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
                "(example: G0100ABCD)."
            ),
        )
    return barcode


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

    if append_etx and not raw.endswith(ETX.encode("ascii")):
        raw += ETX.encode("ascii")

    if not raw:
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

app = FastAPI(
    title="Open Graphtec Server",
    version="0.1.0",
    description=(
        "Headless Graphtec Data Link service with network cut-file intake."
    ),
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.on_event("startup")
def startup_event() -> None:
    logger.info("API startup at %s", datetime.utcnow().isoformat() + "Z")
    if settings.ingest_enabled:
        ingest_worker.start()
        logger.info("Headless ingest worker autostart is enabled.")
    if settings.dls_enabled:
        dls_worker.start()
        logger.info("DLS worker autostart is enabled.")


@app.on_event("shutdown")
def shutdown_event() -> None:
    ingest_worker.stop()
    dls_worker.stop()
    logger.info("API shutdown complete.")


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "headless",
        "dls": dls_worker.get_status(),
        "ingest": ingest_worker.get_status(),
        "database_path": str(settings.database_path),
    }


@app.get("/ingest/status")
def ingest_status() -> dict[str, object]:
    return ingest_worker.get_status()


@app.get("/dls/status")
def dls_status() -> dict[str, object]:
    return dls_worker.get_status()


@app.post("/dls/start")
def dls_start() -> dict[str, object]:
    dls_worker.start()
    return dls_worker.get_status()


@app.post("/dls/stop")
def dls_stop() -> dict[str, object]:
    dls_worker.stop()
    return dls_worker.get_status()


@app.post("/jobs/import-json", response_model=ImportJobResponse)
def import_json_job(request: ImportJsonJobRequest) -> ImportJobResponse:
    barcode = _validate_barcode_link_info(request.barcode_link_info)
    command_bytes = _decode_command_sequence(
        request.command_sequence,
        request.command_sequence_encoding,
        request.append_etx,
    )

    job_id = store.create_job(
        name=request.name.strip(),
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
        name=request.name.strip(),
        barcode_link_info=barcode,
        command_type=request.command_type,
        command_length=len(command_bytes),
    )


@app.post("/jobs/import-pdf", response_model=ImportJobResponse)
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
    if command_type not in (0, 1):
        raise HTTPException(
            status_code=400, detail="command_type must be 0 (GP-GL) or 1 (HP-GL)."
        )
    barcode = _validate_barcode_link_info(barcode_link_info)

    raw_pdf = await file.read()
    if not raw_pdf:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty.")

    try:
        command_bytes, details = convert_pdf_to_gpgl(raw_pdf)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job_id = store.create_job(
        name=name.strip(),
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
        name=name.strip(),
        barcode_link_info=barcode,
        command_type=command_type,
        command_length=len(command_bytes),
        notes=(
            f"Converted PDF to starter GP-GL sequence, "
            f"segments={details['segment_count']}"
        ),
    )


@app.get("/jobs", response_model=list[JobSummary])
def list_jobs(
    barcode_link_info: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[JobSummary]:
    barcode: Optional[str] = None
    if barcode_link_info:
        barcode = _validate_barcode_link_info(barcode_link_info)

    jobs = store.list_jobs(barcode_link_info=barcode, limit=limit)
    return [_job_to_summary(job) for job in jobs]


@app.get("/jobs/{job_id}", response_model=JobDetails)
def get_job(job_id: int) -> JobDetails:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _job_to_details(job)
