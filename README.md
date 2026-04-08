# Pipe Inspector — PySide6

ระบบตรวจสอบข้อบกพร่องท่อด้วย Computer Vision
รันบน **NVIDIA Jetson Orin Nano** | UI สร้างด้วย **PySide6 (Qt)**

---

## โครงสร้างโปรเจกต์

```
pipe-inspector-pyside/
├── main.py               # Entry point — สร้าง QApplication และเปิด MainWindow
├── pipeline.py           # CV pipeline: FrameBuffer, PipeInspector, CameraWorker
├── database.py           # SQLite layer — thread-safe CRUD
├── batch_state.py        # In-memory batch counter + write-through to DB
├── benchmark.py          # Standalone CV benchmark (ไม่ต้องใช้กล้อง)
├── requirements.txt      # Python dependencies
├── data/                 # สร้างอัตโนมัติ — เก็บ pipe_inspector.db
└── ui/
    ├── main_window.py    # MainWindow — layout, signal wiring, button handlers
    ├── frame_widget.py   # FrameWidget — QPainter canvas สำหรับ live view + bbox
    └── db_viewer.py      # DbViewerDialog — ดูข้อมูล DB + Export CSV
```

---

## Features

- **Live camera view** — แสดงภาพจากกล้องแบบ real-time ~20 fps
- **Upload Image mode** — อัปโหลดรูปภาพแทนกล้อง (ใช้ได้แม้ไม่มีกล้อง)
- **Defect detection** — HoughCircles + Adaptive Threshold ตรวจจับ defect บนท่อ
- **Bounding box overlay** — วาด bbox + label บนผลลัพธ์ด้วย QPainter
- **Batch counter** — นับ Total / NG / NG Rate ต่อ batch
- **Inspection history** — แสดงรายการล่าสุดแบบ real-time
- **Database Viewer** — ดูข้อมูลทุก batch + Export CSV (UTF-8 BOM รองรับ Excel)
- **SQLite persistence** — ข้อมูลไม่หายเมื่อ restart

---

## First Setup บน Jetson Orin Nano

### 1. System packages

```bash
sudo apt update
sudo apt install python3-opencv libxcb-cursor0 libxcb-cursor-dev sqlite3 -y
```

> **สำคัญ:** ใช้ `python3-opencv` จาก apt เท่านั้น
> ห้าม `pip install opencv-python` — จะ conflict กับ Qt ของ PySide6

### 2. Python packages

```bash
pip install "numpy<2"
pip install PySide6
```

### 3. Copy โปรเจกต์

```bash
# จาก PC
scp -r pipe-inspector-pyside/ jetson@<JETSON_IP>:~/Praram9/PySide
```

### 4. ทดสอบ import

```bash
python3 -c "import PySide6; import cv2; import numpy; print('All OK')"
```

---

## วิธีรัน

### มี monitor ต่อกับ Jetson

```bash
export DISPLAY=:0
cd ~/Praram9/PySide
python3 main.py
```

### SSH (ไม่มี monitor)

ต้อง login บน Jetson desktop ก่อน แล้วรันบน desktop terminal:

```bash
xhost +local:
export DISPLAY=:0
python3 main.py
```

---

## การใช้งาน

### Camera Mode (ค่าเริ่มต้น)

1. เปิดโปรแกรม → กล้องเปิดอัตโนมัติ → เห็น `● LIVE`
2. กด **Capture & Inspect** เพื่อตรวจชิ้นงาน
3. ผลลัพธ์แสดง 4 วินาที → กลับ live view อัตโนมัติ

### Upload Image Mode

1. กด **🖼 Upload Image** ในแถบล่าง
2. กด **Upload & Inspect** → เลือกไฟล์รูป (jpg/png/bmp)
3. ผลลัพธ์แสดงเหมือน Camera mode ทุกอย่าง รวมถึงบันทึก DB

> ใช้ mode นี้เมื่อไม่มีกล้อง — เช่น demo ที่ออฟฟิศ

### Reset Batch

กด **↺ Reset Batch** → ปิด batch ปัจจุบัน → เริ่ม batch ใหม่ → counter归零

---

## Config

แก้ค่าตั้งต้นได้ที่ต้นไฟล์ `ui/main_window.py`:

```python
CAMERA_INDEX     = 0        # index ของกล้อง (0, 1, 2, ...)
TRIGGER_MODE     = "manual" # "manual" | "timer" | "gpio"
TIMER_INTERVAL   = 6.0      # วินาที — ใช้เมื่อ TRIGGER_MODE = "timer"
RESULT_VIEW_SECS = 4        # วินาที — แสดงผลก่อนกลับ live view
```

---

## Benchmark (ไม่ต้องมีกล้อง)

```bash
python3 benchmark.py --image /path/to/test.jpg --n 30
```

วัด: inference latency (min/max/mean), FPS, CPU, RAM

---

## Database

ไฟล์ DB อยู่ที่ `data/pipe_inspector.db` (สร้างอัตโนมัติครั้งแรกที่รัน)

### เปิด DB โดยตรง

```bash
sqlite3 data/pipe_inspector.db

# คำสั่งที่ใช้บ่อย
.tables
SELECT id, total, ng, is_active FROM batches;
SELECT piece_id, verdict, confidence FROM inspections ORDER BY id DESC LIMIT 10;
.quit
```

### ลบข้อมูล batch เก่า

```bash
python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect("data/pipe_inspector.db")
conn.execute("DELETE FROM inspections WHERE batch_id IN (SELECT id FROM batches WHERE is_active = 0)")
conn.execute("DELETE FROM batches WHERE is_active = 0")
conn.commit()
conn.close()
print("Done")
EOF
```

> ปิดโปรแกรมก่อนแก้ DB ทุกครั้ง

---

## แก้ปัญหาที่พบบ่อย

| อาการ | วิธีแก้ |
|-------|--------|
| `Could not load xcb plugin` | `export QT_QPA_PLATFORM_PLUGIN_PATH=$(python3 -c "import PySide6,os; print(os.path.join(os.path.dirname(PySide6.__file__),'Qt','plugins'))")` |
| `numpy.core failed to import` | `pip install "numpy<2"` |
| `could not connect to display` | Login Jetson desktop ก่อน แล้ว `xhost +local:` และ `export DISPLAY=:0` |
| กล้องเปิดไม่ได้ | แก้ `CAMERA_INDEX` ใน `main_window.py` — ลองค่า 0, 1, 2 |
| หน้าต่างไม่ขึ้น (SSH) | ต้องมี `DISPLAY=:0` และ Jetson desktop ต้อง login อยู่ |

---

## Performance (tegrastats บน Jetson Orin Nano)

| Version | RAM | VDD_IN (avg) |
|---------|-----|-------------|
| Web (FastAPI+JS) | ~3,450 MB | ~4,441 mW |
| **PySide6** | **~3,314 MB** | **~4,359 mW** |
| NiceGUI | ~3,394 MB | ~4,359 mW |

PySide6 ใช้ทรัพยากรน้อยสุด → เลือกเป็น production version
