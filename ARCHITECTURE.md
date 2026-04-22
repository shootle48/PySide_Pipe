# Pipe Inspector — Architecture & Edge Cases

เอกสารสรุป architecture, workflow, design rationale, edge cases และข้อแนะนำ production
สำหรับโปรเจค **Pipe Inspector (PySide6 edition)** บน Jetson Orin Nano

---

## 1. High-level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       MainWindow (UI)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ Counters     │  │ Preview      │  │ Buttons           │  │
│  │ total / ng   │  │ QLabel       │  │ capture / reset / │  │
│  │ expected/miss│  │ (QPixmap)    │  │ set_expected /    │  │
│  │              │  │              │  │ db_viewer         │  │
│  └──────────────┘  └──────────────┘  └───────────────────┘  │
└──────▲────────────────────▲────────────────────▲────────────┘
       │  Qt Signal         │  Qt Signal         │ open dialog
       │  result_ready      │  frame_ready       │
┌──────┴────────────┐  ┌────┴──────────┐  ┌──────┴──────────┐
│ BatchStateManager │  │ CameraWorker  │  │   DbViewer      │
│  (in-memory +     │  │  (QThread)    │  │  (QDialog)      │
│   write-through)  │  │  - VideoCap   │  │  - list batches │
│  seq / total / ng │  │  - Inspector  │  │  - delete rec   │
│  expected_total   │  │  - triggers   │  │  - export CSV   │
└────────┬──────────┘  │    inspect()  │  │  - export data- │
         │             └──────┬────────┘  │    set          │
         │                    │           └─────────┬───────┘
         │                    │                     │
         │                    ▼                     │
         │         ┌────────────────────┐           │
         └────────▶│  DatabaseManager   │◀──────────┘
                   │  SQLite + WAL +    │
                   │  threading.Lock    │
                   │  (inspections,     │
                   │   batches)         │
                   └────────────────────┘
```

**Key ownership**: `MainWindow` เป็นเจ้าของทั้ง `DatabaseManager`, `BatchStateManager`, และ `CameraWorker`
ทุก component สื่อสารกันผ่าน **Qt Signals/Slots** (thread-safe) และ **shared DB**

---

## 2. Workflows

### 2.1 Capture workflow (trigger → save)

```
User click "Capture"
    │
    ▼
CameraWorker.trigger()          (main thread sets flag)
    │
    ▼
CameraWorker loop               (worker thread)
    │
    ├─ read frame from VideoCapture
    │
    ├─ t0 = perf_counter()
    ├─ result = inspector.inspect(frame)   # YOLO
    ├─ log "inference done in X ms"
    │
    ├─ batch_state.increment(verdict)      # seq++, total++
    │      └─ DB.update_batch_counters()
    │
    ├─ piece_id = f"{batch_id}-{seq:04d}"
    │
    ├─ if _should_save_image(verdict, batch_id):
    │      image_b64 = base64(jpeg)
    │   else:
    │      image_b64 = None
    │
    ├─ DB.save_inspection(piece_id, verdict, conf, label, image_b64, timestamp)
    │
    ├─ DB.cleanup_old_data()              (if due)
    │
    └─ emit result_ready(result, snapshot)
           │
           ▼
       MainWindow._on_result_ready()      (main thread)
           - update counters
           - update preview
           - update missing label
```

### 2.2 Startup workflow

```
systemd / XDG Autostart
    │
    ▼
main.py
    │
    ├─ QApplication()
    ├─ MainWindow()
    │     │
    │     ├─ DatabaseManager()
    │     │     ├─ open SQLite (WAL mode)
    │     │     ├─ CREATE TABLE IF NOT EXISTS ...
    │     │     └─ migrations (ADD COLUMN ...)
    │     │
    │     ├─ db.cleanup_old_data()
    │     │     ├─ DELETE inspections > 90 days
    │     │     └─ _recalculate_all_batch_counters()
    │     │
    │     ├─ BatchStateManager(db)
    │     │     └─ _initialize()
    │     │         ├─ get_active_batch()   # recover
    │     │         ├─ get_max_piece_seq()  # seq = MAX piece_seq
    │     │         └─ populate _BatchData
    │     │
    │     └─ CameraWorker.start()
    │
    └─ app.exec()
```

### 2.3 Reset batch workflow

```
User click "Reset Batch"
    │
    ▼
QInputDialog.getInt() → expected_total
    │
    ▼
batch_state.reset(expected_total)
    ├─ db.close_active_batch(old_id, now)
    ├─ db.create_batch(new_id, now, expected_total)
    └─ _data = _BatchData(new_id, expected_total=...)
```

---

## 3. Design Rationale

| ตัวเลือก | เหตุผล | ทางเลือกที่ถูกปฏิเสธ |
|---------|--------|---------------------|
| **QThread + Qt Signals** | UI ไม่ freeze ตอน YOLO inference (~150ms), signals auto-queue ข้าม thread | `asyncio` — PySide6 event loop ไม่เข้ากันดี; `multiprocessing` — overhead สูง share frame ยาก |
| **SQLite + WAL mode** | concurrent read ขณะเขียน, single file, zero-config, พอสำหรับ ~10 pieces/sec | Postgres — overkill, ต้องลง service; JSON file — ไม่ atomic |
| **threading.Lock บน connection** | `check_same_thread=False` + manual lock เพียงพอเพราะ writer คนเดียว (worker thread) | connection pool — ไม่คุ้มสำหรับ single writer |
| **In-memory state + write-through DB** | อ่าน counter เร็ว (ไม่ query ทุกครั้ง), DB เป็น source of truth ตอน recovery | เขียน DB ทุก read → slow; เก็บ memory อย่างเดียว → หายตอน crash |
| **Base64 JPEG ใน DB** | atomic กับ metadata (ไม่มีกรณี record อยู่แต่ไฟล์หาย), backup ก็อปไฟล์เดียวจบ | เก็บเป็น path — race condition cleanup, sync ยาก |
| **`seq` แยกจาก `total`** | piece_id ต้อง unique; ถ้าใช้ `total = COUNT(*)` → ลบ record แล้ว counter ลด → piece_id ชนของเดิม | ใช้ UUID — อ่านยาก, เสียลำดับ |
| **OK sampling (every-N + ratio cap)** | เก็บ NG ครบทุกชิ้น (สำคัญกับการ train), OK สุ่มพอให้เทียบได้ แต่ไม่กินที่ | เก็บทุกชิ้น — SD card เต็มเร็ว; ไม่เก็บ OK เลย — dataset imbalanced |
| **Migrations ด้วย ALTER TABLE + duplicate guard** | schema เปลี่ยนได้โดยไม่ทิ้งของเก่า, idempotent | drop & recreate — ข้อมูลหาย; Alembic — overkill |
| **Cleanup ทุกครั้งเปิดโปรแกรม** | ไม่ต้องพึ่ง cron / scheduled task, การันตีว่ารัน | periodic task — ต้อง setup เพิ่ม |
| **systemd + XDG Autostart** | GUI app ต้องการ DISPLAY → XDG ดีกว่า systemd (รันหลัง login) | systemd อย่างเดียว → รันก่อน login → `cannot connect to display :0` |

---

## 4. Edge Cases

### 🔴 HIGH severity

| Case | ผลกระทบ | Mitigation |
|------|--------|-----------|
| **Multiple instances รันพร้อมกัน** | 2 process เปิด batch พร้อมกัน → seq ชน, DB lock contention | **P0**: single-instance lock (flock ไฟล์ / QLockFile) |
| **Clock skew** (Jetson ไม่มี RTC battery) | timestamp ย้อนกลับ → cleanup ลบผิด, เรียงผิด | NTP sync ตอน boot + ใช้ monotonic clock สำหรับ sort |
| **Storage full mid-save** | `save_inspection()` throw → record หาย, batch counter เพิ่มไปแล้ว (inconsistent) | **P0**: try/except รอบ save + rollback `batch_state` ถ้า fail |
| **Camera unplug ขณะรัน** | VideoCapture return None ต่อเนื่อง → UI ค้าง preview เก่า | **P0**: camera health check + reconnect loop + UI indicator |

### 🟡 MEDIUM severity

| Case | ผลกระทบ | Mitigation |
|------|--------|-----------|
| **Worker shutdown timeout** | ปิดโปรแกรมตอน inspect ยังไม่เสร็จ → DB write ไม่จบ | `QThread.wait(timeout)` + flush DB ใน `closeEvent` |
| **Batch ใหญ่มาก (>10k pieces)** | `get_max_piece_seq` query ช้าลง | index บน `piece_id` (มีอยู่แล้ว) — ปัจจุบัน OK |
| **DB corruption** | SQLite ไฟล์เสีย → เปิดไม่ได้ | WAL ช่วยลดความเสี่ยง + **P0**: nightly backup |
| **Lock contention** | DbViewer query นานขณะ worker เขียน | WAL allow concurrent read; query UI ใช้ LIMIT |
| **OK sampling stuck** | ถ้า NG = 0 → ไม่เคย save OK เลย (ratio cap) | by design — รอจนมี NG ก่อนค่อยเก็บ sample |

### 🟢 LOW severity

| Case | ผลกระทบ | Mitigation |
|------|--------|-----------|
| **Unicode path** (ชื่อโฟลเดอร์ไทย) | Export path อาจพัง บาง Windows | ใช้ `pathlib.Path` (มีอยู่แล้ว) |
| **Counter overflow** | int64 — ใช้ไม่หมดภายใน lifetime | ignore |
| **Signal queue backlog** | ถ้า UI ช้ากว่า worker → signal คิวยาว | Qt handle ให้อยู่แล้ว; trigger ปุ่ม ≠ stream |

---

## 5. P0 Production Recommendations

ก่อน deploy จริงควรเพิ่ม:

1. **Single-instance lock**
   ```python
   from PySide6.QtCore import QLockFile
   lock = QLockFile("/tmp/pipe-inspector.lock")
   if not lock.tryLock(100):
       sys.exit("Another instance is running")
   ```

2. **Try/except รอบ `save_inspection`**
   - ถ้า save fail → rollback `batch_state.increment`
   - หรืออย่างน้อย log + แจ้ง UI ว่าชิ้นล่าสุด "not saved"

3. **DB backup schedule**
   - cron/systemd timer: `sqlite3 db.sqlite ".backup backup_$(date).db"` ทุกคืน
   - เก็บ 7 ชุดล่าสุด

4. **Camera health check**
   - Worker loop: ถ้า `read()` return None > N ครั้ง → emit `camera_error` signal
   - UI แสดงสถานะ "🔴 Camera offline" + auto-reconnect

5. **(Nice-to-have) Prometheus/log aggregation**
   - inference time, error rate, storage usage → grafana

---

## 6. ไฟล์ที่เกี่ยวข้อง

| File | หน้าที่ |
|------|--------|
| `main.py` | entry point, สร้าง QApplication + MainWindow |
| `ui/main_window.py` | UI หลัก, owner ของ DB/BatchState/Worker |
| `ui/db_viewer.py` | dialog ดู/ลบ/export ข้อมูล |
| `pipeline.py` | `CameraWorker` (QThread) + `_should_save_image` |
| `batch_state.py` | in-memory batch counter + write-through |
| `database.py` | SQLite wrapper, migrations, cleanup, export helpers |
| `fix_timestamp.py` | debug helper แก้ timestamp เพื่อทดสอบ cleanup |
| `/etc/systemd/system/pipe-inspector.service` | (Jetson) service file — ปัจจุบันปิดใช้งาน |
| `~/.config/autostart/pipe-inspector.desktop` | (Jetson) XDG autostart — ใช้งานจริง |

---

_Last updated: 2026-04-21_
