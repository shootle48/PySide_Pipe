# Pipe Inspector — PySide6

> ระบบตรวจสอบข้อบกพร่องท่อด้วย Computer Vision
> รันบน **NVIDIA Jetson Orin Nano** | UI สร้างด้วย **PySide6 (Qt6)**

![Platform](https://img.shields.io/badge/Platform-Jetson_Orin_Nano-green?style=flat-square&logo=nvidia)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)
![PySide6](https://img.shields.io/badge/UI-PySide6-41CD52?style=flat-square&logo=qt)
![DB](https://img.shields.io/badge/DB-SQLite_WAL-blue?style=flat-square)

---

## Overview

Pipe Inspector คือ desktop application สำหรับสายการผลิต ใช้ OpenCV ตรวจจับ defect บนท่อแบบ real-time ผ่านกล้อง USB หรืออัปโหลดรูปภาพ ผลการตรวจบันทึกลง SQLite อัตโนมัติและดูย้อนหลังได้ผ่าน Database Viewer

โปรเจกต์จัดเป็น **src/ layout แบบแบ่งเลเยอร์** (`pipe_inspector` package) — ดู [ARCHITECTURE.md](ARCHITECTURE.md)

## Quick Start

```bash
# Windows / dev (มี Python 3.10+)
pip install -e ".[dev]"      # ติดตั้ง package + dev tools (pytest, ruff)
python main.py               # รันแอป (เทียบเท่า: python -m pipe_inspector  หรือ  pipe-inspector)
```

```bash
# Jetson (production) — ห้าม pip install opencv-python (ใช้ของ apt)
sudo apt install python3-opencv libxcb-cursor0 sqlite3 -y
pip install -e . --no-deps   # ติดตั้งเฉพาะ package; deps มาจาก apt/ระบบ
python main.py
```

> ⚠️ บน Jetson **ห้าม** `pip install opencv-python` — ให้ใช้ `python3-opencv` จาก apt เท่านั้น (จึงใช้ `--no-deps`)

**Entry points** (เทียบเท่ากันหมด): `python main.py` · `python -m pipe_inspector` · `pipe-inspector`

## Configuration

| ที่ | ใช้ทำอะไร |
|---|---|
| [`config/settings.yaml`](config/settings.yaml) | ค่า default ของ station — camera index, trigger mode, RS485, detection threshold |
| `.env` (ดู [`.env.example`](.env.example)) | override ผ่าน env (`PIPE_INSPECTOR_*`) เช่น `PIPE_INSPECTOR_ROOT`, `PIPE_INSPECTOR_CAMERA_INDEX` |
| Qt `QSettings` | ค่าที่ผู้ใช้ปรับ runtime (threshold ต่อ size, กล้องที่เลือก) — persist อัตโนมัติ |

ลำดับความสำคัญ: `settings.yaml` < `PIPE_INSPECTOR_*` env ; ส่วน QSettings เป็นค่าผู้ใช้ตอน runtime

## Tests & Lint

```bash
pytest                       # unit tests (headless — ไม่ต้องมีกล้อง/Qt display)
ruff check src tests         # lint
```

## Docker (headless / CI เท่านั้น)

```bash
docker compose run --rm tests   # รัน pytest ใน container
docker compose run --rm lint    # รัน ruff
```

> ⚠️ image นี้ **ไม่ได้ใช้รันแอปจริง** — บน Windows, Docker ส่งผ่านกล้อง USB/RS485/GUI เข้า container ไม่ได้ ให้รันแอป HMI แบบ native (`python main.py`) image มีไว้สำหรับ test/CI/โค้ดฝั่ง headless

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
PySide_Pipe/
├── main.py                  # thin shim → pipe_inspector.app.main()
├── pyproject.toml           # packaging, deps, pytest/ruff config
├── config/settings.yaml     # default settings (per-station)
├── .env.example
├── Dockerfile / docker-compose.yml   # headless tests/CI
├── src/pipe_inspector/
│   ├── app.py               # QApplication bootstrap
│   ├── paths.py             # project-root / data / logs / DB path resolution
│   ├── config/              # settings loader + logging setup
│   ├── domain/              # enums + batch state (no Qt, no I/O)
│   ├── vision/              # detection, inspector, camera worker, size classifier
│   ├── hardware/            # RS485 DIO driver + Qt worker (mock + real)
│   ├── storage/             # SQLite persistence
│   └── ui/                  # PySide6: main_window, theme, widgets/, dialogs/
├── scripts/                 # RS485 diagnostics + CV benchmark
├── tests/                   # pytest (unit/, integration/)
├── data/                    # runtime — pipe_inspector.db (auto-created, gitignored)
└── logs/                    # runtime — app.log (rotating)
```

## Documentation

| เอกสาร | เนื้อหา |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, workflows, design rationale, edge cases |
| [docs/database.md](docs/database.md) | Schema, queries, backup, cleanup |
| [docs/development.md](docs/development.md) | Setup, benchmark, contributing |
| [docs/deployment.md](docs/deployment.md) | Jetson setup, autostart, systemd |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Common errors + edge cases |
