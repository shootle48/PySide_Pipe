"""
pipeline.py  (PySide6 edition)
──────────────────────────────
CV logic is 1-to-1 with FastAPI version (Test_1.ipynb mapping unchanged).

Key difference from FastAPI version:
  - CameraWorker extends QThread instead of threading.Thread
  - Results are delivered via Qt Signals instead of asyncio.Queue
  - No bridge / asyncio / WebSocket involved at all

Signal flow:
  frame_ready(np.ndarray BGR)  →  FrameWidget.set_live_frame()     ~20 fps
  result_ready(dict)           →  MainWindow._on_result()           per trigger
  status_changed(str)          →  MainWindow._on_status()           scanning/processing/idle

Result dict shape:
  {
    "verdict":    "OK" | "NG",
    "confidence": float,
    "detections": [{"label": str, "confidence": float,
                    "bbox": {"x": int, "y": int, "w": int, "h": int}}],
    "image_b64":  str,           # base64 JPEG of the inspected frame (RGB)
    "piece_id":   str,           # e.g. "BATCH-3A9F12-0042"
    "timestamp":  str,           # UTC ISO-8601
    "batch":      {"id": str, "total": int, "ng": int},
  }
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

# ── Tunable constants (mirror Test_1.ipynb exactly) ───────────────────────
MIN_DEFECT_AREA    = 50        # px² — ignore contours smaller than this
INNER_RADIUS_RATIO = 0.5       # fraction of pipe radius to inspect
JPEG_QUALITY       = 85        # inspection frame JPEG quality
SIGNIFICANT_AREA   = MIN_DEFECT_AREA * 10   # area → max confidence (~500 px²)
CAPTURE_DELAY      = 0.3       # seconds to wait after trigger (pipe settles)
TIMER_INTERVAL     = 6.0       # seconds between auto-triggers (timer mode)
STREAM_FPS         = 20        # live view frame rate cap

# ── OK Image Sampling Config ─────────────────────────────────────────────
OK_SAMPLE_EVERY_N  = 50        # ทุก N ชิ้น OK ค่อย snap 1 รูป
MAX_OK_NG_RATIO    = 1.5       # saved_OK / saved_NG ไม่เกินค่านี้ (ป้องกัน storage บวม)

# ── Camera Health Monitor ────────────────────────────────────────────────
MAX_READ_FAILURES  = 30        # consecutive fails → mark offline (~1-2 วิ)
READ_FAIL_COOLDOWN = 0.05      # sleep กัน tight-spin 100% CPU ตอน read fail


# ══════════════════════════════════════════════════════════════════════════════
# FrameBuffer — shared between _read_frames_loop and _emit_frames_loop
# ══════════════════════════════════════════════════════════════════════════════

class FrameBuffer:
    """Thread-safe container for the latest camera frame (BGR numpy array)."""

    def __init__(self) -> None:
        self._lock  = threading.Lock()
        self._frame: Optional[np.ndarray] = None

    def update(self, frame_bgr: np.ndarray) -> None:
        with self._lock:
            self._frame = frame_bgr

    def get_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._frame is not None


# ══════════════════════════════════════════════════════════════════════════════
# PipeInspector — CV logic (direct refactor of Test_1.ipynb)
# ══════════════════════════════════════════════════════════════════════════════

class PipeInspector:
    """
    Encapsulates the full OpenCV inspection pipeline from Test_1.ipynb.
    Returns a plain dict instead of a Pydantic model (no extra dependencies).
    """

    def __init__(
        self,
        min_defect_area:    int   = MIN_DEFECT_AREA,
        inner_radius_ratio: float = INNER_RADIUS_RATIO,
        jpeg_quality:       int   = JPEG_QUALITY,
    ) -> None:
        self.min_defect_area    = min_defect_area
        self.inner_radius_ratio = inner_radius_ratio
        self.jpeg_quality       = jpeg_quality

    def inspect(self, frame_bgr: np.ndarray) -> dict:
        """
        Run the full inspection pipeline on one BGR frame.
        Returns a result dict (see module docstring for shape).
        """
        circle = self._find_pipe_circle(frame_bgr)

        if circle is None:
            logger.warning("PipeInspector: pipe circle not detected.")
            return self._build_result(
                verdict="NG",
                confidence=0.0,
                detections=[{
                    "label": "pipe_not_found",
                    "confidence": 0.0,
                    "bbox": {"x": 0, "y": 0,
                             "w": frame_bgr.shape[1],
                             "h": frame_bgr.shape[0]},
                }],
                frame_bgr=frame_bgr,
            )

        cx, cy, radius = circle
        gray, mask, inner_radius = self._extract_roi(frame_bgr, cx, cy, radius)
        defect_contours = self._detect_defects(gray, mask)

        significant = [c for c in defect_contours if cv2.contourArea(c) > self.min_defect_area]
        verdict = "NG" if significant else "OK"

        logger.info(f"PipeInspector: {verdict} | defects={len(significant)} | pipe=({cx},{cy}) r={radius}")

        inner_area = np.pi * inner_radius ** 2
        detections = self._build_detections(significant)
        confidence = detections[0]["confidence"] if detections else 0.99

        return self._build_result(
            verdict=verdict,
            confidence=confidence,
            detections=detections,
            frame_bgr=frame_bgr,
        )

    # ── Private CV stages (1-to-1 with notebook cells) ────────────────────

    def _find_pipe_circle(self, frame_bgr: np.ndarray) -> Optional[tuple]:
        gray  = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        blur  = cv2.GaussianBlur(gray, (31, 31), 0)
        adaptive = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 201, 10,
        )
        circles = cv2.HoughCircles(
            adaptive, cv2.HOUGH_GRADIENT,
            dp=1, minDist=100, param1=50, param2=30,
            minRadius=50, maxRadius=300,
        )
        if circles is None:
            return None
        cx, cy, r = np.round(circles[0][0]).astype(int)
        return int(cx), int(cy), int(r)

    def _extract_roi(self, frame_bgr: np.ndarray, cx: int, cy: int, radius: int):
        gray         = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        pipe_mask = np.zeros_like(gray)
        cv2.circle(pipe_mask, (cx, cy), radius, 255, -1)
        pipe_gray = cv2.bitwise_and(gray, pipe_mask)

        blur_inner = cv2.GaussianBlur(pipe_gray, (31, 31), 0)
        adaptive_inner = cv2.adaptiveThreshold(
            blur_inner, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 201, 10,
        )
        inner_circles = cv2.HoughCircles(
            adaptive_inner, cv2.HOUGH_GRADIENT,
            dp=1, minDist=100, param1=50, param2=30,
            minRadius=20, maxRadius=0,
        )

        if inner_circles is not None:
            inner_circles = np.uint16(np.around(inner_circles))
            ix, iy, ir = min(inner_circles[0], key=lambda c: c[2])
            inner_radius = int(ir * self.inner_radius_ratio)
            center = (int(ix), int(iy))
        else:
            inner_radius = int(radius * self.inner_radius_ratio)
            center = (cx, cy)

        mask = np.zeros_like(gray)
        cv2.circle(mask, center, inner_radius, 255, thickness=-1)
        return gray, mask, inner_radius

    def _detect_defects(self, gray: np.ndarray, mask: np.ndarray) -> list:
        inside_pipe  = cv2.bitwise_and(gray, mask)
        inside_blur  = cv2.GaussianBlur(inside_pipe, (5, 5), 0)
        defect_thresh = cv2.adaptiveThreshold(
            inside_blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 201, 10,
        )
        defect_thresh = cv2.bitwise_and(defect_thresh, mask)
        kernel        = np.ones((2, 2), np.uint8)
        defect_clean  = cv2.morphologyEx(defect_thresh, cv2.MORPH_OPEN, kernel, iterations=2)
        contours, _   = cv2.findContours(defect_clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return list(contours)

    def _build_detections(self, contours: list) -> list:
        detections = []
        for contour in contours:
            area           = cv2.contourArea(contour)
            bx, by, bw, bh = cv2.boundingRect(contour)
            confidence     = round(min(0.99, 0.50 + (area / SIGNIFICANT_AREA) * 0.49), 3)
            detections.append({
                "label":      "defect",
                "confidence": confidence,
                "bbox":       {"x": int(bx), "y": int(by), "w": int(bw), "h": int(bh)},
            })
        detections.sort(key=lambda d: d["confidence"], reverse=True)
        return detections

    def _build_result(
        self,
        verdict: str,
        confidence: float,
        detections: list,
        frame_bgr: np.ndarray,
    ) -> dict:
        # BGR → RGB so the base64 JPEG is browser/Qt-compatible
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        success, buffer = cv2.imencode(
            '.jpg', frame_rgb, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
        )
        if not success:
            raise RuntimeError("cv2.imencode failed.")
        image_b64 = base64.b64encode(buffer).decode('utf-8')
        return {
            "verdict":    verdict,
            "confidence": confidence,
            "detections": detections,
            "image_b64":  image_b64,
        }


# ══════════════════════════════════════════════════════════════════════════════
# CameraWorker — QThread: capture → inspect → emit signal
# ══════════════════════════════════════════════════════════════════════════════

class CameraWorker(QThread):
    """
    Background QThread managing the full stop-and-go inspection cycle.

    Signals (connect in MainWindow):
        frame_ready(object)   — np.ndarray BGR, emitted ~20 fps for live view
        result_ready(dict)    — one result dict per trigger, for UI update
        status_changed(str)   — 'idle' | 'scanning' | 'processing'
        error_occurred(str)   — fatal error message
    """

    frame_ready           = Signal(object)   # np.ndarray BGR
    result_ready          = Signal(dict)
    status_changed        = Signal(str)
    error_occurred        = Signal(str)
    camera_health_changed = Signal(bool)     # True=online, False=offline

    def __init__(
        self,
        batch_state,
        db,
        camera_index:   int   = 0,
        trigger_mode:   str   = "manual",
        timer_interval: float = TIMER_INTERVAL,
    ) -> None:
        super().__init__()
        self._batch_state    = batch_state
        self._db             = db
        self._camera_index   = camera_index
        self._trigger_mode   = trigger_mode
        self._timer_interval = timer_interval

        self._inspector     = PipeInspector()
        self._frame_buffer  = FrameBuffer()
        self._stop_event    = threading.Event()
        self._trigger_event = threading.Event()
        self._cap: Optional[cv2.VideoCapture] = None

    # ── Public API ─────────────────────────────────────────────────────────

    def trigger(self) -> None:
        """Fire one manual inspection cycle (trigger_mode='manual' or override)."""
        logger.info("CameraWorker: manual trigger.")
        self._trigger_event.set()

    def inspect_file(self, path: str) -> None:
        """
        Inspect a static image file instead of a camera frame.
        Safe to call from any thread — Qt signals are thread-safe.
        Used in Upload mode (no camera required).
        """
        import os
        self.status_changed.emit("processing")

        frame = cv2.imread(path)
        if frame is None:
            self.error_occurred.emit(f"Cannot read image:\n{os.path.basename(path)}")
            self.status_changed.emit("idle")
            return

        logger.info(f"CameraWorker: inspecting file '{os.path.basename(path)}'")
        try:
            _t0 = time.perf_counter()
            result = self._inspector.inspect(frame)
            inference_ms = (time.perf_counter() - _t0) * 1000
            logger.info(f"CameraWorker: inference done in {inference_ms:.1f} ms")
        except Exception as exc:
            logger.error(f"CameraWorker: file inspection error: {exc}", exc_info=True)
            self.status_changed.emit("idle")
            return

        batch_snapshot = self._batch_state.increment(result["verdict"])
        piece_id       = f"{batch_snapshot['id']}-{batch_snapshot['seq']:04d}"
        timestamp      = datetime.now(timezone.utc).isoformat()

        self._db.save_inspection(
            piece_id   = piece_id,
            batch_id   = batch_snapshot["id"],
            verdict    = result["verdict"],
            confidence = result["confidence"],
            timestamp  = timestamp,
            detections = result["detections"],
            image_b64  = result.get("image_b64", "") if self._should_save_image(result["verdict"], batch_snapshot["id"]) else "",
        )
        self._db.cleanup_old_data()

        payload = {
            **result,
            "piece_id":     piece_id,
            "timestamp":    timestamp,
            "batch":        batch_snapshot,
            "inference_ms": inference_ms,
        }

        logger.info(
            f"CameraWorker: file done | "
            f"verdict={result['verdict']} | "
            f"detections={len(result['detections'])}"
        )
        self.result_ready.emit(payload)
        self.status_changed.emit("idle")

    def stop(self) -> None:
        """Signal the worker to stop. Call before closing the window."""
        logger.info("CameraWorker: stop requested.")
        self._stop_event.set()
        self._trigger_event.set()   # unblock any waiting trigger

    # ── QThread.run() ──────────────────────────────────────────────────────

    def run(self) -> None:
        logger.info(
            f"CameraWorker: starting | "
            f"camera={self._camera_index} trigger={self._trigger_mode}"
        )

        self._cap = cv2.VideoCapture(self._camera_index)
        if not self._cap.isOpened():
            msg = (
                f"Cannot open camera index {self._camera_index}. "
                # f"Run camera_check.py to find the correct index."
            )
            logger.error(msg)
            self.error_occurred.emit(msg)
            return

        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        w   = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h   = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = self._cap.get(cv2.CAP_PROP_FPS)
        logger.info(f"CameraWorker: {w}×{h} @ {fps:.0f}fps")

        # Warmup — discard first 10 frames (exposure settling)
        logger.info("CameraWorker: warming up (10 frames)...")
        for _ in range(10):
            self._cap.read()
        logger.info("CameraWorker: warmup done.")

        # ── Daemon threads for continuous frame reading and live emission ──
        read_thread = threading.Thread(
            target=self._read_frames_loop, daemon=True, name="FrameReader"
        )
        emit_thread = threading.Thread(
            target=self._emit_frames_loop, daemon=True, name="FrameEmitter"
        )
        read_thread.start()
        emit_thread.start()
        logger.info("CameraWorker: frame reader + emitter threads started.")

        # GPIO setup if requested
        if self._trigger_mode == "gpio":
            self._setup_gpio()

        self.status_changed.emit("idle")

        try:
            while not self._stop_event.is_set():
                self._wait_for_trigger()
                if self._stop_event.is_set():
                    break
                self._run_inspection_cycle()
        finally:
            self._cleanup()

    # ── Internal threads ───────────────────────────────────────────────────

    def _read_frames_loop(self) -> None:
        """
        อ่าน frame จากกล้องตลอดเวลา → เขียนลง FrameBuffer
        ทำงานใน daemon thread — ไม่ block inspection loop

        Health monitor: นับ consecutive read failures
          - เกิน MAX_READ_FAILURES → emit camera_health_changed(False) ครั้งเดียว
          - เมื่ออ่านสำเร็จหลัง offline → emit camera_health_changed(True) ครั้งเดียว
        """
        fail_count = 0
        is_offline = False

        while not self._stop_event.is_set():
            ret, frame = self._cap.read()

            if ret and frame is not None:
                self._frame_buffer.update(frame)
                if is_offline:
                    logger.info("CameraWorker: camera recovered ✅")
                    self.camera_health_changed.emit(True)
                    is_offline = False
                fail_count = 0
            else:
                fail_count += 1
                if fail_count == MAX_READ_FAILURES and not is_offline:
                    logger.warning(
                        f"CameraWorker: camera offline 🔴 "
                        f"(after {fail_count} consecutive failed reads)"
                    )
                    self.camera_health_changed.emit(False)
                    is_offline = True
                time.sleep(READ_FAIL_COOLDOWN)   # กัน tight-spin 100% CPU

    def _emit_frames_loop(self) -> None:
        """
        ส่ง frame ล่าสุดผ่าน frame_ready signal สำหรับ live view
        ส่งที่ ~STREAM_FPS fps — ป้องกัน UI ล้นด้วย frame ที่มากเกินไป
        """
        interval = 1.0 / STREAM_FPS
        while not self._stop_event.is_set():
            frame = self._frame_buffer.get_frame()
            if frame is not None:
                self.frame_ready.emit(frame)
            time.sleep(interval)

    # ── Trigger mechanisms ─────────────────────────────────────────────────

    def _wait_for_trigger(self) -> None:
        if self._trigger_mode == "timer":
            self._stop_event.wait(timeout=self._timer_interval)
        elif self._trigger_mode in ("gpio", "manual"):
            self._trigger_event.wait()
            self._trigger_event.clear()
        else:
            self._stop_event.wait(timeout=self._timer_interval)

    # ── Image save policy ──────────────────────────────────────────────────

    def _should_save_image(self, verdict: str, batch_id: str) -> bool:
        """ตัดสินใจว่าจะเก็บรูปหรือไม่:
          NG → เก็บทุกครั้ง
          OK → เก็บทุก OK_SAMPLE_EVERY_N ชิ้น แต่ต้องไม่ทำให้
               saved_OK / saved_NG > MAX_OK_NG_RATIO (ถ้าไม่มี NG เลย ก็ไม่เก็บ)
        """
        if verdict == "NG":
            return True
        # OK sampling
        ok_count = self._db.count_inspections_by_verdict(batch_id, "OK") + 1  # +1 = ตัวปัจจุบัน
        if ok_count % OK_SAMPLE_EVERY_N != 0:
            return False
        saved_ng = self._db.count_saved_images(batch_id, "NG")
        if saved_ng == 0:
            return False  # ยังไม่มี NG reference — ไม่ sample OK
        saved_ok = self._db.count_saved_images(batch_id, "OK")
        if (saved_ok + 1) / saved_ng > MAX_OK_NG_RATIO:
            logger.info(f"OK sample skipped (ratio cap): ok={saved_ok+1} ng={saved_ng}")
            return False
        logger.info(f"OK sampled at #{ok_count} (ok={saved_ok+1} ng={saved_ng})")
        return True

    # ── Inspection cycle ───────────────────────────────────────────────────

    def _run_inspection_cycle(self) -> None:
        """
        Full stop-and-go cycle:
          t=0.0s  Trigger received → status: scanning
          t=0.3s  Capture frame from FrameBuffer
          t=0.3–0.8s  CV inference
          t≈0.8s  Emit result_ready → status: idle
        """
        self.status_changed.emit("scanning")
        time.sleep(CAPTURE_DELAY)

        frame_bgr = self._frame_buffer.get_frame()
        if frame_bgr is None:
            logger.warning("CameraWorker: no frame available, skipping cycle.")
            self.status_changed.emit("idle")
            return

        logger.info(f"CameraWorker: frame captured {frame_bgr.shape[1]}×{frame_bgr.shape[0]}")
        self.status_changed.emit("processing")

        try:
            _t0 = time.perf_counter()
            result = self._inspector.inspect(frame_bgr)
            inference_ms = (time.perf_counter() - _t0) * 1000
            logger.info(f"CameraWorker: inference done in {inference_ms:.1f} ms")
        except Exception as exc:
            logger.error(f"CameraWorker: inspection error: {exc}", exc_info=True)
            self.status_changed.emit("idle")
            return

        # Increment batch counters + persist to DB
        batch_snapshot = self._batch_state.increment(result["verdict"])
        piece_id       = f"{batch_snapshot['id']}-{batch_snapshot['seq']:04d}"
        timestamp      = datetime.now(timezone.utc).isoformat()

        self._db.save_inspection(
            piece_id   = piece_id,
            batch_id   = batch_snapshot["id"],
            verdict    = result["verdict"],
            confidence = result["confidence"],
            timestamp  = timestamp,
            detections = result["detections"],
            image_b64  = result.get("image_b64", "") if self._should_save_image(result["verdict"], batch_snapshot["id"]) else "",
        )
        self._db.cleanup_old_data()

        payload = {
            **result,
            "piece_id":     piece_id,
            "timestamp":    timestamp,
            "batch":        batch_snapshot,
            "inference_ms": inference_ms,
        }

        logger.info(
            f"CameraWorker: submitted | "
            f"verdict={result['verdict']} | "
            f"detections={len(result['detections'])}"
        )

        self.result_ready.emit(payload)
        self.status_changed.emit("idle")

    # ── GPIO (Jetson production trigger) ──────────────────────────────────

    def _setup_gpio(self) -> None:
        try:
            import Jetson.GPIO as GPIO          # type: ignore
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(18, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.add_event_detect(18, GPIO.RISING, callback=self._gpio_callback, bouncetime=500)
            self._gpio = GPIO
            logger.info("CameraWorker: GPIO trigger configured on BCM pin 18.")
        except ImportError:
            logger.error("Jetson.GPIO not found — falling back to manual trigger.")
            self._trigger_mode = "manual"

    def _gpio_callback(self, channel: int) -> None:
        logger.info(f"CameraWorker: GPIO rising edge on pin {channel}.")
        self._trigger_event.set()

    # ── Cleanup ────────────────────────────────────────────────────────────

    def _cleanup(self) -> None:
        if self._cap is not None:
            self._cap.release()
            logger.info("CameraWorker: camera released.")
        if self._trigger_mode == "gpio" and hasattr(self, "_gpio"):
            self._gpio.cleanup()
            logger.info("CameraWorker: GPIO cleaned up.")
