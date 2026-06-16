"""camera_worker.py — FrameBuffer + CameraWorker (QThread).

Moved from core/pipeline.py during the standard-layout refactor. Behaviour is
unchanged: QThread managing the full stop-and-go inspection cycle
(capture → inspect → persist → emit Qt signals).

Signal flow:
  frame_ready(np.ndarray BGR)  →  FrameWidget.set_live_frame()     ~20 fps
  result_ready(dict)           →  MainWindow._on_result()           per trigger
  status_changed(str)          →  MainWindow._on_status()           scanning/processing/idle
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

from pipe_inspector.domain.enums import TriggerMode, Verdict, WorkerStatus
from pipe_inspector.utils.timefmt import utcnow_iso
from pipe_inspector.vision.inspector import PipeInspector

logger = logging.getLogger(__name__)

# ── Tunable constants ──────────────────────────────────────────────────────
CAPTURE_DELAY      = 0.3       # seconds to wait after trigger (pipe settles)
STREAM_FPS         = 20        # live view frame rate cap

# ── OK Image Sampling Config ─────────────────────────────────────────────
OK_SAMPLE_EVERY_N  = 10        # ทุก N ชิ้น OK ค่อย snap 1 รูป
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
        self._frame: np.ndarray | None = None

    def update(self, frame_bgr: np.ndarray) -> None:
        with self._lock:
            self._frame = frame_bgr

    def get_frame(self) -> np.ndarray | None:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._frame is not None


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
        camera_index:   int         = 0,
        trigger_mode:   TriggerMode = TriggerMode.MANUAL,
        timer_interval: float       = 6.0,   # fallback; MainWindow passes the Settings value
        size_classifier=None,   # Optional[SizeClassifier]; None = skip size detection
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
        self._cap: cv2.VideoCapture | None = None

    # ── Size validation helper ─────────────────────────────────────────────

    def _apply_size_check(self, result: dict, frame_shape: tuple) -> None:
        """
        ถ้า batch ปัจจุบันล็อคขนาดไว้ (expected_size) แต่ขนาดที่ตรวจได้
        (detected_size) ไม่ตรง → force verdict=NG + เพิ่ม label size_mismatch.

        Mutates `result` in-place. ทำก่อน batch_state.increment() เสมอ
        เพื่อให้ counter + DB เห็น verdict ที่แก้แล้ว.

        Backward compat:
          - expected_size = "" → ไม่ตรวจ (batch ไม่ได้ล็อคขนาด)
          - detected_size = "" → ไม่ตรวจ (size_classifier ไม่ได้ enable)
        """
        state = self._batch_state.get_state()
        expected = state.get("expected_size", "") or ""
        detected = result.get("detected_size", "") or ""

        if not expected or not detected:
            return   # ไม่ trigger validation

        if detected == expected:
            return   # match

        # Mismatch — force NG + add size_mismatch detection label
        h, w = frame_shape[:2]
        if result["verdict"] == Verdict.OK:
            result["verdict"] = Verdict.NG
        result["detections"].append({
            "label": "size_mismatch",
            "bbox":  {"x": 0, "y": 0, "w": int(w), "h": int(h)},
        })
        logger.warning(
            f"CameraWorker: size mismatch — expected={expected} "
            f"detected={detected} → force NG"
        )

    # ── Public API ─────────────────────────────────────────────────────────

    def trigger(self) -> None:
        """Fire one manual inspection cycle (trigger_mode='manual' or override)."""
        logger.info("CameraWorker: manual trigger.")
        self._trigger_event.set()

    def inspect_file(self, path: str) -> None:
        """Inspect a static image file instead of a live camera frame.

        Safe to call from any thread (Qt signals are thread-safe).
        Used in Upload mode when no camera is needed.
        """
        self.status_changed.emit(WorkerStatus.PROCESSING)

        frame = cv2.imread(path)
        if frame is None:
            self.error_occurred.emit(f"Cannot read image:\n{os.path.basename(path)}")
            self.status_changed.emit(WorkerStatus.IDLE)
            return

        logger.info("CameraWorker: inspecting file '%s'", os.path.basename(path))
        result = self._run_cv_inference(frame)
        if result is None:
            self.status_changed.emit(WorkerStatus.IDLE)
            return

        self._apply_size_check(result, frame.shape)
        payload = self._persist_result(result)
        logger.info(
            "CameraWorker: file done | verdict=%s | detections=%d",
            result["verdict"], len(result["detections"]),
        )
        self.result_ready.emit(payload)
        self.status_changed.emit(WorkerStatus.IDLE)

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

        t0 = time.perf_counter()
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
        logger.info("CameraWorker: %dx%d @ %.0ffps | open=%.0f ms",
                    w, h, fps, (time.perf_counter() - t0) * 1000)

        self.camera_health_changed.emit(True)

        # Warmup — discard first 10 frames (exposure settling)
        logger.info("CameraWorker: warming up (10 frames)...")
        t0 = time.perf_counter()
        for _ in range(10):
            self._cap.read()
        logger.info("CameraWorker: warmup done in %.0f ms", (time.perf_counter() - t0) * 1000)

        # ── Daemon threads for continuous frame reading and live emission ──
        # เก็บ ref ไว้ join ตอน _cleanup ก่อน release กล้อง (กัน release ขณะ cap.read())
        self._reader_thread = threading.Thread(
            target=self._read_frames_loop, daemon=True, name="FrameReader"
        )
        self._emitter_thread = threading.Thread(
            target=self._emit_frames_loop, daemon=True, name="FrameEmitter"
        )
        self._reader_thread.start()
        self._emitter_thread.start()
        logger.info("CameraWorker: frame reader + emitter threads started.")

        if self._trigger_mode is TriggerMode.GPIO:
            self._setup_gpio()

        self.status_changed.emit(WorkerStatus.IDLE)

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
                # wait() returns True when stop is signalled → exits faster than sleep()
                self._stop_event.wait(timeout=READ_FAIL_COOLDOWN)

    def _emit_frames_loop(self) -> None:
        """
        ส่ง frame ล่าสุดผ่าน frame_ready signal สำหรับ live view
        ส่งที่ ~STREAM_FPS fps — ป้องกัน UI ล้นด้วย frame ที่มากเกินไป
        """
        interval = 1.0 / STREAM_FPS
        # wait() returns False on timeout → emit frame; True on stop signal → exit
        while not self._stop_event.wait(timeout=interval):
            frame = self._frame_buffer.get_frame()
            if frame is not None:
                self.frame_ready.emit(frame)

    # ── Trigger mechanisms ─────────────────────────────────────────────────

    def _wait_for_trigger(self) -> None:
        if self._trigger_mode is TriggerMode.TIMER:
            self._stop_event.wait(timeout=self._timer_interval)
        elif self._trigger_mode in (TriggerMode.GPIO, TriggerMode.MANUAL):
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

    # ── Inspection cycle (broken into three focused methods) ─────────────────

    def _capture_frame(self) -> np.ndarray | None:
        """Wait for pipe to settle then grab the latest frame.

        Returns None if the buffer is empty or stop was requested during the delay.
        """
        self.status_changed.emit(WorkerStatus.SCANNING)
        # Event.wait() returns True when stop is signalled — exit early
        if self._stop_event.wait(timeout=CAPTURE_DELAY):
            return None

        frame = self._frame_buffer.get_frame()
        if frame is None:
            logger.warning("CameraWorker: no frame in buffer — skipping cycle.")
        else:
            logger.info("CameraWorker: frame captured %dx%d", frame.shape[1], frame.shape[0])
        return frame

    def _run_cv_inference(self, frame: np.ndarray) -> dict | None:
        """Run the CV pipeline on *frame*.

        Returns the result dict, or None on error (status is NOT reset here —
        caller is responsible for emitting IDLE on failure).
        """
        self.status_changed.emit(WorkerStatus.PROCESSING)
        try:
            size = self._batch_state.get_state().get("expected_size") or "L"
            t0 = time.perf_counter()
            result = self._inspector.inspect(frame,size=size)
            result["inference_ms"] = (time.perf_counter() - t0) * 1000
            logger.info("CameraWorker: inference done in %.1f ms", result["inference_ms"])
            return result
        except (cv2.error, RuntimeError, ValueError) as exc:
            logger.error("CameraWorker: CV inference error: %s", exc, exc_info=True)
            return None

    def _persist_result(self, result: dict) -> dict:
        """Increment batch counter, write to DB, return enriched payload dict."""
        t0 = time.perf_counter()

        batch_snapshot    = self._batch_state.increment(result["verdict"])
        timestamp         = utcnow_iso()
        internal_piece_id = str(self._db.get_next_piece_number())

        save_image = self._should_save_image(result["verdict"], batch_snapshot["id"])
        self._db.save_inspection(
            piece_id      = internal_piece_id,
            batch_id      = batch_snapshot["id"],
            verdict       = result["verdict"],
            timestamp     = timestamp,
            detections    = result["detections"],
            image_b64     = result.get("image_b64", "") if save_image else "",
            detected_size = result.get("detected_size", ""),
        )
        self._db.cleanup_old_data()

        logger.info("CameraWorker: DB save %.0f ms (image=%s)",
                    (time.perf_counter() - t0) * 1000,
                    "yes" if save_image else "no")

        return {
            **result,
            "timestamp": timestamp,
            "batch":     batch_snapshot,
        }

    def _run_inspection_cycle(self) -> None:
        """Orchestrate one full stop-and-go inspection cycle.

        Timeline: trigger → settle (0.3 s) → capture → inference → persist → emit
        """
        t_cycle = time.perf_counter()

        # settle + capture
        t0    = time.perf_counter()
        frame = self._capture_frame()
        t_cap = (time.perf_counter() - t0) * 1000
        if frame is None:
            self.status_changed.emit(WorkerStatus.IDLE)
            return

        # CV inference
        result    = self._run_cv_inference(frame)
        t_infer   = result.get("inference_ms", 0) if result else 0
        if result is None:
            self.status_changed.emit(WorkerStatus.IDLE)
            return

        # size check + DB save
        self._apply_size_check(result, frame.shape)
        t0 = time.perf_counter()
        try:
            payload = self._persist_result(result)
        except sqlite3.IntegrityError:
            logger.error(
                "CameraWorker: batch '%s' ไม่มีใน DB (ถูกลบ) — กรุณา Reset Batch",
                self._batch_state.get_state()["id"],
            )
            self.error_occurred.emit(
                "Batch ปัจจุบันถูกลบออกจาก Database\n\n"
                "กรุณากด 'Reset Batch' เพื่อสร้าง Batch ใหม่ก่อนตรวจต่อ"
            )
            self.status_changed.emit(WorkerStatus.IDLE)
            return
        t_db = (time.perf_counter() - t0) * 1000

        t_total = (time.perf_counter() - t_cycle) * 1000
        logger.info(
            "CameraWorker: submitted | verdict=%s | "
            "settle+capture=%.0f  inference=%.0f  db=%.0f  TOTAL=%.0f ms",
            result["verdict"], t_cap, t_infer, t_db, t_total,
        )

        self.result_ready.emit(payload)
        self.status_changed.emit(WorkerStatus.IDLE)

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
            self._trigger_mode = TriggerMode.MANUAL

    def _gpio_callback(self, channel: int) -> None:
        logger.info(f"CameraWorker: GPIO rising edge on pin {channel}.")
        self._trigger_event.set()

    # ── Cleanup ────────────────────────────────────────────────────────────

    def _cleanup(self) -> None:
        # join daemon threads ก่อน release กล้อง — กัน cap.read() ทำงานอยู่ตอน release()
        # (release ขณะ read = native crash บน Jetson)
        self._stop_event.set()
        for th in (getattr(self, "_reader_thread", None), getattr(self, "_emitter_thread", None)):
            if th is not None and th.is_alive():
                th.join(timeout=1.5)
        if self._cap is not None:
            self._cap.release()
            logger.info("CameraWorker: camera released.")
        if self._trigger_mode is TriggerMode.GPIO and hasattr(self, "_gpio"):
            self._gpio.cleanup()
            logger.info("CameraWorker: GPIO cleaned up.")
