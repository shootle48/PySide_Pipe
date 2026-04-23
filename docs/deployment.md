# Deployment Guide

การติดตั้งและ deploy Pipe Inspector บน NVIDIA Jetson Orin Nano

---

## System Requirements

| รายการ | Spec |
|---|---|
| Hardware | NVIDIA Jetson Orin Nano |
| OS | Ubuntu 20.04 / JetPack |
| Python | 3.11 (system python3) |
| Camera | USB Camera (UVC compatible) |
| Display | HDMI หรือ SSH + DISPLAY forwarding |

---

## Installation

### 1. System Packages

```bash
sudo apt update
sudo apt install python3-opencv libxcb-cursor0 libxcb-cursor-dev sqlite3 -y
```

> ⚠️ **สำคัญ**: ใช้ `python3-opencv` จาก apt เท่านั้น  
> ห้าม `pip install opencv-python` — จะ conflict กับ Qt ของ PySide6

### 2. Python Packages

```bash
pip install "numpy<2"
pip install PySide6
```

### 3. Copy Project จาก PC

```bash
# จาก PC
scp -r pipe-inspector-pyside/ jetson@<JETSON_IP>:~/Praram9/PySide

# หรือ git clone โดยตรงบน Jetson
git clone https://github.com/shootle48/PySide_Pipe ~/Praram9/PySide
```

### 4. ตรวจสอบ

```bash
python3 -c "import PySide6; import cv2; import numpy; print('All OK')"
```

---

## วิธีรัน

### มี Monitor ต่อกับ Jetson

```bash
export DISPLAY=:0
cd ~/Praram9/PySide
python3 main.py
```

### ผ่าน SSH (ไม่มี Monitor)

```bash
# ต้อง login บน Jetson desktop ก่อน แล้วรันบน desktop terminal
xhost +local:
export DISPLAY=:0
python3 main.py
```

---

## Autostart (XDG — แนะนำ)

เปิดโปรแกรมอัตโนมัติหลัง login บน Jetson desktop

```bash
mkdir -p ~/.config/autostart

cat > ~/.config/autostart/pipe-inspector.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Pipe Inspector
Exec=bash -c "cd /home/jetson/Praram9/PySide && python3 main.py"
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF
```

> ✅ XDG Autostart รันหลัง user login → มี DISPLAY environment พร้อม  
> ❌ systemd service รันก่อน login → `cannot connect to display :0`

---

## Systemd Service (ถ้าต้องการ headless)

กรณีต้องรันก่อน login — ต้องจัดการ DISPLAY เอง (ซับซ้อนกว่า)

```ini
# /etc/systemd/system/pipe-inspector.service
[Unit]
Description=Pipe Inspector CV App
After=graphical-session.target
Wants=graphical-session.target

[Service]
User=jetson
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/jetson/.Xauthority
WorkingDirectory=/home/jetson/Praram9/PySide
ExecStart=/usr/bin/python3 main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
```

```bash
sudo systemctl enable pipe-inspector
sudo systemctl start pipe-inspector
sudo journalctl -u pipe-inspector -f   # ดู log
```

---

## Camera Setup

```bash
# ดูกล้องที่มีอยู่
ls /dev/video*

# ทดสอบกล้อง (ต้องมี display)
python3 -c "
import cv2
for i in range(5):
    cap = cv2.VideoCapture(i)
    if cap.isOpened():
        print(f'Camera {i}: OK')
        cap.release()
    else:
        print(f'Camera {i}: Not found')
"
```

แก้ `CAMERA_INDEX` ใน `ui/main_window.py` ให้ตรงกับผลลัพธ์ข้างบน

---

## P0 Hardening

### 1. Single-Instance Lock

ป้องกันรัน 2 instance พร้อมกัน (seq ชน, DB contention)

```python
# เพิ่มใน main.py ก่อน MainWindow()
from PySide6.QtCore import QLockFile
lock = QLockFile("/tmp/pipe-inspector.lock")
if not lock.tryLock(100):
    print("Another instance is already running.")
    sys.exit(1)
```

### 2. Storage Save Error Handling

```python
# core/pipeline.py — รอบ save_inspection
try:
    db.save_inspection(piece_id, verdict, conf, label, image_b64, ts)
except Exception as e:
    logger.error(f"Failed to save inspection: {e}")
    batch_state.rollback_last()   # คืน counter
    self.status_changed.emit("⚠️ Save failed — piece not recorded")
```

### 3. Nightly DB Backup

```bash
# crontab -e
0 2 * * * sqlite3 /home/jetson/Praram9/PySide/data/pipe_inspector.db \
    ".backup /home/jetson/backups/pipe_$(date +\%Y\%m\%d).db"
0 3 * * * find /home/jetson/backups/ -name "pipe_*.db" -mtime +7 -delete
```

### 4. NTP Sync (กัน clock skew)

Jetson Orin Nano ไม่มี RTC battery — clock reset ทุกครั้ง restart ถ้าไม่มีเน็ต

```bash
sudo apt install chrony -y
sudo systemctl enable chrony
sudo systemctl start chrony

# ตรวจสอบ
chronyc tracking
```

### 5. Camera Health Check

เพิ่มใน `core/pipeline.py`:

```python
MAX_READ_FAILURES = 30   # มีอยู่แล้ว

# ใน worker loop
if consecutive_failures > MAX_READ_FAILURES:
    self.status_changed.emit("🔴 Camera offline")
    time.sleep(2)
    self._try_reconnect()
```

---

## Monitor Resource Usage

```bash
# CPU, RAM, GPU, Power
tegrastats

# ดู power consumption แบบ average
tegrastats --interval 1000 | grep -oP 'VDD_IN \K[0-9]+mW'
```
