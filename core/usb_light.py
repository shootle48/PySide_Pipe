"""
core/usb_light.py
─────────────────
UsbLight — สั่งเปิด/ปิดไฟที่เสียบพอร์ต USB ของ Jetson โดยตัด/จ่ายไฟ VBUS ผ่าน `uhubctl`
(ไฟหลอดที่ 2 — แยกอิสระจากไฟ relay ที่สั่งผ่าน RS485 DIO bit)

ข้อจำกัดที่ต้องรู้ก่อนใช้:
  - ไม่ใช่ทุก hub/พอร์ตสั่งได้ — ต้องเป็น hub ที่รองรับ power switching (รัน `sudo uhubctl`
    ดูรายการ hub ที่คุมได้) ; บางพอร์ต VBUS ต่อตรง 5V ตลอด ตัดไม่ได้เลย
  - บาง hub ตัดแบบ "ganged" = ดับทุกพอร์ตใน hub พร้อมกัน → ห้ามเสียบกล้อง/dongle RS485
    ร่วม hub กับไฟ ; หา hub/port ที่ปลอดภัยด้วย scripts/test_usb_light.py ก่อน
  - ต้องใช้ sudo → ตั้ง NOPASSWD เฉพาะ uhubctl:
      echo "$USER ALL=(ALL) NOPASSWD: $(which uhubctl)" | sudo tee /etc/sudoers.d/uhubctl

ใช้งาน:
    light = UsbLight(hub="2-1", port=[3, 4])   # port เดียว หรือหลาย port (ganged ต้องคุมครบคู่)
    light.set(True)    # จ่ายไฟทุก port (ไฟติด) — non-blocking (ยิงใน thread)
    light.set(False)   # ตัดไฟทุก port (ไฟดับ)
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading

logger = logging.getLogger(__name__)

_CMD_TIMEOUT_S = 5.0   # uhubctl ปกติจบใน <1s — เกินนี้ถือว่าค้าง


class UsbLight:
    """คุมไฟผ่าน USB VBUS (uhubctl) — best-effort: สั่งไม่สำเร็จ = log ไม่ crash แอป."""

    def __init__(
        self,
        hub: str,
        port: int | list[int],
        cmd: str = "uhubctl",
        use_sudo: bool = True,
        runner=None,            # inject ได้สำหรับเทส (default = subprocess.run)
    ) -> None:
        self._hub = hub
        if isinstance(port, int):
            self._ports = [port]
        else:
            self._ports = list(port)
        self._cmd = cmd
        self._use_sudo = use_sudo
        self._runner = runner or subprocess.run

    def available(self) -> bool:
        """มี uhubctl ในเครื่องไหม (เช็คครั้งเดียวตอน init แอป — ไม่มีก็ปิดฟีเจอร์เงียบๆ)."""
        return shutil.which(self._cmd) is not None

    def set(self, on: bool) -> None:
        """สั่งไฟ — non-blocking (subprocess วิ่งใน daemon thread, UI ไม่สะดุด)."""
        threading.Thread(target=self.set_blocking, args=(bool(on),), daemon=True,
                         name="UsbLight").start()

    def set_blocking(self, on: bool) -> bool:
        """สั่งไฟแบบรอผล — ยิง "ทุก port" ให้ครบ (ganged ต้องคุมพร้อมกัน) คืน True
        ก็ต่อเมื่อทุก port สำเร็จ (ใช้ใน test script ; แอปใช้ set() แทน)."""
        action = "on" if on else "off"
        all_ok = True
        for port in self._ports:
            all_ok = self._one_port(action, port) and all_ok   # ยิงครบก่อนเสมอ (ไม่ short-circuit)
        return all_ok

    def _one_port(self, action: str, port: int) -> bool:
        argv = (["sudo", "-n"] if self._use_sudo else []) + [
            self._cmd, "-l", self._hub, "-p", str(port), "-a", action,
        ]
        logger.debug("Running: %s", " ".join(argv))
        try:
            res = self._runner(argv, capture_output=True, text=True, timeout=_CMD_TIMEOUT_S)
            if res.returncode == 0:
                logger.info("UsbLight: %s (hub %s port %s)", action.upper(), self._hub, port)
                return True
            # sudo ต้องการรหัส (rc=1 จาก -n) / hub ไม่รองรับ / พอร์ตผิด
            logger.warning(
                "UsbLight: uhubctl fail rc=%d port %s (%s) — เช็ค sudoers NOPASSWD / hub รองรับ power switching ไหม",
                res.returncode, port, (res.stderr or res.stdout or "").strip()[:200],
            )
        except FileNotFoundError:
            logger.warning("UsbLight: ไม่พบ uhubctl — ติดตั้ง: sudo apt install uhubctl")
        except subprocess.TimeoutExpired:
            logger.warning("UsbLight: uhubctl ค้างเกิน %.0fs (port %s) — ข้าม", _CMD_TIMEOUT_S, port)
        except OSError as exc:
            logger.warning("UsbLight: สั่งไม่สำเร็จ port %s (%s) — ข้าม", port, exc)
        return False
