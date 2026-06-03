"""
size_classifier.py
──────────────────
จัดประเภทขนาดชิ้นงาน (S / M / L) จาก radius ของ pipe circle ที่ PipeInspector
หาเจออยู่แล้ว — ไม่ต้องคำนวณ CV ซ้ำ

Design:
  - 3 size classes: "S", "M", "L"
  - แต่ละ class มี (rmin, rmax) — radius เป็นพิกเซล
  - radius นอก range ทุก class → "unknown" (จะถูก force NG ใน main_window)

Storage:
  - Default thresholds compile-time constant (ใช้ได้ทันทีโดยไม่ต้อง config)
  - Override ได้ผ่าน QSettings key "calibration/size_thresholds" (JSON string)
  - Phase 3 จะมี calibration UI ใน MaintenanceWidget สำหรับ capture ตัวอย่าง 3 ขนาด

Usage:
    from core.size_classifier import SizeClassifier

    cls = SizeClassifier()                  # โหลดจาก QSettings, fallback default
    size = cls.classify(radius_px=95)       # → "M"
    size = cls.classify(radius_px=300)      # → "unknown"

    # ปรับ thresholds (phase 3)
    cls.update_thresholds({"S": (50,100), "M": (100,160), "L": (160,250)})
"""

from __future__ import annotations

import json
import logging
import threading

logger = logging.getLogger(__name__)


# ── Default thresholds — คาดเดาเริ่มต้น ปรับเองได้ภายหลัง ────────────────
# (rmin, rmax) ในหน่วยพิกเซล (radius ของ outer pipe circle)
DEFAULT_SIZE_THRESHOLDS: dict[str, tuple[int, int]] = {
    "S": (40, 120),
    "M": (120, 160),
    "L": (160, 250),
}

# QSettings key สำหรับเก็บ thresholds ที่ลูกค้า calibrate เอง
QSETTINGS_KEY = "calibration/size_thresholds"

# ค่าพิเศษเมื่อขนาดไม่อยู่ใน range ใดเลย
UNKNOWN_SIZE = "unknown"


class SizeClassifier:
    """
    จำแนกขนาดชิ้นงานเป็น "S" / "M" / "L" / "unknown" จาก radius พิกเซล

    Thread-safe สำหรับ classify (read-only); update_thresholds ควรเรียก
    จาก main thread เท่านั้น (เขียน QSettings)
    """

    def __init__(self, thresholds: dict[str, tuple[int, int]] | None = None) -> None:
        """
        Args:
            thresholds: ถ้าระบุ → ใช้ค่านี้
                        ถ้า None → ลองโหลดจาก QSettings, fallback DEFAULT
        """
        self._lock = threading.Lock()
        if thresholds is not None:
            self._thresholds = self._validate(thresholds)
            logger.info(f"SizeClassifier: using injected thresholds {self._thresholds}")
        else:
            self._thresholds = self._load_from_qsettings()

    # ── Public API ────────────────────────────────────────────────────────

    def classify(self, radius_px: int | None) -> str:
        """
        จำแนก radius เป็น size class

        Args:
            radius_px: ค่า radius ในพิกเซล (จาก PipeInspector._find_pipe_circle)
                       None ก็ได้ — return "unknown"

        Returns:
            "S" / "M" / "L" / "unknown"
        """
        if radius_px is None or radius_px <= 0:
            return UNKNOWN_SIZE

        with self._lock:
            for size, (rmin, rmax) in self._thresholds.items():
                if rmin <= radius_px <= rmax:
                    return size

        return UNKNOWN_SIZE

    def get_thresholds(self) -> dict[str, tuple[int, int]]:
        """คืน thresholds ปัจจุบัน (copy เพื่อไม่ให้ caller แก้โดยบังเอิญ)"""
        with self._lock:
            return dict(self._thresholds)

    def update_thresholds(self, new_thresholds: dict[str, tuple[int, int]]) -> None:
        """
        Override thresholds + persist ไป QSettings

        Args:
            new_thresholds: dict ของ size→(rmin, rmax)
                            ต้องมี key "S", "M", "L" ครบ และ rmin < rmax
        """
        validated = self._validate(new_thresholds)
        with self._lock:
            self._thresholds = validated
        self._save_to_qsettings(validated)
        logger.info(f"SizeClassifier: thresholds updated → {validated}")

    def reset_to_defaults(self) -> None:
        """กลับไปใช้ DEFAULT_SIZE_THRESHOLDS + เคลียร์ QSettings"""
        self._thresholds = dict(DEFAULT_SIZE_THRESHOLDS)
        self._save_to_qsettings(self._thresholds)
        logger.info("SizeClassifier: reset to defaults")

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _validate(thresholds: dict) -> dict[str, tuple[int, int]]:
        """ตรวจ thresholds ว่ามี S/M/L ครบ และ rmin < rmax"""
        result: dict[str, tuple[int, int]] = {}
        for size in ("S", "M", "L"):
            if size not in thresholds:
                raise ValueError(f"size_thresholds missing key '{size}'")
            pair = thresholds[size]
            if (
                not isinstance(pair, (tuple, list))
                or len(pair) != 2
                or not all(isinstance(v, int) for v in pair)
            ):
                raise ValueError(
                    f"size_thresholds['{size}'] must be (rmin, rmax) ints, got {pair!r}"
                )
            rmin, rmax = int(pair[0]), int(pair[1])
            if rmin >= rmax or rmin < 0:
                raise ValueError(
                    f"size_thresholds['{size}']: invalid range ({rmin}, {rmax}); "
                    f"need 0 <= rmin < rmax"
                )
            result[size] = (rmin, rmax)
        return result

    @staticmethod
    def _load_from_qsettings() -> dict[str, tuple[int, int]]:
        """ลอง load จาก QSettings, fallback ใช้ DEFAULT"""
        try:
            from PySide6.QtCore import QSettings
            settings = QSettings()
            raw = settings.value(QSETTINGS_KEY, "")
            if not raw:
                logger.info(
                    f"SizeClassifier: no saved thresholds, using defaults "
                    f"{DEFAULT_SIZE_THRESHOLDS}"
                )
                return dict(DEFAULT_SIZE_THRESHOLDS)
            parsed = json.loads(raw)
            # JSON เก็บ list แทน tuple → แปลงกลับ
            converted = {k: tuple(v) for k, v in parsed.items()}
            validated = SizeClassifier._validate(converted)
            logger.info(f"SizeClassifier: loaded thresholds {validated}")
            return validated
        except (ImportError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                f"SizeClassifier: failed to load thresholds ({exc}), using defaults"
            )
            return dict(DEFAULT_SIZE_THRESHOLDS)

    @staticmethod
    def _save_to_qsettings(thresholds: dict[str, tuple[int, int]]) -> None:
        """Persist thresholds ลง QSettings (เป็น JSON string)"""
        try:
            from PySide6.QtCore import QSettings
            settings = QSettings()
            # แปลง tuple → list สำหรับ JSON
            payload = {k: list(v) for k, v in thresholds.items()}
            settings.setValue(QSETTINGS_KEY, json.dumps(payload))
        except (ImportError, TypeError) as exc:
            logger.error(f"SizeClassifier: failed to save thresholds: {exc}")
