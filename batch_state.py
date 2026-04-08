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
    total: int = 0
    ng: int = 0


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
                logger.info(
                    f"Recovered batch {recovered['id']} "
                    f"(total={recovered['total']}, ng={recovered['ng']})"
                )
                return _BatchData(
                    batch_id=recovered["id"],
                    total=recovered["total"],
                    ng=recovered["ng"],
                )

        new_id = self._generate_batch_id()
        now = datetime.now(timezone.utc).isoformat()
        if self._db is not None:
            self._db.create_batch(new_id, now)
        logger.info(f"New batch started: {new_id}")
        return _BatchData(batch_id=new_id)

    def increment(self, verdict: str) -> dict:
        with self._lock:
            self._data.total += 1
            if verdict == "NG":
                self._data.ng += 1
            snapshot = self._snapshot()

        if self._db is not None:
            self._db.update_batch_counters(snapshot["id"], snapshot["total"], snapshot["ng"])
        return snapshot

    def reset(self) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        new_id = self._generate_batch_id()
        with self._lock:
            old_id = self._data.batch_id
            if self._db is not None:
                self._db.close_active_batch(old_id, now)
                self._db.create_batch(new_id, now)
            self._data = _BatchData(batch_id=new_id)
            snapshot = self._snapshot()
        logger.info(f"Batch reset: {old_id} → {new_id}")
        return snapshot

    def get_state(self) -> dict:
        with self._lock:
            return self._snapshot()

    def _snapshot(self) -> dict:
        return {"id": self._data.batch_id, "total": self._data.total, "ng": self._data.ng}

    @staticmethod
    def _generate_batch_id() -> str:
        return f"BATCH-{uuid.uuid4().hex[:6].upper()}"
