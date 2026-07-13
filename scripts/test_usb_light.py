"""
scripts/test_usb_light.py
─────────────────────────
หา hub/port ที่สั่งตัดไฟได้ + เทสไฟหลอด USB (VBUS ผ่าน uhubctl) — ไม่ต้องเปิดแอปเต็ม

ขั้นตอนบนเครื่อง Jetson ทดสอบ:
  1) sudo apt install uhubctl
  2) python3 scripts/test_usb_light.py --list          # ดูว่า hub ไหนคุมไฟได้บ้าง
  3) เสียบไฟ USB → python3 scripts/test_usb_light.py --list   # เทียบว่าไฟโผล่ hub/port ไหน
  4) python3 scripts/test_usb_light.py --hub 1-2 --port 1     # ยิงกระพริบ — ดูหลอดจริง
  5) ได้ hub/port แล้ว → ใส่ USB_LIGHT_HUB / USB_LIGHT_PORT ใน ui/main_window.py + ENABLED=True

⚠️ ก่อนยิง: ถ้ากล้อง/dongle RS485 เสียบ hub เดียวกับไฟ ให้ย้ายออกก่อน —
   บาง hub ตัดไฟแบบยกแผง (ganged) อุปกรณ์ร่วม hub จะหลุดไปด้วย!

sudo ไม่ถามรหัส (จำเป็นถ้าให้แอปเรียกเอง):
  echo "$USER ALL=(ALL) NOPASSWD: $(which uhubctl)" | sudo tee /etc/sudoers.d/uhubctl
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("TEST-USB-LIGHT")


def list_hubs() -> int:
    """โชว์ hub/port ทั้งหมดที่ uhubctl คุมได้ (ตรงนี้แหละคำตอบว่า 'พอร์ตไหนสั่งได้')"""
    try:
        res = subprocess.run(["sudo", "-n", "uhubctl"], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        log.error("ไม่พบ uhubctl — ติดตั้ง: sudo apt install uhubctl")
        return 1
    out = (res.stdout or "") + (res.stderr or "")
    print(out.strip() or "(ไม่มี output)")
    if res.returncode != 0 and "password" in out.lower():
        log.error("sudo ต้องการรหัส — รันเอง: sudo uhubctl  หรือ ตั้ง NOPASSWD (ดูหัวไฟล์)")
        return 1
    print()
    log.info("hub ที่ขึ้นในรายการ = สั่งตัดไฟได้ ; port ที่มีอุปกรณ์จะมีชื่อ device ต่อท้าย")
    log.info("เสียบ/ถอดไฟแล้วรัน --list ซ้ำ เทียบว่าไฟอยู่ hub/port ไหน")
    return 0


def blink(hub: str, port: int, on_s: float, off_s: float, cycles: int) -> int:
    from core.usb_light import UsbLight   # ใช้ path เดียวกับแอปจริง
    light = UsbLight(hub=hub, port=port)

    log.info("เทสไฟ USB: hub=%s port=%d | ON %.1fs / OFF %.1fs × %d รอบ", hub, port, on_s, off_s, cycles)
    ok_all = True
    try:
        for i in range(1, cycles + 1):
            ok_all &= light.set_blocking(True)
            log.info("รอบ %d/%d: ไฟ ON  → ดูหลอดจริง", i, cycles)
            time.sleep(on_s)
            ok_all &= light.set_blocking(False)
            log.info("รอบ %d/%d: ไฟ OFF", i, cycles)
            time.sleep(off_s)
    except KeyboardInterrupt:
        log.info("Ctrl+C — จ่ายไฟคืนก่อนออก")
    finally:
        light.set_blocking(True)   # จบเทสจ่ายไฟคืน (default ปลอดภัย: พอร์ตมีไฟตามปกติ)

    if ok_all:
        log.info("คำสั่งผ่านหมด — ถ้าหลอด 'ติด/ดับตามรอบจริง' = ใช้ hub/port นี้ได้:")
        log.info("  ui/main_window.py → USB_LIGHT_ENABLED=True, USB_LIGHT_HUB=%r, USB_LIGHT_PORT=%d", hub, port)
        log.info("ถ้าคำสั่งผ่านแต่หลอดไม่ดับ = พอร์ตนี้ VBUS ต่อตรง (always-on) — ลอง hub/port อื่น")
    else:
        log.warning("มีคำสั่ง fail — ดู warning ข้างบน (sudoers / hub ไม่รองรับ)")
    return 0 if ok_all else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="หา hub/port + เทสไฟ USB (uhubctl)")
    ap.add_argument("--list", action="store_true", help="โชว์ hub/port ที่คุมได้ทั้งหมด")
    ap.add_argument("--hub", help="hub location เช่น 1-2 (จาก --list)")
    ap.add_argument("--port", type=int, help="port ใน hub นั้น")
    ap.add_argument("--on-s", type=float, default=2.0)
    ap.add_argument("--off-s", type=float, default=1.0)
    ap.add_argument("--cycles", type=int, default=3)
    args = ap.parse_args()

    if args.list:
        return list_hubs()
    if args.hub and args.port is not None:
        return blink(args.hub, args.port, args.on_s, args.off_s, args.cycles)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
