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
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "pipe_inspector.db"

# ── Storage Threshold Config ───────────────────────────────────────────────
MAX_RECORD_AGE_DAYS = 90     # ลบ record ที่เก่ากว่า 3 เดือน
MAX_DB_SIZE_MB      = 32_768  # 32 GB — ถ้า DB ใหญ่กว่านี้ → cleanup
CLEANUP_KEEP_RATIO  = 0.60   # เก็บ 60% ใหม่สุด, ลบ 40% เก่าสุด
CLEANUP_INTERVAL_S  = 300    # minimum seconds between cleanup runs (throttle)

_DDL = """
CREATE TABLE IF NOT EXISTS batches (
    id             TEXT    PRIMARY KEY,
    started_at     TEXT    NOT NULL,
    ended_at       TEXT,
    total          INTEGER NOT NULL DEFAULT 0,
    ng             INTEGER NOT NULL DEFAULT 0,
    is_active      INTEGER NOT NULL DEFAULT 1,
    expected_total INTEGER NOT NULL DEFAULT 0,
    expected_size  TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS inspections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    piece_id      TEXT    NOT NULL UNIQUE,
    batch_id      TEXT    NOT NULL REFERENCES batches(id),
    verdict       TEXT    NOT NULL CHECK(verdict IN ('OK', 'NG')),
    confidence    REAL    NOT NULL,
    timestamp     TEXT    NOT NULL,
    detections    TEXT    NOT NULL DEFAULT '[]',
    image_b64     TEXT,
    detected_size TEXT    NOT NULL DEFAULT ''
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
        self._last_cleanup_at: float = 0.0   # throttle: last time cleanup_old_data ran
        self._apply_pragmas()
        self._create_schema()
        logger.info(f"Database ready: {db_path}")

    def _apply_pragmas(self) -> None:
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous  = NORMAL")
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _add_column_if_missing(self, table: str, column: str, col_def: str) -> None:
        """Run ALTER TABLE … ADD COLUMN, silently ignore 'already exists' errors."""
        try:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            self._conn.commit()
            logger.info("Migration: added %s.%s.", table, column)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "duplicate column" not in msg and "already exists" not in msg:
                logger.error("Migration failed unexpectedly: %s", exc)
                raise

    def _create_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_DDL)
            self._add_column_if_missing("inspections", "image_b64",     "TEXT")
            self._add_column_if_missing("batches",     "expected_total", "INTEGER NOT NULL DEFAULT 0")
            self._add_column_if_missing("batches",     "expected_size",  "TEXT NOT NULL DEFAULT ''")
            self._add_column_if_missing("inspections", "detected_size",  "TEXT NOT NULL DEFAULT ''")

    # ── Batch operations ───────────────────────────────────────────────────

    def get_next_piece_number(self) -> int:
        """คืน piece number ถัดไป = จำนวน inspection ทั้งหมดใน DB + 1"""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM inspections").fetchone()
        return (row[0] or 0) + 1

    def get_active_batch(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, total, ng, expected_total, expected_size FROM batches
                WHERE is_active = 1
                ORDER BY started_at DESC LIMIT 1
                """
            ).fetchone()
        if row:
            result = dict(row)
            logger.info(
                f"Recovered active batch: id={result['id']} "
                f"total={result['total']} ng={result['ng']} "
                f"expected={result['expected_total']} size={result['expected_size']!r}"
            )
            return result
        return None

    def create_batch(
        self,
        batch_id: str,
        started_at: str,
        expected_total: int = 0,
        expected_size: str = "",
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO batches "
                "(id, started_at, total, ng, is_active, expected_total, expected_size) "
                "VALUES (?, ?, 0, 0, 1, ?, ?)",
                (batch_id, started_at, expected_total, expected_size),
            )
            self._conn.commit()
        logger.info(
            f"New batch created: {batch_id} "
            f"(expected={expected_total} size={expected_size!r})"
        )

    def update_expected_total(self, batch_id: str, expected_total: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE batches SET expected_total = ? WHERE id = ?",
                (expected_total, batch_id),
            )
            self._conn.commit()
        logger.info(f"Updated expected_total for {batch_id}: {expected_total}")

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
        timestamp: str,
        detections: list,
        image_b64: str = "",
        detected_size: str = "",
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO inspections
                    (piece_id, batch_id, verdict, confidence, timestamp,
                     detections, image_b64, detected_size)
                VALUES (?, ?, ?, 0.0, ?, ?, ?, ?)
                """,
                (
                    piece_id, batch_id, verdict, timestamp,
                    json.dumps(detections), image_b64, detected_size,
                ),
            )
            self._conn.commit()
        logger.debug(f"Saved inspection: {piece_id} | {verdict} | size={detected_size!r}")

    def get_recent_inspections(self, batch_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT piece_id, verdict, timestamp,
                       detections, image_b64, detected_size
                FROM   inspections
                WHERE  batch_id = ?
                ORDER  BY id DESC
                LIMIT  ?
                """,
                (batch_id, limit),
            ).fetchall()
        return [
            {**dict(row), "detections": self._safe_json_loads(row["detections"])}
            for row in rows
        ]

    def get_all_inspections_with_images(self) -> list[dict]:
        """ดึง inspection ทั้งหมดที่มีรูป (NG) ข้ามทุก batch — สำหรับ Dataset Export"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT piece_id, batch_id, verdict, timestamp,
                       detections, image_b64, detected_size
                FROM   inspections
                WHERE  image_b64 IS NOT NULL AND image_b64 != ''
                ORDER  BY timestamp ASC
                """
            ).fetchall()
        return [
            {**dict(row), "detections": self._safe_json_loads(row["detections"])}
            for row in rows
        ]

    @staticmethod
    def _safe_json_loads(raw: str) -> list:
        try:
            return json.loads(raw) if raw else []
        except json.JSONDecodeError:
            logger.warning("DB: corrupt detections JSON, returning empty list")
            return []

    def get_all_batches(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, started_at, ended_at, total, ng, is_active, "
                "expected_total, expected_size "
                "FROM batches ORDER BY started_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def count_inspections_by_verdict(self, batch_id: str, verdict: str) -> int:
        """นับจำนวน inspection ของ batch นี้ตาม verdict (OK/NG)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM inspections WHERE batch_id = ? AND verdict = ?",
                (batch_id, verdict),
            ).fetchone()
        return int(row["c"] or 0)

    def count_saved_images(self, batch_id: str, verdict: str) -> int:
        """นับจำนวน record ของ batch ที่บันทึกรูปจริง (image_b64 != '')."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM inspections "
                "WHERE batch_id = ? AND verdict = ? "
                "AND image_b64 IS NOT NULL AND image_b64 != ''",
                (batch_id, verdict),
            ).fetchone()
        return int(row["c"] or 0)

    # ── CRUD — Delete / Update ─────────────────────────────────────────────

    def delete_inspection(self, piece_id: str) -> None:
        """ลบ inspection record เดียว (รวมรูป)"""
        with self._lock:
            self._conn.execute(
                "DELETE FROM inspections WHERE piece_id = ?", (piece_id,)
            )
            self._conn.commit()
        logger.info(f"Deleted inspection: {piece_id}")

    def recalculate_batch_counters(self, batch_id: str) -> tuple[int, int]:
        """Recount total/NG จาก inspections จริง แล้ว sync กลับ batches table.
        Returns (total, ng) ใหม่."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN verdict = 'NG' THEN 1 ELSE 0 END) AS ng
                FROM inspections WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
            total = row["total"] or 0
            ng    = int(row["ng"] or 0)
            self._conn.execute(
                "UPDATE batches SET total = ?, ng = ? WHERE id = ?",
                (total, ng, batch_id),
            )
            self._conn.commit()
        logger.info(f"Recalculated batch {batch_id}: total={total} ng={ng}")
        return total, ng

    def delete_batch_inspections(self, batch_id: str) -> None:
        """ลบทุก inspection record และ batch record ออกทั้งหมด"""
        with self._lock:
            deleted = self._conn.execute(
                "DELETE FROM inspections WHERE batch_id = ?", (batch_id,)
            ).rowcount
            self._conn.execute(
                "DELETE FROM batches WHERE id = ?", (batch_id,)
            )
            self._conn.commit()
        logger.info(f"Deleted batch {batch_id} and {deleted} inspections")

    def clear_image_b64(self, piece_id: str) -> None:
        """เคลียร์เฉพาะรูป — เก็บ metadata (verdict/timestamp/detections) ไว้"""
        with self._lock:
            self._conn.execute(
                "UPDATE inspections SET image_b64 = '' WHERE piece_id = ?", (piece_id,)
            )
            self._conn.commit()
        logger.info(f"Cleared image for: {piece_id}")

    # ── Storage cleanup ────────────────────────────────────────────────────

    def cleanup_old_data(self) -> None:
        """
        เรียกหลัง save_inspection() ทุกครั้ง — throttled ด้วย CLEANUP_INTERVAL_S.

        Rule 1 — Age  : ลบ record เก่ากว่า MAX_RECORD_AGE_DAYS
        Rule 2 — Size : ถ้า DB > MAX_DB_SIZE_MB → ลบ 40% เก่าสุด เก็บ 60% ใหม่สุด
        Recalculates batch counters only when at least one record was actually deleted.
        """
        now = time.time()
        if now - self._last_cleanup_at < CLEANUP_INTERVAL_S:
            return   # เรียกบ่อย แต่ทำจริงทุก 5 นาทีเท่านั้น
        self._last_cleanup_at = now

        age_deleted  = self._cleanup_by_age()
        size_deleted = self._cleanup_by_size()
        if age_deleted or size_deleted:
            self._recalculate_all_batch_counters()

    def _recalculate_all_batch_counters(self) -> None:
        """Sync batches.total / batches.ng ให้ตรงกับ inspections จริงหลัง cleanup."""
        with self._lock:
            self._conn.execute(
                """
                UPDATE batches SET
                    total = (SELECT COUNT(*)
                             FROM inspections WHERE batch_id = batches.id),
                    ng    = (SELECT COUNT(*)
                             FROM inspections
                             WHERE batch_id = batches.id AND verdict = 'NG')
                """
            )
            self._conn.commit()
        logger.info("Recalculated all batch counters after cleanup.")

    def _cleanup_by_age(self) -> bool:
        """Returns True if any records were deleted."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=MAX_RECORD_AGE_DAYS)
        ).isoformat()
        with self._lock:
            deleted = self._conn.execute(
                "DELETE FROM inspections WHERE timestamp < ?", (cutoff,)
            ).rowcount
            if deleted:
                self._conn.commit()
                logger.info(
                    f"Cleanup [age]: removed {deleted} records "
                    f"older than {MAX_RECORD_AGE_DAYS} days."
                )
                return True
        return False

    def _cleanup_by_size(self) -> bool:
        """Returns True if any records were deleted."""
        db_mb = self._db_path.stat().st_size / (1024 * 1024)
        if db_mb <= MAX_DB_SIZE_MB:
            return False

        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM inspections"
            ).fetchone()[0]
            delete_count = int(total * (1 - CLEANUP_KEEP_RATIO))   # 40%
            if delete_count <= 0:
                return False
            self._conn.execute(
                """
                DELETE FROM inspections WHERE id IN (
                    SELECT id FROM inspections ORDER BY id ASC LIMIT ?
                )
                """,
                (delete_count,),
            )
            self._conn.commit()
            logger.info(
                f"Cleanup [size]: DB was {db_mb:.1f} MB, "
                f"removed {delete_count} oldest records (kept {CLEANUP_KEEP_RATIO:.0%})."
            )
        # VACUUM reclaims disk space but blocks the connection — run off the main thread
        threading.Thread(
            target=self._vacuum, daemon=True, name="DBVacuum"
        ).start()
        return True

    def _vacuum(self) -> None:
        """Run VACUUM in a daemon thread — reclaims disk space without blocking callers."""
        try:
            with self._lock:
                self._conn.execute("VACUUM")
            logger.info("VACUUM complete.")
        except sqlite3.Error as exc:
            logger.warning("VACUUM failed: %s", exc)

    def close(self) -> None:
        try:
            self._conn.close()
            logger.info("Database connection closed.")
        except sqlite3.Error as exc:
            logger.warning(f"Error closing DB: {exc}")
