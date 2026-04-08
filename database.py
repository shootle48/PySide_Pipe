"""
database.py  (PySide6 edition — identical logic to FastAPI version)
───────────
SQLite persistence layer for the Pipe Inspector.

Schema and all methods are 1-to-1 with backend/database.py.
Only DEFAULT_DB_PATH differs (stored inside pipe-inspector-pyside/data/).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "pipe_inspector.db"

_DDL = """
CREATE TABLE IF NOT EXISTS batches (
    id          TEXT    PRIMARY KEY,
    started_at  TEXT    NOT NULL,
    ended_at    TEXT,
    total       INTEGER NOT NULL DEFAULT 0,
    ng          INTEGER NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS inspections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    piece_id    TEXT    NOT NULL UNIQUE,
    batch_id    TEXT    NOT NULL REFERENCES batches(id),
    verdict     TEXT    NOT NULL CHECK(verdict IN ('OK', 'NG')),
    confidence  REAL    NOT NULL,
    timestamp   TEXT    NOT NULL,
    detections  TEXT    NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_inspections_batch
    ON inspections(batch_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_inspections_timestamp
    ON inspections(timestamp);
"""


class DatabaseManager:
    """Thread-safe SQLite manager — identical API to backend/database.py."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.row_factory = sqlite3.Row
        self._apply_pragmas()
        self._create_schema()
        logger.info(f"Database ready: {db_path}")

    def _apply_pragmas(self) -> None:
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous  = NORMAL")
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _create_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_DDL)
            self._conn.commit()

    # ── Batch operations ───────────────────────────────────────────────────

    def get_active_batch(self) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, total, ng FROM batches
                WHERE is_active = 1
                ORDER BY started_at DESC LIMIT 1
                """
            ).fetchone()
        if row:
            result = dict(row)
            logger.info(
                f"Recovered active batch: id={result['id']} "
                f"total={result['total']} ng={result['ng']}"
            )
            return result
        return None

    def create_batch(self, batch_id: str, started_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO batches (id, started_at, total, ng, is_active) VALUES (?, ?, 0, 0, 1)",
                (batch_id, started_at),
            )
            self._conn.commit()
        logger.info(f"New batch created: {batch_id}")

    def close_active_batch(self, batch_id: str, ended_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE batches SET is_active = 0, ended_at = ? WHERE id = ?",
                (ended_at, batch_id),
            )
            self._conn.commit()
        logger.info(f"Batch closed: {batch_id}")

    def update_batch_counters(self, batch_id: str, total: int, ng: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE batches SET total = ?, ng = ? WHERE id = ?",
                (total, ng, batch_id),
            )
            self._conn.commit()

    # ── Inspection operations ──────────────────────────────────────────────

    def save_inspection(
        self,
        piece_id: str,
        batch_id: str,
        verdict: str,
        confidence: float,
        timestamp: str,
        detections: list,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO inspections
                    (piece_id, batch_id, verdict, confidence, timestamp, detections)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    piece_id, batch_id, verdict, confidence, timestamp,
                    json.dumps(detections),
                ),
            )
            self._conn.commit()
        logger.debug(f"Saved inspection: {piece_id} | {verdict}")

    def get_recent_inspections(self, batch_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT piece_id, verdict, confidence, timestamp, detections
                FROM   inspections
                WHERE  batch_id = ?
                ORDER  BY id DESC
                LIMIT  ?
                """,
                (batch_id, limit),
            ).fetchall()
        return [
            {**dict(row), "detections": json.loads(row["detections"])}
            for row in rows
        ]

    def get_all_batches(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, started_at, ended_at, total, ng, is_active FROM batches ORDER BY started_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        try:
            self._conn.close()
            logger.info("Database connection closed.")
        except Exception as exc:
            logger.warning(f"Error closing DB: {exc}")
