"""inspector.py — PipeInspector.

Moved from core/pipeline.py during the standard-layout refactor. Thin wrapper
that calls Detection and encodes the result as a base64 result dict; all CV
logic lives in ``pipe_inspector.vision.detection``.
"""

from __future__ import annotations

import base64
import logging

import cv2
import numpy as np
from PySide6.QtCore import QSettings

from pipe_inspector.vision.detection import Detection

logger = logging.getLogger(__name__)

# ── Tunable constants (mirror Test_1.ipynb exactly) ───────────────────────
MIN_DEFECT_AREA    = 50        # px² — ignore contours smaller than this
INNER_RADIUS_RATIO = 0.5       # fraction of pipe radius to inspect
JPEG_QUALITY       = 85        # inspection frame JPEG quality
SIGNIFICANT_AREA   = MIN_DEFECT_AREA * 10   # area → max confidence (~500 px²)


class PipeInspector:
    """
    Thin wrapper ที่เรียก Detection แล้ว encode ผลลัพธ์เป็น base64 result dict.
    CV logic ทั้งหมดอยู่ใน pipe_inspector/vision/detection.py
    """

    def __init__(
        self,
        jpeg_quality: int = JPEG_QUALITY,
        # size_classifier=None,   # ยังรองรับ interface เดิม แต่ไม่ใช้แล้ว
    ) -> None:
        self.jpeg_quality = jpeg_quality
        # ตรวจ signature ของ Detection 1 ครั้ง — รองรับทั้งตัวเก่า (defthresh_pct เดียว)
        # และตัวใหม่ 6 พารามิเตอร์ (image, size, outer_pct, outer_light_pct, inner_pct, inner_light_pct)
        import inspect as _inspect
        self._detection_new_sig = len(_inspect.signature(Detection.__init__).parameters) >= 7

    @staticmethod
    def _read_pct(settings: QSettings, ch: str, size: str, default: float) -> float:
        """อ่านค่า % ของ channel/size จาก QSettings (default ถ้าไม่มี/พัง)"""
        raw = settings.value(f"detection/{ch}/{size}", default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    def inspect(self, frame_bgr: np.ndarray, size: str = "L") -> dict:
        # 4 ค่าที่ MA ตั้งผ่าน slider (เก็บที่ QSettings, per size) → ส่งให้ Detection
        # Detection ตัวใหม่: ค่ายิ่งมาก = ยิ่งเข้มงวด (ทั้งพื้นที่และแสง)
        #   default (production ไม่ override) = ตรงกับ default slider ของ dialog:
        #     พื้นที่ 50 (กลาง), แสง 40 (≈ baseline ทีม)
        s = QSettings()
        outer_pct       = self._read_pct(s, "outer_pct",       size, 50.0)
        inner_pct       = self._read_pct(s, "inner_pct",       size, 50.0)
        outer_light_pct = self._read_pct(s, "outer_light_pct", size, 40.0)
        inner_light_pct = self._read_pct(s, "inner_light_pct", size, 40.0)

        logger.info(
            "PipeInspector: [STEP] ส่ง → Detection | size=%s | "
            "พื้นที่ outer=%.1f%% inner=%.1f%% | แสง outer=%.1f%% inner=%.1f%%",
            size, outer_pct, inner_pct, outer_light_pct, inner_light_pct,
        )

        if self._detection_new_sig:
            # ลำดับตาม requirement: image, size, outer_pct, outer_light_pct, inner_pct, inner_light_pct
            det = Detection(frame_bgr, size, outer_pct, outer_light_pct, inner_pct, inner_light_pct)
        else:
            # fallback: Detection ตัวเก่ายังรับ defthresh_pct เดียว — ใช้ inner_pct (กันพังระหว่างรอทีม sync)
            logger.warning("PipeInspector: Detection ยัง signature เดิม — fallback ใช้ inner_pct เป็น defthresh_pct")
            det = Detection(frame_bgr, size=size, defthresh_pct=inner_pct)
        vis = det.vis   # BGR image ที่ Detection วาด bbox + verdict ลงแล้ว
        verdict = det.verdict

        # แยกประเภท defect จาก flag ของทีม Detection → บันทึกลง DB ผ่าน detections
        # (getattr กัน Detection เวอร์ชันเก่าที่ยังไม่มี flag — ได้ list ว่างเหมือนเดิม)
        detections = []
        if getattr(det, "defect1", False):
            detections.append({"label": "รอยแตก", "zone": "land"})
        if getattr(det, "defect2", False):
            detections.append({"label": "เศษขี้เหล็ก", "zone": "inner"})

        # encode vis → base64 JPEG (BGR → RGB ก่อน)
        vis_rgb = cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)
        success, buffer = cv2.imencode(
            '.jpg', vis_rgb, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
        )
        if not success:
            raise RuntimeError("cv2.imencode failed.")
        image_b64 = base64.b64encode(buffer).decode('utf-8')

        logger.info(
            "PipeInspector: %s | defects=%s",
            verdict, [d["label"] for d in detections] or "-",
        )

        return {
            "verdict":        verdict,
            "detections":     detections,   # ประเภท defect (กรอบวาดลงรูป vis แล้ว)
            "image_b64":      image_b64,
            "pipe_radius_px": None,
            "detected_size":  "",
        }
