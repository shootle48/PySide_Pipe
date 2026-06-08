"""
batch_state.py
──────────────
Thread-safe in-memory batch counter with SQLite write-through.

The counter always reflects reality even if the DB write fails — the DB will
be re-synced on the next ``sync_from_db()`` call (e.g. after app restart).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime

from core.utils import utcnow_iso

logger = logging.getLogger(__name__)


# ── Module-level helpers ───────────────────────────────────────────────────

def _make_batch_id(size: str = "") -> str:
    """Generate a human-readable batch ID: {size}_{YYYYMMDD}_{HHMMSS}

    ตัวอย่าง: "M_20260525_143052"  (size M, 25 May 2026, 14:30:52 local time)
    ถ้า size ว่าง (startup placeholder): "20260525_143052"

    ใช้ระดับวินาที (เดิมเป็นนาที) — กัน reset 2 ครั้งในนาทีเดียวกันแล้ว ID ชนกัน
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{size}_{ts}" if size else ts


# ── Internal data class ────────────────────────────────────────────────────

@dataclass
class _BatchData:
    batch_id:       str
    total:          int = 0    # display counter (source of truth: in-memory)
    ng:             int = 0
    expected_total: int = 0    # target pieces for this batch (user-set)
    expected_size:  str = ""   # locked size class "S"/"M"/"L"/"" = unlocked


# ── Public class ───────────────────────────────────────────────────────────

class BatchStateManager:
    """Owns batch counter state with persistent DB write-through.

    Thread-safe for all public methods.  The DB is written *after* the
    in-memory update so a DB failure never corrupts the in-memory counter.

    Public API::

        mgr = BatchStateManager(db=db_manager)
        state = mgr.increment("NG")   # → {"id": ..., "total": 1, "ng": 1, ...}
        state = mgr.reset()
        state = mgr.get_state()
    """

    def __init__(self, db=None) -> None:
        self._db   = db
        self._lock = threading.Lock()
        self._data = self._initialize()

    def _unique_batch_id(self, size: str) -> str:
        """สร้าง batch id ที่ไม่ชนกับของเดิมใน DB (เผื่อชนวินาทีเดียวกันข้าม session)"""
        base = _make_batch_id(size)
        if self._db is None or not self._db.batch_exists(base):
            return base
        n = 2
        while self._db.batch_exists(f"{base}_{n}"):
            n += 1
        return f"{base}_{n}"

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def _initialize(self) -> _BatchData:
        """Recover an active batch from DB, or create a new one."""
        if self._db is not None:
            recovered = self._db.get_active_batch()
            if recovered:
                logger.info(
                    "Recovered batch %s (total=%d ng=%d)",
                    recovered["id"], recovered["total"], recovered["ng"],
                )
                return _BatchData(
                    batch_id=recovered["id"],
                    total=recovered["total"],
                    ng=recovered["ng"],
                    expected_total=recovered.get("expected_total", 0),
                    expected_size=recovered.get("expected_size", "") or "",
                )

        new_id = self._unique_batch_id("")   # startup placeholder — no size yet
        if self._db is not None:
            self._db.create_batch(new_id, utcnow_iso())
        logger.info("New batch started: %s", new_id)
        return _BatchData(batch_id=new_id)

    # ── Mutators ───────────────────────────────────────────────────────────

    def increment(self, verdict: str) -> dict:
        """Record one inspection result.

        In-memory counter updates *before* the DB write so a DB failure
        never causes the UI counter to fall behind.
        """
        with self._lock:
            self._data.total += 1
            if verdict == "NG":
                self._data.ng += 1
            snapshot = self._snapshot()

        # DB write outside the lock — failure is logged but not fatal
        if self._db is not None:
            try:
                self._db.update_batch_counters(
                    snapshot["id"], snapshot["total"], snapshot["ng"]
                )
            except sqlite3.Error as exc:
                logger.error(
                    "DB counter write failed for batch %s: %s "
                    "(will re-sync on next sync_from_db call)",
                    snapshot["id"], exc,
                )
        return snapshot

    def reset(self, expected_total: int = 0, expected_size: str = "") -> dict:
        """Close the current batch and open a new one."""
        new_id = self._unique_batch_id(expected_size)   # e.g. "M_20260525_143052"
        now    = utcnow_iso()
        with self._lock:
            old_id = self._data.batch_id
            if self._db is not None:
                self._db.close_active_batch(old_id, now)
                self._db.create_batch(new_id, now, expected_total, expected_size)
            self._data = _BatchData(
                batch_id=new_id,
                expected_total=expected_total,
                expected_size=expected_size,
            )
            snapshot = self._snapshot()
        logger.info(
            "Batch reset: %s → %s (expected=%d size=%r)",
            old_id, new_id, expected_total, expected_size,
        )
        return snapshot

    def set_expected_total(self, expected_total: int) -> dict:
        """Update the target piece count for the current batch without resetting."""
        with self._lock:
            self._data.expected_total = expected_total
            if self._db is not None:
                self._db.update_expected_total(self._data.batch_id, expected_total)
            return self._snapshot()

    # ── Accessors ──────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        """Return a snapshot of the current batch state (thread-safe read)."""
        with self._lock:
            return self._snapshot()

    def sync_from_db(self) -> dict:
        """Reload counters from DB for the current batch.

        Use this after external code (e.g. DbViewer) deletes records so the
        in-memory counters match the DB again.
        """
        if self._db is None:
            return self.get_state()
        with self._lock:
            recovered = self._db.get_active_batch()
            if recovered and recovered["id"] == self._data.batch_id:
                self._data.total          = recovered["total"]
                self._data.ng             = recovered["ng"]
                self._data.expected_total = recovered.get("expected_total", 0)
                self._data.expected_size  = recovered.get("expected_size", "") or ""
            return self._snapshot()

    # ── Internal helpers ───────────────────────────────────────────────────

    def _snapshot(self) -> dict:
        """Return a plain-dict copy of current state (caller must hold lock or be reading atomically)."""
        return {
            "id":             self._data.batch_id,
            "total":          self._data.total,
            "ng":             self._data.ng,
            "expected_total": self._data.expected_total,
            "expected_size":  self._data.expected_size,
        }
