"""
test_rs485_worker.py
────────────────────
Standalone test script for RS485InputWorker — ไม่ต้องมี hardware

ใช้สำหรับทดสอบ WFH:
  - Mode 1 (auto):   MockRS485DIO สุ่มยิง pulse ทุก 2 วินาที → worker emit signal
  - Mode 2 (manual): กด Enter เพื่อ trigger pulse เอง (simulate sensor)

รัน:
    python scripts/test_rs485_worker.py            # auto mode (default)
    python scripts/test_rs485_worker.py --manual   # manual mode (press Enter)
    python scripts/test_rs485_worker.py --bounce   # test debounce (yiง pulse ถี่ๆ)

ออก: Ctrl+C
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

# Allow import from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Force UTF-8 on Windows terminal (cp1252 can't render ═ 🔔 etc.)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from PySide6.QtCore import QCoreApplication, QTimer

from core.rs485_worker import MockRS485DIO, RS485InputWorker


# ── Logging: show timestamp + level so we see event order clearly ─────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("TEST")


# ══════════════════════════════════════════════════════════════════════════
# Signal handlers — ที่ Worker emit มา
# ══════════════════════════════════════════════════════════════════════════

_pulse_count = 0
_health_events: list[tuple[float, bool]] = []
_start_ts = time.monotonic()


def on_pulse(bit: int) -> None:
    global _pulse_count
    _pulse_count += 1
    elapsed = time.monotonic() - _start_ts
    print(
        f"  [{elapsed:6.2f}s] 🔔 PULSE #{_pulse_count:02d}  bit={bit}  "
        f"→ (ถ้าเป็น Pipe Inspector ตรงนี้คือจุดที่ trigger inspection)"
    )


def on_health(online: bool) -> None:
    _health_events.append((time.monotonic() - _start_ts, online))
    status = "🟢 ONLINE" if online else "🔴 OFFLINE"
    print(f"  [HEALTH] I/O {status}")


# ══════════════════════════════════════════════════════════════════════════
# Modes
# ══════════════════════════════════════════════════════════════════════════

def run_auto_mode(app: QCoreApplication) -> None:
    """MockRS485DIO สุ่มยิง pulse ทุก 2 วินาที"""
    print("\n" + "═" * 68)
    print("  AUTO MODE — MockRS485DIO ยิง pulse ทุก 2 วินาที (±20% jitter)")
    print("  Worker ควรจับ rising edge + emit signal → เห็น 🔔 PULSE")
    print("  กด Ctrl+C เพื่อออก")
    print("═" * 68 + "\n")

    io = MockRS485DIO(mode="auto_pulse", pulse_bit=0, pulse_interval_s=2.0)
    worker = RS485InputWorker(io=io, watch_bits=[0, 1, 2])
    worker.pulse_detected.connect(on_pulse)
    worker.io_health_changed.connect(on_health)
    worker.start()

    # Auto-exit after 15 seconds (ประมาณ 6-7 pulses)
    QTimer.singleShot(15_000, lambda: _graceful_exit(app, worker, io))
    app.exec()


def run_manual_mode(app: QCoreApplication) -> None:
    """กด Enter เพื่อ trigger pulse เอง"""
    print("\n" + "═" * 68)
    print("  MANUAL MODE — กด Enter เพื่อ trigger pulse (simulate sensor)")
    print("  กด 'q' + Enter เพื่อออก")
    print("═" * 68 + "\n")

    io = MockRS485DIO(mode="manual", pulse_bit=0)
    worker = RS485InputWorker(io=io, watch_bits=[0])
    worker.pulse_detected.connect(on_pulse)
    worker.io_health_changed.connect(on_health)
    worker.start()

    def input_loop() -> None:
        while True:
            user_input = input("  [Press Enter to pulse, 'q' to quit] ")
            if user_input.strip().lower() == "q":
                _graceful_exit(app, worker, io)
                return
            # Pulse: HIGH 100ms → LOW
            io.set_input(0, 1)
            time.sleep(0.1)
            io.set_input(0, 0)

    threading.Thread(target=input_loop, daemon=True).start()
    app.exec()


def run_bounce_mode(app: QCoreApplication) -> None:
    """ยิง pulse 5 ครั้งถี่ๆ (10ms apart) → worker ควร fire แค่ครั้งเดียวเพราะ debounce 30ms"""
    print("\n" + "═" * 68)
    print("  BOUNCE MODE — ยิง pulse 5 ครั้งห่างกัน 10ms (จำลอง switch bounce)")
    print("  คาดหวัง: เห็น PULSE แค่ครั้งแรก (debounce = 30ms)")
    print("═" * 68 + "\n")

    io = MockRS485DIO(mode="manual", pulse_bit=0)
    worker = RS485InputWorker(io=io, watch_bits=[0])
    worker.pulse_detected.connect(on_pulse)
    worker.start()

    def bounce_burst() -> None:
        time.sleep(1.0)   # ให้ worker warm up ก่อน
        print("  [SENDING 5 pulses, 10ms apart — bounce simulation]")
        for i in range(5):
            io.set_input(0, 1)
            time.sleep(0.005)
            io.set_input(0, 0)
            time.sleep(0.005)
        time.sleep(1.0)
        print(
            f"\n  RESULT: {_pulse_count} pulse(s) detected. "
            f"{'✅ PASS (debounce ทำงาน)' if _pulse_count == 1 else '❌ FAIL'}"
        )
        _graceful_exit(app, worker, io)

    threading.Thread(target=bounce_burst, daemon=True).start()
    app.exec()


# ══════════════════════════════════════════════════════════════════════════
# Cleanup
# ══════════════════════════════════════════════════════════════════════════

def _graceful_exit(app: QCoreApplication, worker: RS485InputWorker, io: MockRS485DIO) -> None:
    print(f"\n  ── Summary ──")
    print(f"  Total pulses detected : {_pulse_count}")
    print(f"  Health events         : {len(_health_events)}")
    print(f"  Elapsed               : {time.monotonic() - _start_ts:.1f}s")
    print()

    worker.stop()
    worker.wait(2000)
    io.stop()
    app.quit()


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="Test RS485InputWorker without hardware")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--manual", action="store_true", help="Manual pulse (press Enter)")
    mode_group.add_argument("--bounce", action="store_true", help="Test debounce logic")
    args = parser.parse_args()

    app = QCoreApplication(sys.argv)

    try:
        if args.manual:
            run_manual_mode(app)
        elif args.bounce:
            run_bounce_mode(app)
        else:
            run_auto_mode(app)
    except KeyboardInterrupt:
        print("\n  [Ctrl+C] exiting...")


if __name__ == "__main__":
    main()
