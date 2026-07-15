"""
scripts/test_job_light.py
─────────────────────────
ทดสอบไฟ job แบบ standalone — ไม่ต้องเปิดแอปเต็ม. คุมได้ทั้ง 2 หลอดเหมือน production:
  • หลอด 1 = relay ผ่าน RS485 (RS485OutputWriter + bus_lock — path เดียวกับแอป)
  • หลอด 2 = USB VBUS ผ่าน uhubctl (UsbLight — path เดียวกับแอป)
ยิง ON <on_s> วิ → OFF <off_s> วิ × cycles พร้อมกันทั้งสองหลอด (เทสผ่าน = แอปจริงใช้ได้)

รัน:
  python3 scripts/test_job_light.py                              # mock relay — ดู log (ไม่มี hardware)
  python3 scripts/test_job_light.py --real                       # relay จริง (/dev/ttyTHS1)
  python3 scripts/test_job_light.py --real --port /dev/ttyUSB0   # relay ผ่าน dongle USB-RS485

  # หลอด USB (ตัด VBUS) — จากที่หา --scan เจอบน Jetson นี้ = hub 2-1 port 3+4:
  python3 scripts/test_job_light.py --no-relay --usb-hub 2-1 --usb-port 3 4   # USB อย่างเดียว
  python3 scripts/test_job_light.py --real --usb-hub 2-1 --usb-port 3 4       # ทั้ง 2 หลอดพร้อมกัน

หา device dongle: ls /dev/ttyUSB*  ·  หา USB port ไฟ: python3 scripts/test_usb_light.py --scan

⚠️ ห้ามใช้ bit 0 (OK) / bit 1 (NG) — ต่อ sorter จริง ยิงเล่นแล้ว PLC ดีดชิ้น!
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
    ap = argparse.ArgumentParser(description="ทดสอบไฟ job (relay RS485 + USB VBUS)")
    ap.add_argument("--real", action="store_true", help="relay ใช้ hardware จริง (default = mock)")
    ap.add_argument("--no-relay", action="store_true", help="ข้ามหลอด relay (เทสหลอด USB อย่างเดียว)")
    ap.add_argument("--port", default="/dev/ttyTHS1",
                    help="serial device relay (default /dev/ttyTHS1 ; dongle = /dev/ttyUSB0)")
    ap.add_argument("--bit", type=int, default=2, help="output bit ของไฟ relay (default 2)")
    ap.add_argument("--usb-hub", help="hub ของไฟ USB เช่น 2-1 (เว้น = ไม่เทส USB)")
    ap.add_argument("--usb-port", type=int, nargs="+", help="port ไฟ USB เช่น 3 4 (ยิงครบทุก port)")
    ap.add_argument("--on-s", type=float, default=2.0, help="ติดกี่วินาทีต่อรอบ (default 2)")
    ap.add_argument("--off-s", type=float, default=1.0, help="ดับกี่วินาทีต่อรอบ (default 1)")
    ap.add_argument("--cycles", type=int, default=3, help="กี่รอบ (default 3)")
    args = ap.parse_args()

    if not args.no_relay and args.bit in RESERVED_BITS:
        log.error("bit %d คือ %s — ห้ามใช้เทสไฟ! เลือก bit อื่น (เช่น 2)", args.bit, RESERVED_BITS[args.bit])
        return 1
    if args.no_relay and not args.usb_hub:
        log.error("--no-relay แต่ไม่ได้ตั้ง --usb-hub → ไม่มีอะไรให้เทส")
        return 1

    # ── หลอด 1: relay ผ่าน RS485 ──────────────────────────────────────────
    writer = io = None
    if not args.no_relay:
        if args.real:
            try:
                from rs485_dio import RS485DIO
                from core.rs485_worker import LoggingRS485DIO
                io = LoggingRS485DIO(RS485DIO(port=args.port))
                log.info("relay: hardware จริง port=%s bit=%d", args.port, args.bit)
            except Exception as exc:   # noqa: BLE001
                log.error("เปิด RS485 ไม่ได้ (%s) — เช็คสาย/พอร์ต/สิทธิ์ (usermod -aG dialout $USER)", exc)
                return 1
        else:
            from core.rs485_worker import MockRS485DIO
            io = MockRS485DIO(mode="manual")
            log.info("relay: MOCK (ไม่มี hardware — ดู log write_output)")
        from core.rs485_worker import RS485OutputWriter
        writer = RS485OutputWriter(io=io, bus_lock=threading.Lock())

    # ── หลอด 2: USB VBUS ผ่าน uhubctl ─────────────────────────────────────
    usb = None
    if args.usb_hub:
        from core.usb_light import UsbLight
        usb = UsbLight(args.usb_hub, args.usb_port or [1])
        if not usb.available():
            log.error("ไม่พบ uhubctl — sudo apt install uhubctl")
            return 1
        log.info("USB: hub %s port %s", args.usb_hub, usb._ports)

    def _both(on: bool) -> None:
        if writer is not None:
            writer.set_output(args.bit, 1 if on else 0)
        if usb is not None:
            usb.set_blocking(on)   # blocking = เห็นผลจริงต่อรอบ

    log.info("เริ่มเทส: ON %.1fs / OFF %.1fs × %d รอบ — ดูที่หลอดจริง", args.on_s, args.off_s, args.cycles)
    try:
        for i in range(1, args.cycles + 1):
            _both(True);  log.info("รอบ %d/%d: ไฟ ON  (ติดค้าง %.1fs)", i, args.cycles, args.on_s)
            time.sleep(args.on_s)
            _both(False); log.info("รอบ %d/%d: ไฟ OFF", i, args.cycles)
            time.sleep(args.off_s)
    except KeyboardInterrupt:
        log.info("Ctrl+C — ดับไฟก่อนออก")
    finally:
        _both(False)          # จบยังไงไฟต้องดับ
        time.sleep(0.5)       # ให้คิว relay drain
        if writer is not None:
            writer.stop()
        if io is not None and hasattr(io, "stop"):
            io.stop()

    log.info("จบเทส — ไฟติด/ดับตามรอบ = สาย+bit/port ถูก เอาค่าไปใส่ config ใน ui/main_window.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
