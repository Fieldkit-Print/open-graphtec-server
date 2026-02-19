from __future__ import annotations

from datetime import datetime
import sqlite3
import threading
from typing import Iterable, Optional

from .models import CutJob


class JobStore:
    def __init__(self, database_path: str) -> None:
        self._conn = sqlite3.connect(
            database_path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    barcode_link_info TEXT NOT NULL,
                    command_type INTEGER NOT NULL CHECK(command_type IN (0, 1)),
                    regmark_fx INTEGER NOT NULL DEFAULT 0,
                    regmark_fy INTEGER NOT NULL DEFAULT 0,
                    regmark_rx INTEGER NOT NULL DEFAULT 0,
                    regmark_ry INTEGER NOT NULL DEFAULT 0,
                    command_sequence BLOB NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_jobs_barcode
                ON jobs(barcode_link_info)
                """
            )

    def create_job(
        self,
        *,
        name: str,
        barcode_link_info: str,
        command_type: int,
        regmark_fx: int,
        regmark_fy: int,
        regmark_rx: int,
        regmark_ry: int,
        command_sequence: bytes,
    ) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO jobs (
                    name,
                    barcode_link_info,
                    command_type,
                    regmark_fx,
                    regmark_fy,
                    regmark_rx,
                    regmark_ry,
                    command_sequence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    barcode_link_info,
                    command_type,
                    regmark_fx,
                    regmark_fy,
                    regmark_rx,
                    regmark_ry,
                    command_sequence,
                ),
            )
            return int(cursor.lastrowid)

    def get_job(self, job_id: int) -> Optional[CutJob]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_cut_job(row)

    def list_jobs(
        self, *, barcode_link_info: Optional[str] = None, limit: int = 100
    ) -> list[CutJob]:
        query = "SELECT * FROM jobs"
        params: Iterable[object]
        if barcode_link_info:
            query += " WHERE barcode_link_info = ?"
            params = (barcode_link_info,)
        else:
            params = ()
        query += " ORDER BY id DESC LIMIT ?"
        params = tuple(params) + (limit,)

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_cut_job(row) for row in rows]

    def find_jobs_for_barcode(self, barcode_link_info: str) -> list[CutJob]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM jobs
                WHERE barcode_link_info = ?
                ORDER BY id ASC
                """,
                (barcode_link_info,),
            ).fetchall()
        return [self._row_to_cut_job(row) for row in rows]

    @staticmethod
    def _row_to_cut_job(row: sqlite3.Row) -> CutJob:
        created_at = datetime.fromisoformat(str(row["created_at"]))
        return CutJob(
            id=int(row["id"]),
            name=str(row["name"]),
            barcode_link_info=str(row["barcode_link_info"]),
            command_type=int(row["command_type"]),
            regmark_fx=int(row["regmark_fx"]),
            regmark_fy=int(row["regmark_fy"]),
            regmark_rx=int(row["regmark_rx"]),
            regmark_ry=int(row["regmark_ry"]),
            command_sequence=bytes(row["command_sequence"]),
            created_at=created_at,
        )

