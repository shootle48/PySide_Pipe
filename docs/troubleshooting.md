# Troubleshooting

ปัญหาที่พบบ่อยและวิธีแก้

---

## UI / Display

| อาการ | สาเหตุ | วิธีแก้ |
|---|---|---|
| `Could not load xcb plugin` | Qt plugin path ไม่ถูก | `export QT_QPA_PLATFORM_PLUGIN_PATH=$(python3 -c "import PySide6,os; print(os.path.join(os.path.dirname(PySide6.__file__),'Qt','plugins'))")` |
| หน้าต่างไม่ขึ้น (SSH) | ไม่มี DISPLAY | Login Jetson desktop ก่อน → `xhost +local:` → `export DISPLAY=:0` |
| `could not connect to display` | DISPLAY ไม่ set | `export DISPLAY=:0` |
| UI ค้าง / ไม่ตอบสนอง | inference นาน + main thread block | ตรวจสอบ CameraWorker ทำงานอยู่ใน QThread แยก |

---

## Python / Dependencies

| อาการ | สาเหตุ | วิธีแก้ |
|---|---|---|
| `numpy.core failed to import` | numpy version ไม่เข้ากัน | `pip install "numpy<2"` |
| `ModuleNotFoundError: cv2` | opencv ไม่ถูกติดตั้ง | Jetson: `sudo apt install python3-opencv` / PC: `pip install opencv-python` |
| `ImportError: PySide6` | PySide6 ไม่ถูกติดตั้ง | `pip install PySide6` |
| `from core.pipeline import ...` fails | รันจากนอก project root | `cd pipe-inspector-pyside` ก่อน `python main.py` |

---

## Camera

| อาการ | สาเหตุ | วิธีแก้ |
|---|---|---|
| กล้องเปิดไม่ได้ | CAMERA_INDEX ผิด | แก้ `CAMERA_INDEX` ใน `ui/main_window.py` — ลองค่า 0, 1, 2 |
| ภาพดำ / freeze | กล้องยังไม่ warm up | รอ 2-3 วินาทีหลังเปิดโปรแกรม |
| `🔴 Camera offline` | กล้อง unplug หรือ read fail ต่อเนื่อง | เสียบกล้องใหม่ → โปรแกรม reconnect อัตโนมัติ |

---

## Database

| อาการ | สาเหตุ | วิธีแก้ |
|---|---|---|
| `database is locked` | มี process อื่นเปิด DB อยู่ | ปิดโปรแกรมก่อนแก้ DB โดยตรง |
| DB file หาย / เสีย | Storage เต็ม หรือ power loss | restore จาก backup ที่ `~/backups/` |
| Counter ไม่ตรงหลัง restart | batch_state recover ผิด | `python3 -c "import sqlite3; ..."` ดู DB โดยตรง |
| Export CSV เปิดใน Excel ผิด encoding | UTF-8 ไม่มี BOM | export ใช้ UTF-8 BOM อยู่แล้ว — ถ้ายังผิดให้เปิด Excel → Data → From Text/CSV |

---

## Performance

| อาการ | สาเหตุ | วิธีแก้ |
|---|---|---|
| FPS ต่ำกว่า 20 | inference นานเกินไป | ดู benchmark: `python scripts/benchmark.py --image test.jpg` |
| RAM สูง | image_b64 สะสมใน DB | เปิด DbViewer → ลบ batch เก่า / ปล่อยให้ cleanup อัตโนมัติรัน (ทุก boot) |
| CPU 100% ขณะกล้อง offline | tight loop ตอน read fail | ตรวจสอบว่ามี `READ_FAIL_COOLDOWN` sleep ใน pipeline.py |

---

## Benchmark

```bash
# ดู inference latency จริง
python scripts/benchmark.py --image /path/to/test.jpg --n 30

# ดู resource ขณะรัน (Jetson)
tegrastats
```

---

## Debug Logging

```bash
# เปิด log แบบ live
tail -f logs/app.log

# กรองเฉพาะ ERROR
grep "ERROR\|CRITICAL" logs/app.log

# ดู inference time ทุกชิ้น
grep "inference done" logs/app.log
```

---

## ยังแก้ไม่ได้?

1. ดู [Architecture](architecture.md) เพื่อเข้าใจ component ที่มีปัญหา
2. เปิด issue บน GitHub พร้อม: อาการ, OS, Python version, log จาก `logs/app.log`
