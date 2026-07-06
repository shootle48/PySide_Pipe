# core/ — Business Logic Layer

Package นี้รวม logic ทั้งหมดที่ไม่เกี่ยวกับ UI  
**ไม่มี Qt import** → สามารถ reuse กับ backend/CLI อื่นได้ในอนาคต

---

## Modules

### `pipeline.py` — CV Pipeline + Camera Worker

```
PipeInspector          ← OpenCV defect detection
CameraWorker           ← QThread สำหรับ capture + inspect loop
```

**Signal ที่ emit ออกไปยัง MainWindow:**

| Signal | Type | เมื่อไหร่ |
|---|---|---|
| `frame_ready` | `np.ndarray` BGR | ทุก frame ~20 fps |
| `result_ready` | `dict` | หลัง inspect เสร็จแต่ละชิ้น |
| `status_changed` | `str` | scanning / processing / idle / error |

**Result dict shape:**
```python
{
    "verdict":    "OK" | "NG",
    "confidence": float,          # 0.0 – 1.0
    "detections": [
        {"label": str, "confidence": float,
         "bbox": {"x": int, "y": int, "w": int, "h": int}}
    ],
    "image_b64":  str,            # base64 JPEG ของ frame ที่ inspect
    "piece_id":   str,            # e.g. "BATCH-3A9F12-0042"
    "timestamp":  str,            # UTC ISO-8601
    "batch":      {"id": str, "total": int, "ng": int},
}
```

**Tunable constants (ต้นไฟล์):**
```python
MIN_DEFECT_AREA    = 50     # px² — contour เล็กกว่านี้ถือว่าไม่ใช่ defect
INNER_RADIUS_RATIO = 0.5    # fraction ของ pipe radius ที่ inspect
JPEG_QUALITY       = 85
OK_FILE_SAMPLE_EVERY_N = 50 # เก็บรูป OK (ทั้ง DB+ไฟล์) 1 ใบทุก N ชิ้น OK ; NG เก็บทุกใบ
MAX_READ_FAILURES  = 30     # consecutive camera read fail → offline
```

---

### `database.py` — SQLite Thread-Safe CRUD

```
DatabaseManager    ← wrapper รอบ sqlite3 connection
                      - threading.Lock (single writer safe)
                      - WAL mode (concurrent read)
                      - auto migrations (ALTER TABLE)
                      - cleanup ข้อมูล > 90 วัน
```

ดูรายละเอียด schema และ queries ที่ [docs/database.md](../docs/database.md)

---

### `batch_state.py` — Batch Counter

```
BatchStateManager  ← in-memory counter พร้อม write-through to DB
_BatchData         ← dataclass เก็บ state ของ batch ปัจจุบัน
```

**Design:** อ่านจาก memory (เร็ว), เขียนลง DB ทุกครั้ง (durable)  
ตอน startup → recover จาก DB อัตโนมัติ (`_initialize`)

---

## Usage Example

```python
from core.database    import DatabaseManager
from core.batch_state import BatchStateManager
from core.pipeline    import PipeInspector

db = DatabaseManager("data/pipe_inspector.db")
bs = BatchStateManager(db)

inspector = PipeInspector()
result = inspector.inspect(frame)   # frame: np.ndarray BGR
```

---

## Rules

- ❌ ห้าม import จาก `PySide6` ในไฟล์ `core/` (ยกเว้น `pipeline.py` ที่ต้องใช้ `QThread`)
- ❌ ห้าม import จาก `ui/`
- ✅ test ได้โดยตรงโดยไม่ต้อง start Qt application
