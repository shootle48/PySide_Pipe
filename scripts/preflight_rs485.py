"""
preflight_rs485.py
──────────────────
Hardware sanity check — รันก่อนเริ่มงานวันที่ 6 พ.ค. ที่บางปะอิน
ตรวจ 4 อย่าง:
  1. minimalmodbus + pyserial ติดตั้งครบมั้ย
  2. Serial port ที่ระบุมีจริงมั้ย
  3. RS485DIO ติดต่อได้มั้ย (ลอง read_inputs)
  4. ส่ง output pulse ออกได้มั้ย (ลอง write_pulse bit 0 + 1)

รัน:
    python scripts/preflight_rs485.py
    python scripts/preflight_rs485.py --port /dev/ttyTHS2  # ถ้า port ต่างจาก default

ถ้าผ่าน → พร้อมเปิด main.py ได้
ถ้าไม่ผ่าน → fix ที่ระบุก่อน อย่ารัน app
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Allow import from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def check_dependencies() -> bool:
    """Step 1: minimalmodbus + pyserial ติดตั้งครบมั้ย"""
    print("\n[1/4] Checking Python dependencies...")
    missing = []
    try:
        import minimalmodbus
        print(f"  ✓ minimalmodbus ({minimalmodbus.__version__})")
    except ImportError:
        missing.append("minimalmodbus")
    try:
        import serial
        print(f"  ✓ pyserial ({serial.__version__})")
    except ImportError:
        missing.append("pyserial")

    if missing:
        print(f"  ✗ MISSING: {', '.join(missing)}")
        print(f"    Fix: pip install {' '.join(missing)}")
        return False
    return True


def check_serial_port(port: str) -> bool:
    """Step 2: Serial port มีจริงมั้ย"""
    print(f"\n[2/4] Checking serial port: {port}")
    try:
        import serial
        s = serial.Serial(port, 9600, timeout=1.0)
        s.close()
        print(f"  ✓ port {port} เปิดได้")
        return True
    except Exception as exc:
        print(f"  ✗ FAIL: {exc}")
        print(f"    Fix:")
        print(f"      - ตรวจว่า USB-to-RS485 dongle เสียบอยู่")
        print(f"      - ลอง: ls /dev/ttyTHS* /dev/ttyUSB*")
        print(f"      - permission: sudo chmod 666 {port}")
        return False


def check_dio_read(port: str) -> "tuple[bool, object]":
    """Step 3: ลอง read_inputs จาก DIO module"""
    print("\n[3/4] Testing RS485DIO read_inputs...")
    try:
        from rs485_dio import RS485DIO
        dio = RS485DIO(port=port, clear_outputs_on_start=False)
        values = dio.read_inputs()
        print(f"  ✓ read_inputs() → {values}")
        print(f"    (input bit 0-7 ปัจจุบัน — ถ้าไม่มีอะไร trigger ค่าจะเป็น 0 หมด)")
        return True, dio
    except Exception as exc:
        print(f"  ✗ FAIL: {exc}")
        print(f"    Fix:")
        print(f"      - ตรวจสาย RS485 (A+ / B-) ต่อถูกขั้วมั้ย")
        print(f"      - ตรวจ slave_id (default = 1) — ขอจากทีม Smart-Sense")
        print(f"      - ตรวจ baudrate (default = 9600) — ขอจากทีม Smart-Sense")
        return False, None


def check_dio_write(dio) -> bool:
    """Step 4: ลอง write_pulse บน bit 0 และ bit 1"""
    print("\n[4/4] Testing RS485DIO write_pulse...")
    try:
        print("  → pulse bit 0 (OK signal) for 200ms...")
        dio.write_pulse(0, pulse_time=0.2)
        time.sleep(0.3)

        print("  → pulse bit 1 (NG signal) for 200ms...")
        dio.write_pulse(1, pulse_time=0.2)
        time.sleep(0.3)

        outputs = dio.read_outputs()
        print(f"  ✓ write_pulse สำเร็จ — output state ปัจจุบัน: {outputs}")
        print(f"    (ถ้ามี LED ต่ออยู่จะเห็นกระพริบ)")
        return True
    except Exception as exc:
        print(f"  ✗ FAIL: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="RS485 hardware preflight check")
    parser.add_argument(
        "--port", default="/dev/ttyTHS1",
        help="Serial port (default: /dev/ttyTHS1 for Jetson)"
    )
    args = parser.parse_args()

    print("═" * 60)
    print("  RS485 PREFLIGHT CHECK — Pipe Inspector")
    print("═" * 60)

    if not check_dependencies():
        return 1
    if not check_serial_port(args.port):
        return 1

    ok, dio = check_dio_read(args.port)
    if not ok:
        return 1

    if not check_dio_write(dio):
        return 1

    print("\n" + "═" * 60)
    print("  ✅ ALL CHECKS PASSED — พร้อมเปิด main.py ได้เลยคับ")
    print("═" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
