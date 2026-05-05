"""
batch_state.py  (PySide6 edition — identical logic to FastAPI version)
──────────────
Thread-safe in-memory batch counter with SQLite write-through.
API is 1-to-1 with backend/batch_state.py.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class _BatchData:
    batch_id: str
    seq: int            = 0   # sequence counter สำหรับสร้าง piece_id (ใช้ MAX จาก DB)
    total: int          = 0   # display counter (ใช้ COUNT จาก DB)
    ng: int             = 0
    expected_total: int = 0   # จำนวนชิ้นที่คาดหวังใน batch นี้ (user input)
    expected_size: str  = ""  # ขนาดที่ล็อคใน batch นี้ ("S"/"M"/"L"/"" = ไม่ล็อค)


class BatchStateManager:
    """
    Owns batch counter state with persistent DB write-through.

    increment(verdict) → dict
    reset()            → dict
    get_state()        → dict

    All methods thread-safe.
    """

    def __init__(self, db=None) -> None:
        self._db = db
        self._lock = threading.Lock()
        self._data = self._initialize()

    def _initialize(self) -> _BatchData:
        if self._db is not None:
            recovered = self._db.get_active_batch()
            if recovered:
                max_seq = self._db.get_max_piece_seq(recovered["id"])
                logger.info(
                    f"Recovered batch {recovered['id']} "
                    f"(total={recovered['total']}, ng={recovered['ng']}, seq={max_seq})"
                )
                return _BatchData(
                    batch_id=recovered["id"],
                    seq=max_seq,
                    total=recovered["total"],
                    ng=recovered["ng"],
                    expected_total=recovered.get("expected_total", 0),
                    expected_size=recovered.get("expected_size", "") or "",
                )

        new_id = self._generate_batch_id()
        now = datetime.now(timezone.utc).isoformat()
        if self._db is not None:
            self._db.create_batch(new_id, now)
        logger.info(f"New batch started: {new_id}")
        return _BatchData(batch_id=new_id)

    def increment(self, verdict: str) -> dict:
        with self._lock:
            self._data.seq   += 1
            self._data.total += 1
            if verdict == "NG":
                self._data.ng += 1
            snapshot = self._snapshot()

        if self._db is not None:
            try:
                self._db.update_batch_counters(snapshot["id"], snapshot["total"], snapshot["ng"])
            except Exception as exc:
                logger.error(
                    f"DB write failed (counter will be corrected on next sync): {exc}",
                    exc_info=True,
                )
        return snapshot

    def reset(self, expected_total: int = 0, expected_size: str = "") -> dict:
        now = datetime.now(timezone.utc).isoformat()
        new_id = self._generate_batch_id()
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
            f"Batch reset: {old_id} → {new_id} "
            f"(expected={expected_total} size={expected_size!r})"
        )
        return snapshot

    def set_expected_total(self, expected_total: int) -> dict:
        """แก้ expected_total ของ batch ปัจจุบัน (โดยไม่ reset)."""
        with self._lock:
            self._data.expected_total = expected_total
            if self._db is not None:
                self._db.update_expected_total(self._data.batch_id, expected_total)
            return self._snapshot()

    def get_state(self) -> dict:
        with self._lock:
            return self._snapshot()

    def sync_from_db(self) -> dict:
        """Reload total/ng จาก DB สำหรับ batch ปัจจุบัน.
        ใช้หลังจาก external code (เช่น DbViewer) ลบ record ออกจาก DB."""
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

    def _snapshot(self) -> dict:
        return {
            "id":             self._data.batch_id,
            "seq":            self._data.seq,
            "expected_total": self._data.expected_total,
            "expected_size":  self._data.expected_size,
            "total": self._data.total,
            "ng":    self._data.ng,
        }

    @staticmethod
    def _generate_batch_id() -> str:
        return f"BATCH-{uuid.uuid4().hex[:6].upper()}"
