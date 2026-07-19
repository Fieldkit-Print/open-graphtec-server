from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


@dataclass
class CutJob:
    id: int
    name: str
    barcode_link_info: str
    command_type: int
    regmark_fx: int
    regmark_fy: int
    regmark_rx: int
    regmark_ry: int
    command_sequence: bytes
    created_at: datetime


@dataclass
class CutJobMeta:
    """A CutJob without its command BLOB, for job-list building."""

    id: int
    name: str
    barcode_link_info: str
    command_type: int
    regmark_fx: int
    regmark_fy: int
    regmark_rx: int
    regmark_ry: int
    created_at: datetime


class ImportJsonJobRequest(BaseModel):
    # Job names go on the cutter's LCD via ESC.d3: max 25 printable ASCII
    # characters per the DLS guideline (p.19).
    name: str = Field(min_length=1, max_length=25, pattern=r"^[\x20-\x7e]+$")
    barcode_link_info: str = Field(min_length=9, max_length=9)
    command_type: Literal[0, 1] = 0
    regmark_fx: int = 0
    regmark_fy: int = 0
    regmark_rx: int = 0
    regmark_ry: int = 0
    command_sequence: str = Field(min_length=1)
    command_sequence_encoding: Literal["plain", "base64"] = "plain"
    append_etx: bool = True


class ImportJobResponse(BaseModel):
    job_id: int
    name: str
    barcode_link_info: str
    command_type: int
    command_length: int
    notes: Optional[str] = None


class JobSummary(BaseModel):
    id: int
    name: str
    barcode_link_info: str
    command_type: int
    created_at: datetime
    command_length: int


class JobDetails(BaseModel):
    id: int
    name: str
    barcode_link_info: str
    command_type: int
    regmark_fx: int
    regmark_fy: int
    regmark_rx: int
    regmark_ry: int
    created_at: datetime
    command_length: int
    command_preview: str

