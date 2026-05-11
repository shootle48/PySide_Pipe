"""
rs485_worker.py
───────────────
QThread worker + output writer สำหรับ RS485 Modbus RTU Digital I/O

Responsibilities:
  - Input  : Poll RS485 input bits ใน background thread, detect pulse,
             emit Qt signal ให้ MainWindow (thread-safe trigger)
  - Output : เขียน OK/NG verdict กลับไปหา Smart-Sense PLC ผ่าน output bit
  - Logging: แยก log ของฝั่ง Smart-Sense library กับฝั่ง app ชัดเจน
             → debug ง่ายเวลาเกิดปัญหา

Design decisions:
  - Duck-typed IO object — รับอะไรก็ได้ที่มี `.read_inputs(indices)`
    → ใช้ MockRS485DIO สำหรับ test WFH
    → ใช้ RS485DIO จริงตอน deploy บน Jetson
  - Polling interval 20ms (ถ้า pulse width > 50ms ไม่ miss)
  - Debounce 30ms (กัน bounce เรียก trigger ซ้ำ)

Logger naming convention:
  - "smartsense.dio"     — ทุก call ที่ลงไปยัง RS485DIO library ของพี่ Smart-Sense
                            (ถ้ามี ERROR จาก logger นี้ → ปัญหาฝั่ง hardware/library)
  - "app.rs485_worker"   — logic ของผมเอง (debounce, edge detection, retry)
                            (ถ้ามี ERROR จาก logger นี้ → ปัญหาฝั่ง software ผม)
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import List, Optional, Protocol, Sequence

from PySide6.QtCore import QObject, QThread, Signal, Slot

try:
    from minimalmodbus import ModbusException as _ModbusException
except ImportError:
    class _ModbusException(OSError): pass  # type: ignore[no-redef]

# ── Two separate loggers — ทำให้แยกฝั่งได้ชัดเจน ────────────────────────────
smartsense_log = logging.getLogger("smartsense.dio")   # ฝั่ง Smart-Sense library
app_log        = logging.getLogger("app.rs485_worker") # ฝั่ง app ของผม

# Backward-compat alias (เผื่อโค้ดเก่าอ้างถึง logger)
logger = app_log


# ──────────────────────────────────────────────────────────────────────────
# Protocol (duck-typed IO interface)
# ──────────────────────────────────────────────────────────────────────────

class IOReader(Protocol):
    """สิ่งที่ RS485InputWorker คาดหวัง — RS485DIO จริงและ MockRS485DIO implement ทั้งคู่"""
    def read_inputs(self, input_indices: Optional[Sequence[int]] = None) -> List[int]: ...


# ──────────────────────────────────────────────────────────────────────────
# Mock — สำหรับ test ไม่ต้องมี hardware
# ──────────────────────────────────────────────────────────────────────────

class MockRS485DIO(QObject):
    """
    Fake RS485 I/O สำหรับทดสอบ WFH (Windows / ไม่มี hardware).

    Modes:
      - "auto_pulse": สุ่ม trigger pulse ทุก `pulse_interval_s` วินาที
                      (default 2 วินาที → จำลอง sensor pipe เข้า)
      - "manual":     control ผ่าน `set_input(bit, value)` เอง
                      เหมาะกับ unit test และ test panel UI

    รองรับทั้ง read (input) และ write (output) → ทดสอบ round-trip ได้:
      input_pulsed (Signal) — emit เมื่อ test panel กด trigger
      output_written (Signal)— emit เมื่อโค้ดเรียก write_output / write_pulse
                                (test panel ใช้แสดงว่า PLC ได้รับผลแล้ว)
    """

    # Signals ให้ test panel subscribe — แสดงสถานะ I/O แบบ real-time
    input_pulsed   = Signal(int)        # bit_index ที่เพิ่งถูก trigger
    output_written = Signal(int, int)   # (bit_index, value)

    NUM_BITS = 8

    def __init__(
        self,
        mode: str = "auto_pulse",
        pulse_bit: int = 0,
        pulse_interval_s: float = 2.0,
        pulse_width_s: float = 0.1,
    ) -> None:
        super().__init__()
        self._state:    List[int] = [0] * self.NUM_BITS   # input bits
        self._outputs:  List[int] = [0] * self.NUM_BITS   # output bits
        self._lock = threading.Lock()
        self._mode = mode
        self._pulse_bit = pulse_bit
        self._pulse_interval_s = pulse_interval_s
        self._pulse_width_s = pulse_width_s
        self._stop_event = threading.Event()

        if mode == "auto_pulse":
            self._pulse_thread = threading.Thread(target=self._pulse_loop, daemon=True)
            self._pulse_thread.start()
            smartsense_log.info(
                f"MockRS485DIO: auto_pulse on bit {pulse_bit} "
                f"every {pulse_interval_s}s (width={pulse_width_s}s)"
            )
        else:
            smartsense_log.info("MockRS485DIO: manual mode — control via set_input()")

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
            smartsense_log.debug(f"MockRS485DIO: bit {self._pulse_bit} HIGH")

            if self._stop_event.wait(self._pulse_width_s):
                break

            with self._lock:
                self._state[self._pulse_bit] = 0
            smartsense_log.debug(f"MockRS485DIO: bit {self._pulse_bit} LOW")

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

    def pulse_input(self, bit: int = 0, pulse_width_s: float = 0.1) -> None:
        """
        จำลอง Smart-Sense ส่ง trigger pulse:
          0 → 1 (รอ pulse_width_s) → 0
        ใช้กับ test panel ตอน user กดปุ่ม "Send Trigger"
        Run ใน background thread กัน UI ค้าง.
        """
        def _do_pulse():
            with self._lock:
                self._state[bit] = 1
            self.input_pulsed.emit(bit)
            smartsense_log.debug(f"MockRS485DIO: bit {bit} HIGH (manual trigger)")
            time.sleep(pulse_width_s)
            with self._lock:
                self._state[bit] = 0
            smartsense_log.debug(f"MockRS485DIO: bit {bit} LOW")

        threading.Thread(target=_do_pulse, daemon=True).start()

    # ── Output side — duck-type RS485DIO interface ──────────────────────

    def write_output(self, output_index: int, value: int) -> None:
        """Mock write — เก็บค่า + emit signal ให้ test panel เห็น"""
        with self._lock:
            self._outputs[output_index] = int(bool(value))
        smartsense_log.info(f"MockRS485DIO: write_output(bit={output_index}, value={value})")
        self.output_written.emit(output_index, int(bool(value)))

    def write_pulse(
        self,
        output_index: int,
        pulse_time: float = 0.2,
        active_value: int = 1,
    ) -> None:
        """
        Mock pulse — HIGH → wait → LOW (เหมือน RS485DIO.write_pulse จริง)
        Block ตาม pulse_time → caller ต้องเรียกจาก background thread
        """
        active   = int(bool(active_value))
        inactive = 0 if active == 1 else 1
        self.write_output(output_index, active)
        time.sleep(pulse_time)
        self.write_output(output_index, inactive)

    def read_outputs(self, output_indices: Optional[Sequence[int]] = None) -> List[int]:
        """อ่านสถานะ output ปัจจุบัน (สำหรับ test/debug)"""
        with self._lock:
            if output_indices is None:
                return list(self._outputs)
            return [self._outputs[i] for i in output_indices]

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
        app_log.info(
            f"RS485InputWorker: started | bits={self._watch_bits} "
            f"| poll={self._poll_interval_s*1000:.0f}ms "
            f"| debounce={self._debounce_ms}ms"
        )

        last_values: Optional[List[int]] = None
        fail_count = 0
        is_offline = False

        while not self._stop_event.is_set():
            # ── Read ──────────────────────────────────────────────────────
            # Note: ถ้า read_inputs FAIL → log ที่ฝั่ง smartsense (เพราะเรียก library)
            #       ส่วน retry / health logic เป็นของ app ของผมเอง
            try:
                values = self._io.read_inputs(self._watch_bits)
            except (OSError, _ModbusException) as exc:
                smartsense_log.debug(f"read_inputs failed: {exc}")
                fail_count += 1
                if fail_count == IO_FAIL_THRESHOLD and not is_offline:
                    app_log.warning(
                        f"RS485InputWorker: I/O offline after {fail_count} fails: {exc}"
                    )
                    self.io_health_changed.emit(False)
                    is_offline = True
                time.sleep(IO_FAIL_COOLDOWN_S)
                continue

            # ── Recovered ────────────────────────────────────────────────
            if is_offline:
                app_log.info("RS485InputWorker: I/O recovered")
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
                            app_log.info(f"RS485InputWorker: rising edge on bit {bit}")
                            self.pulse_detected.emit(bit)

            last_values = values
            time.sleep(self._poll_interval_s)

        app_log.info("RS485InputWorker: stopped")

    def stop(self) -> None:
        app_log.info("RS485InputWorker: stop requested")
        self._stop_event.set()


# ──────────────────────────────────────────────────────────────────────────
# RS485OutputWriter — ส่ง verdict กลับไปหา Smart-Sense PLC
# ──────────────────────────────────────────────────────────────────────────
#
# Convention การส่งกลับ (ตกลงกับทีม Smart-Sense ก่อน deploy):
#   - bit `ok_bit`  pulse 200ms เมื่อ verdict = "OK"
#   - bit `ng_bit`  pulse 200ms เมื่อ verdict = "NG"
#
# Smart-Sense PLC อ่าน pulse เหล่านี้ไปควบคุม sorter / reject mechanism
# (เช่น ถ้าได้ pulse ที่ ng_bit → เปิด solenoid ดันชิ้นออกจากสายพาน)
#
# ทำไมเป็น pulse ไม่ใช่ level?
#   - แต่ละ pulse = ผลตรวจ 1 ชิ้น → PLC นับได้ตรงๆ
#   - ไม่ต้อง state-track ฝั่ง PLC ว่าเคยอ่านผลล่าสุดไปแล้วหรือยัง
#

class RS485OutputWriter(QObject):
    """
    QObject helper — ส่ง verdict (OK/NG) กลับไปหา Smart-Sense PLC ผ่าน RS485 output.

    ใช้งาน:
        writer = RS485OutputWriter(io=dio, ok_bit=0, ng_bit=1)
        # ตอน main_window รับ result_ready signal:
        writer.send_verdict("OK")   # → pulse bit 0
        writer.send_verdict("NG")   # → pulse bit 1

    Threading:
        write_pulse() ใน RS485DIO ใช้ time.sleep() ภายใน → block ~200ms
        ถ้าเรียกจาก main thread จะทำให้ UI ค้าง
        → ต้องเรียกผ่าน QThreadPool / QtConcurrent หรือ wrap ด้วย QThread
        ในที่นี้ใช้วิธีง่ายสุด: spawn เป็น threading.Thread (daemon)
    """

    # Signal เผื่อ UI อยากรู้ว่า output ส่งสำเร็จหรือไม่ (optional)
    write_complete = Signal(str, bool)   # (verdict, success)

    def __init__(
        self,
        io,                       # RS485DIO หรือ MockRS485DIO (ต้องมี write_pulse)
        ok_bit: int = 0,
        ng_bit: int = 1,
        pulse_ms: int = 200,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._io = io
        self._ok_bit  = ok_bit
        self._ng_bit  = ng_bit
        self._pulse_s = pulse_ms / 1000.0
        app_log.info(
            f"RS485OutputWriter: ok_bit={ok_bit} ng_bit={ng_bit} pulse={pulse_ms}ms"
        )

    @Slot(str)
    def send_verdict(self, verdict: str) -> None:
        """
        ส่ง verdict กลับไปหา PLC — non-blocking (run ใน background thread)

        Args:
            verdict: "OK" หรือ "NG"
        """
        if verdict not in ("OK", "NG"):
            app_log.warning(f"RS485OutputWriter: unknown verdict '{verdict}', skipped")
            return

        bit = self._ok_bit if verdict == "OK" else self._ng_bit

        # Run write_pulse ใน background — กัน UI ค้าง
        threading.Thread(
            target=self._do_pulse,
            args=(verdict, bit),
            daemon=True,
        ).start()

    def _do_pulse(self, verdict: str, bit: int) -> None:
        """Worker function — ทำ write_pulse จริงใน background"""
        try:
            # หมายเหตุ: write_pulse เป็น call ลงไปยัง Smart-Sense library
            #          ถ้า fail → log ที่ smartsense logger (ปัญหาฝั่งเขา)
            self._io.write_pulse(bit, pulse_time=self._pulse_s)
            app_log.info(f"verdict {verdict} sent to PLC (bit {bit})")
            self.write_complete.emit(verdict, True)
        except (OSError, _ModbusException) as exc:
            smartsense_log.error(
                f"write_pulse(bit={bit}) FAILED for verdict {verdict}: {exc}"
            )
            app_log.warning(
                f"failed to send verdict {verdict} to PLC — sorter อาจไม่ทำงาน"
            )
            self.write_complete.emit(verdict, False)


# ──────────────────────────────────────────────────────────────────────────
# LoggingRS485DIO — wrapper ให้ทุก call ลงไปยัง library มี log อัตโนมัติ
# ──────────────────────────────────────────────────────────────────────────
#
# ใช้ตอน production เพื่อ debug:
#   from rs485_dio import RS485DIO
#   real_dio = RS485DIO(port="/dev/ttyTHS1")
#   logged_dio = LoggingRS485DIO(real_dio)
#   worker = RS485InputWorker(io=logged_dio, ...)
#
# ทุก call ที่ผ่าน wrapper จะถูก log ที่ logger "smartsense.dio"
# → ถ้าเห็น ERROR จาก smartsense.dio → ปัญหาอยู่ที่ library/hardware ฝั่ง Smart-Sense
# → ถ้าเห็น ERROR จาก app.* → ปัญหาอยู่ที่ logic ฝั่งผม

class LoggingRS485DIO:
    """
    Decorator-style wrapper — log ทุก call ที่ลงไปยัง RS485DIO ของพี่ Smart-Sense.

    Duck-typing ตามเดิม → ใช้แทน RS485DIO ได้ตรงๆ:
        worker = RS485InputWorker(io=LoggingRS485DIO(real_dio), ...)
    """

    def __init__(self, dio) -> None:
        self._dio = dio

    def read_inputs(self, input_indices=None):
        try:
            result = self._dio.read_inputs(input_indices)
            smartsense_log.debug(f"read_inputs({input_indices}) -> {result}")
            return result
        except (OSError, _ModbusException) as exc:
            smartsense_log.error(f"read_inputs({input_indices}) FAILED: {exc}")
            raise

    def read_input(self, input_index):
        try:
            result = self._dio.read_input(input_index)
            smartsense_log.debug(f"read_input({input_index}) -> {result}")
            return result
        except (OSError, _ModbusException) as exc:
            smartsense_log.error(f"read_input({input_index}) FAILED: {exc}")
            raise

    def write_output(self, output_index, value):
        try:
            self._dio.write_output(output_index, value)
            smartsense_log.info(f"write_output(bit={output_index}, value={value})")
        except (OSError, _ModbusException) as exc:
            smartsense_log.error(
                f"write_output({output_index}, {value}) FAILED: {exc}"
            )
            raise

    def write_pulse(self, output_index, pulse_time=0.2, active_value=1):
        try:
            self._dio.write_pulse(output_index, pulse_time=pulse_time, active_value=active_value)
            smartsense_log.info(
                f"write_pulse(bit={output_index}, {pulse_time*1000:.0f}ms)"
            )
        except (OSError, _ModbusException) as exc:
            smartsense_log.error(f"write_pulse({output_index}) FAILED: {exc}")
            raise

    # delegate methods อื่นที่อาจถูกเรียก (read_outputs, write_outputs ฯลฯ)
    def __getattr__(self, name):
        return getattr(self._dio, name)
