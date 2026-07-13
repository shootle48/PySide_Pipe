"""
scripts/test_job_light.py
─────────────────────────
ทดสอบไฟ job (RS485 level output) แบบ standalone — ไม่ต้องเปิดแอปเต็ม

ยิง ON <on_s> วิ → OFF <off_s> วิ × cycles ผ่าน RS485OutputWriter ตัวเดียวกับ production
(คิว + bus_lock path เดียวกับแอปจริง — เทสผ่าน = แอปจริงใช้ได้)

รัน:
  python3 scripts/test_job_light.py                        # mock — ดู log เฉยๆ (ไม่มี hardware)
  python3 scripts/test_job_light.py --real                 # hardware จริง (default /dev/ttyTHS1)
  python3 scripts/test_job_light.py --real --port /dev/ttyUSB0    # ผ่าน USB-to-RS485 dongle
  python3 scripts/test_job_light.py --real --bit 3 --cycles 5     # ลอง bit อื่น / วนหลายรอบ

หา device ของ dongle: เสียบแล้วดู  ls /dev/ttyUSB*  หรือ  dmesg | tail

⚠️ ห้ามใช้ bit 0 (OK) / bit 1 (NG) — ต่อกับ sorter ของจริง ยิงเล่นแล้ว PLC ดีดชิ้น!
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("TEST-LIGHT")

RESERVED_BITS = {0: "OK pulse (sorter)", 1: "NG pulse (sorter)"}


def main() -> int:
    ap = argparse.ArgumentParser(description="ทดสอบไฟ job ผ่าน RS485 output (level)")
    ap.add_argument("--real", action="store_true", help="ใช้ hardware จริง (default = mock)")
    ap.add_argument("--port", default="/dev/ttyTHS1",
                    help="serial device (default /dev/ttyTHS1 ; dongle USB = /dev/ttyUSB0)")
    ap.add_argument("--bit", type=int, default=2, help="output bit ของไฟ (default 2)")
    ap.add_argument("--on-s", type=float, default=2.0, help="ติดกี่วินาทีต่อรอบ (default 2)")
    ap.add_argument("--off-s", type=float, default=1.0, help="ดับกี่วินาทีต่อรอบ (default 1)")
    ap.add_argument("--cycles", type=int, default=3, help="กี่รอบ (default 3)")
    args = ap.parse_args()

    if args.bit in RESERVED_BITS:
        log.error("bit %d คือ %s — ห้ามใช้เทสไฟ! เลือก bit อื่น (เช่น 2)",
                  args.bit, RESERVED_BITS[args.bit])
        return 1

    # ── เปิด IO source ────────────────────────────────────────────────────
    if args.real:
        try:
            from rs485_dio import RS485DIO
            from core.rs485_worker import LoggingRS485DIO
            io = LoggingRS485DIO(RS485DIO(port=args.port))
            log.info("hardware จริง: port=%s", args.port)
        except Exception as exc:   # noqa: BLE001 — เทสสคริปต์ รายงานแล้วจบ
            log.error("เปิด RS485 ไม่ได้ (%s) — เช็คสาย/พอร์ต/สิทธิ์ (sudo usermod -aG dialout $USER)", exc)
            return 1
    else:
        from core.rs485_worker import MockRS485DIO
        io = MockRS485DIO(mode="manual")
        log.info("MOCK mode — ดู log write_output (ไม่มี hardware)")

    # ── writer ตัวเดียวกับ production (queue + lock) ─────────────────────
    from core.rs485_worker import RS485OutputWriter
    writer = RS485OutputWriter(io=io, bus_lock=threading.Lock())

    log.info("เริ่มเทส: bit=%d | ON %.1fs / OFF %.1fs × %d รอบ", args.bit, args.on_s, args.off_s, args.cycles)
    try:
        for i in range(1, args.cycles + 1):
            writer.set_output(args.bit, 1)
            log.info("รอบ %d/%d: ไฟ ON  → ดูที่หลอดจริง (ต้องติดค้าง %.1fs)", i, args.cycles, args.on_s)
            time.sleep(args.on_s)
            writer.set_output(args.bit, 0)
            log.info("รอบ %d/%d: ไฟ OFF", i, args.cycles)
            time.sleep(args.off_s)
    except KeyboardInterrupt:
        log.info("Ctrl+C — ดับไฟก่อนออก")
    finally:
        writer.set_output(args.bit, 0)   # จบยังไงไฟต้องดับ
        time.sleep(0.5)                  # ให้คิว drain ก่อนปิด
        writer.stop()
        if hasattr(io, "stop"):
            io.stop()

    log.info("จบเทส — ถ้าไฟติด/ดับตามรอบ = สาย+bit ถูกต้อง เอา bit นี้ใส่ RS485_LIGHT_OUTPUT_BIT ได้เลย")
    return 0


if __name__ == "__main__":
    sys.exit(main())
