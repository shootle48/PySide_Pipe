"""
rs485_worker.py
───────────────
QThread worker + mock สำหรับ RS485 Modbus RTU Digital I/O

Responsibilities:
  - Poll RS485 input bits ใน background thread
  - Detect rising edge + debounce
  - Emit Qt signals ให้ MainWindow (thread-safe)
  - Auto-recover + health status (เหมือน CameraWorker)

Design decisions:
  - Duck-typed IO object — รับอะไรก็ได้ที่มี `.read_inputs(indices)`
    → ใช้ MockRS485DIO สำหรับ test WFH
    → ใช้ RS485DIO จริงตอน deploy บน Jetson
  - Polling interval 20ms (ถ้า pulse width > 50ms ไม่ miss)
  - Debounce 30ms (กัน bounce เรียก trigger ซ้ำ)
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import List, Optional, Protocol, Sequence

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Protocol (duck-typed IO interface)
# ──────────────────────────────────────────────────────────────────────────

class IOReader(Protocol):
    """สิ่งที่ RS485InputWorker คาดหวัง — RS485DIO จริงและ MockRS485DIO implement ทั้งคู่"""
    def read_inputs(self, input_indices: Optional[Sequence[int]] = None) -> List[int]: ...


# ──────────────────────────────────────────────────────────────────────────
# Mock — สำหรับ test ไม่ต้องมี hardware
# ──────────────────────────────────────────────────────────────────────────

class MockRS485DIO:
    """
    Fake RS485 I/O สำหรับทดสอบ WFH.

    Modes:
      - "auto_pulse": สุ่ม trigger pulse ทุก `pulse_interval_s` วินาที
                      (default 2 วินาที → จำลอง sensor pipe เข้า)
      - "manual":     control ผ่าน `set_input(bit, value)` เอง
                      เหมาะกับ unit test
    """

    NUM_BITS = 8

    def __init__(
        self,
        mode: str = "auto_pulse",
        pulse_bit: int = 0,
        pulse_interval_s: float = 2.0,
        pulse_width_s: float = 0.1,
    ) -> None:
        self._state: List[int] = [0] * self.NUM_BITS
        self._lock = threading.Lock()
        self._mode = mode
        self._pulse_bit = pulse_bit
        self._pulse_interval_s = pulse_interval_s
        self._pulse_width_s = pulse_width_s
        self._stop_event = threading.Event()

        if mode == "auto_pulse":
            self._pulse_thread = threading.Thread(target=self._pulse_loop, daemon=True)
            self._pulse_thread.start()
            logger.info(
                f"MockRS485DIO: auto_pulse on bit {pulse_bit} "
                f"every {pulse_interval_s}s (width={pulse_width_s}s)"
            )
        else:
            logger.info("MockRS485DIO: manual mode — control via set_input()")

    def _pulse_loop(self) -> None:
        """สุ่ม trigger rising edge ที่ pulse_bit ทุก interval"""
        # delay เริ่มต้น กัน worker ที่ start ช้าพลาด pulse แรก
        time.sleep(0.5)
        while not self._stop_event.is_set():
            # jitter ±20% กัน test ดู pattern ง่ายเกินไป
            jitter = random.uniform(-0.2, 0.2) * self._pulse_interval_s
            wait = max(0.1, self._pulse_interval_s + jitter)
            if self._stop_event.wait(wait):
                break

            # pulse: LOW → HIGH → LOW
            with self._lock:
                self._state[self._pulse_bit] = 1
            logger.debug(f"MockRS485DIO: bit {self._pulse_bit} HIGH")

            if self._stop_event.wait(self._pulse_width_s):
                break

            with self._lock:
                self._state[self._pulse_bit] = 0
            logger.debug(f"MockRS485DIO: bit {self._pulse_bit} LOW")

    def read_inputs(self, input_indices: Optional[Sequence[int]] = None) -> List[int]:
        """Duck-typed: เหมือน RS485DIO.read_inputs()"""
        with self._lock:
            if input_indices is None:
                return list(self._state)
            return [self._state[i] for i in input_indices]

    def set_input(self, bit: int, value: int) -> None:
        """Manual mode: ตั้งค่า input เอง"""
        with self._lock:
            self._state[bit] = value

    def stop(self) -> None:
        self._stop_event.set()


# ──────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────

POLL_INTERVAL_S      = 0.02    # 20ms — detect pulse >= 50ms reliably
DEBOUNCE_MS          = 30      # ignore re-trigger within 30ms
IO_FAIL_THRESHOLD    = 10      # consecutive fails → offline
IO_FAIL_COOLDOWN_S   = 0.05    # sleep after fail เพื่อไม่ CPU 100%


# ──────────────────────────────────────────────────────────────────────────
# RS485InputWorker — QThread
# ──────────────────────────────────────────────────────────────────────────

class RS485InputWorker(QThread):
    """
    QThread ที่ poll RS485 inputs และ emit signal ตอนเจอ rising edge.

    Signals:
      - pulse_detected(int)    : bit index ที่ trigger rising edge
      - io_health_changed(bool): True=online, False=offline
    """

    pulse_detected     = Signal(int)
    io_health_changed  = Signal(bool)

    def __init__(
        self,
        io: IOReader,
        watch_bits: List[int],
        poll_interval_s: float = POLL_INTERVAL_S,
        debounce_ms: int = DEBOUNCE_MS,
    ) -> None:
        super().__init__()
        self._io = io
        self._watch_bits = watch_bits
        self._poll_interval_s = poll_interval_s
        self._debounce_ms = debounce_ms
        self._stop_event = threading.Event()
        self._last_edge_ms: dict[int, float] = {}

    def run(self) -> None:
        logger.info(
            f"RS485InputWorker: started | bits={self._watch_bits} "
            f"| poll={self._poll_interval_s*1000:.0f}ms "
            f"| debounce={self._debounce_ms}ms"
        )

        last_values: Optional[List[int]] = None
        fail_count = 0
        is_offline = False

        while not self._stop_event.is_set():
            # ── Read ──────────────────────────────────────────────────────
            try:
                values = self._io.read_inputs(self._watch_bits)
            except Exception as exc:
                fail_count += 1
                if fail_count == IO_FAIL_THRESHOLD and not is_offline:
                    logger.warning(
                        f"RS485InputWorker: I/O offline after {fail_count} fails: {exc}"
                    )
                    self.io_health_changed.emit(False)
                    is_offline = True
                time.sleep(IO_FAIL_COOLDOWN_S)
                continue

            # ── Recovered ────────────────────────────────────────────────
            if is_offline:
                logger.info("RS485InputWorker: I/O recovered")
                self.io_health_changed.emit(True)
                is_offline = False
            fail_count = 0

            # ── Edge detection + debounce ────────────────────────────────
            if last_values is not None:
                now_ms = time.monotonic() * 1000
                for bit, (old, new) in zip(self._watch_bits, zip(last_values, values)):
                    if old == 0 and new == 1:   # rising edge
                        last_edge = self._last_edge_ms.get(bit, 0.0)
                        if now_ms - last_edge >= self._debounce_ms:
                            self._last_edge_ms[bit] = now_ms
                            logger.info(f"RS485InputWorker: rising edge on bit {bit}")
                            self.pulse_detected.emit(bit)

            last_values = values
            time.sleep(self._poll_interval_s)

        logger.info("RS485InputWorker: stopped")

    def stop(self) -> None:
        logger.info("RS485InputWorker: stop requested")
        self._stop_event.set()
