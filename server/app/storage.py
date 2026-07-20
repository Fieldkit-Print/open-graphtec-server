from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
import threading
from typing import Iterable, Optional

from .models import CutJob, CutJobMeta


class JobStore:
    def __init__(self, database_path: str) -> None:
        self._conn = sqlite3.connect(
            database_path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
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
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS counters (
                    name TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                )
                """
            )

    _BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    def allocate_barcode(self, prefix: str = "F") -> str:
        """Mint a unique 9-char barcode link info: prefix + base36 counter.

        The prefix must not start with 'G' (reserved by Graphtec).
        """
        if not prefix or prefix[0] == "G":
            raise ValueError("Barcode prefix must be set and must not start with 'G'.")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO counters (name, value) VALUES ('barcode', 1)
                ON CONFLICT(name) DO UPDATE SET value = value + 1
                """
            )
            row = self._conn.execute(
                "SELECT value FROM counters WHERE name = 'barcode'"
            ).fetchone()
        counter = int(row["value"])
        digits = ""
        while counter:
            counter, rem = divmod(counter, 36)
            digits = self._BASE36[rem] + digits
        body_len = 9 - len(prefix)
        if len(digits) > body_len:
            raise RuntimeError("Barcode counter space exhausted.")
        return prefix + digits.rjust(body_len, "0")

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
        created_at = datetime.now(timezone.utc).isoformat()
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
                    command_sequence,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    created_at,
                ),
            )
            return int(cursor.lastrowid)

    def delete_job(self, job_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM jobs WHERE id = ?", (job_id,)
            )
            return cursor.rowcount > 0

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

    def find_job_metas_for_barcode(
        self, barcode_link_info: str, *, limit: int = 8
    ) -> list[CutJobMeta]:
        """Most-recent jobs for a barcode, metadata only.

        Newest first, so a re-submitted job shadows stale ones instead of
        being unreachable behind them. Command BLOBs are loaded lazily via
        get_job() once the cutter has actually selected a job.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, name, barcode_link_info, command_type,
                       regmark_fx, regmark_fy, regmark_rx, regmark_ry,
                       created_at
                FROM jobs
                WHERE barcode_link_info = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (barcode_link_info, limit),
            ).fetchall()
        return [self._row_to_cut_job_meta(row) for row in rows]

    @staticmethod
    def _parse_created_at(raw: str) -> datetime:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            # Legacy rows were written by SQLite's datetime('now') in UTC.
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @classmethod
    def _row_to_cut_job(cls, row: sqlite3.Row) -> CutJob:
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
            created_at=cls._parse_created_at(str(row["created_at"])),
        )

    @classmethod
    def _row_to_cut_job_meta(cls, row: sqlite3.Row) -> CutJobMeta:
        return CutJobMeta(
            id=int(row["id"]),
            name=str(row["name"]),
            barcode_link_info=str(row["barcode_link_info"]),
            command_type=int(row["command_type"]),
            regmark_fx=int(row["regmark_fx"]),
            regmark_fy=int(row["regmark_fy"]),
            regmark_rx=int(row["regmark_rx"]),
            regmark_ry=int(row["regmark_ry"]),
            created_at=cls._parse_created_at(str(row["created_at"])),
        )
