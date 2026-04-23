# Pipe Inspector — PySide6

> ระบบตรวจสอบข้อบกพร่องท่อด้วย Computer Vision  
> รันบน **NVIDIA Jetson Orin Nano** | UI สร้างด้วย **PySide6 (Qt6)**

![Platform](https://img.shields.io/badge/Platform-Jetson_Orin_Nano-green?style=flat-square&logo=nvidia)
![Python](https://img.shields.io/badge/Python-3.11-blue?style=flat-square&logo=python)
![PySide6](https://img.shields.io/badge/UI-PySide6-41CD52?style=flat-square&logo=qt)
![DB](https://img.shields.io/badge/DB-SQLite_WAL-blue?style=flat-square)

---

## Overview

Pipe Inspector คือ desktop application สำหรับสายการผลิต ใช้ OpenCV ตรวจจับ defect บนท่อแบบ real-time ผ่านกล้อง USB หรืออัปโหลดรูปภาพ ผลการตรวจบันทึกลง SQLite อัตโนมัติและดูย้อนหลังได้ผ่าน Database Viewer

## Quick Start

```bash
# 1. ติดตั้ง dependencies (Jetson)
sudo apt install python3-opencv libxcb-cursor0 sqlite3 -y
pip install "numpy<2" PySide6

# 2. รัน
cd pipe-inspector-pyside
python main.py
```

> ⚠️ ห้าม `pip install opencv-python` บน Jetson — ให้ใช้ `python3-opencv` จาก apt เท่านั้น

## Features

| Feature | รายละเอียด |
|---|---|
| Live Camera View | real-time ~20 fps จากกล้อง USB |
| Upload Image Mode | ทดสอบโดยไม่มีกล้อง (demo/dev) |
| Defect Detection | HoughCircles + Adaptive Threshold |
| Batch Counter | Total / NG / NG Rate ต่อ batch |
| Inspection History | รายการล่าสุดแบบ real-time |
| Database Viewer | ดู/ลบ/export CSV ทุก batch |
| SQLite Persistence | ข้อมูลไม่หายเมื่อ restart |

## Project Structure

```
pipe-inspector-pyside/
├── main.py              # Entry point
├── requirements.txt
├── core/                # Business logic (ไม่มี Qt)
│   ├── pipeline.py      # CV pipeline + CameraWorker (QThread)
│   ├── database.py      # SQLite thread-safe CRUD
│   └── batch_state.py   # Batch counter (in-memory + write-through)
├── ui/                  # PySide6 UI layer
│   ├── main_window.py   # Main window + signal wiring
│   ├── frame_widget.py  # Live video canvas (QPainter)
│   ├── db_viewer.py     # Database viewer dialog
│   └── camera_select_dialog.py
├── scripts/
│   └── benchmark.py     # CV benchmark (ไม่ต้องมีกล้อง)
├── data/                # Runtime — pipe_inspector.db (auto-created)
└── logs/                # Runtime — app.log (rotating, 5MB×5)
```

## Documentation

| เอกสาร | เนื้อหา |
|---|---|
| [Architecture](docs/architecture.md) | System design, component diagram, signal flow |
| [Database](docs/database.md) | Schema, queries, backup, cleanup |
| [Development Guide](docs/development.md) | Setup, benchmark, contributing |
| [Deployment Guide](docs/deployment.md) | Jetson setup, autostart, systemd |
| [Troubleshooting](docs/troubleshooting.md) | Common errors + edge cases |
| [core/ Package](core/README.md) | Business logic layer |
| [ui/ Package](ui/README.md) | UI components |

## Performance (Jetson Orin Nano)

| Version | RAM | Power (avg) |
|---|---|---|
| Web (FastAPI+JS) | ~3,450 MB | ~4,441 mW |
| **PySide6 (this)** | **~3,314 MB** | **~4,359 mW** |
| NiceGUI | ~3,394 MB | ~4,359 mW |

PySide6 ใช้ทรัพยากรน้อยสุด → เลือกเป็น production version

---

*Last updated: 2026-04-23 | Praram Nine Technology*
