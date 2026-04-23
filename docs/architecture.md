# Architecture

ระบบ Pipe Inspector (PySide6) บน Jetson Orin Nano

---

## System Overview

```mermaid
graph TD
    subgraph UI Layer
        MW[MainWindow]
        FW[FrameWidget]
        DV[DbViewerDialog]
        CS[CameraSelectDialog]
    end

    subgraph Core Layer
        CW[CameraWorker / QThread]
        PI[PipeInspector / CV]
        BS[BatchStateManager]
        DB[DatabaseManager / SQLite]
    end

    MW -->|owns| CW
    MW -->|owns| BS
    MW -->|owns| DB
    MW -->|opens| DV
    MW -->|opens| CS

    CW -->|frame_ready signal| FW
    CW -->|result_ready signal| MW
    CW -->|status_changed signal| MW
    CW --> PI
    CW --> BS
    CW --> DB

    BS -->|write-through| DB
    DV -->|query/export| DB
```

---

## Component Responsibilities

| Component | ไฟล์ | หน้าที่ |
|---|---|---|
| `MainWindow` | `ui/main_window.py` | Owner ทุกอย่าง, wiring signals, button handlers |
| `FrameWidget` | `ui/frame_widget.py` | QPainter canvas แสดง live frame + bbox overlay |
| `DbViewerDialog` | `ui/db_viewer.py` | ดู/ลบ/export ข้อมูล batch |
| `CameraWorker` | `core/pipeline.py` | QThread — capture, inspect, emit signals |
| `PipeInspector` | `core/pipeline.py` | OpenCV defect detection (HoughCircles + Threshold) |
| `BatchStateManager` | `core/batch_state.py` | In-memory counter + write-through to DB |
| `DatabaseManager` | `core/database.py` | SQLite WAL, threading.Lock, migrations |

---

## Signal Flow

### Capture Workflow

```mermaid
sequenceDiagram
    actor User
    participant MW as MainWindow
    participant CW as CameraWorker
    participant PI as PipeInspector
    participant BS as BatchStateManager
    participant DB as DatabaseManager

    User->>MW: click "Capture & Inspect"
    MW->>CW: trigger() — set flag
    CW->>CW: read frame from VideoCapture
    CW->>PI: inspect(frame)
    PI-->>CW: result dict (verdict, confidence, detections)
    CW->>BS: increment(verdict)
    BS->>DB: update_batch_counters()
    CW->>DB: save_inspection(piece_id, ...)
    CW-->>MW: emit result_ready(result, snapshot)
    MW->>MW: update counters + preview
```

### Startup Workflow

```mermaid
flowchart TD
    A[XDG Autostart / Manual] --> B[main.py]
    B --> C[QApplication]
    C --> D[MainWindow.__init__]
    D --> E[DatabaseManager\nopen SQLite WAL\nCREATE TABLE IF NOT EXISTS\nrun migrations]
    E --> F[db.cleanup_old_data\nDELETE > 90 days]
    F --> G[BatchStateManager._initialize\nrecover active batch\nrestore seq counter]
    G --> H[CameraWorker.start]
    H --> I[app.exec — event loop]
```

### Reset Batch Workflow

```mermaid
flowchart LR
    A[click Reset Batch] --> B[QInputDialog\nget expected_total]
    B --> C[batch_state.reset]
    C --> D[db.close_active_batch]
    D --> E[db.create_batch]
    E --> F[_BatchData reset\ncounters = 0]
```

---

## Thread Model

```mermaid
graph LR
    subgraph Main Thread Qt event loop
        MW[MainWindow]
        FW[FrameWidget]
        DV[DbViewerDialog]
    end

    subgraph Worker Thread QThread
        CW[CameraWorker]
        PI[PipeInspector]
    end

    CW -- "Qt Signal cross-thread safe" --> MW
    CW -- "frame_ready ~20fps" --> FW
    CW -- "threading.Lock" --> DB[(SQLite DB)]
    MW -- "LIMIT query" --> DB
```

**Key rules:**
- UI updates เกิดใน main thread เท่านั้น (Qt requirement)
- DB access ทุกทางผ่าน `threading.Lock` (single writer = CameraWorker)
- `WAL mode` ให้ DbViewer อ่านพร้อม worker เขียนได้

---

## Design Rationale

| Decision | ทำไม | ทางเลือกที่ปฏิเสธ |
|---|---|---|
| QThread + Qt Signals | UI ไม่ freeze ตอน inference (~150ms), auto-queue ข้าม thread | asyncio — loop ไม่เข้ากัน; multiprocessing — overhead |
| SQLite + WAL | concurrent read ขณะเขียน, single file, zero-config | Postgres — overkill; JSON file — ไม่ atomic |
| threading.Lock บน connection | single writer (worker thread) เพียงพอ | connection pool — ไม่คุ้ม |
| In-memory state + write-through | อ่าน counter เร็ว, DB เป็น source of truth ตอน recovery | เขียน DB ทุก read — slow |
| Base64 JPEG ใน DB | atomic กับ metadata, backup ก็อปไฟล์เดียว | path เก็บแยก — race condition cleanup |
| seq แยกจาก total | piece_id ต้อง unique แม้หลังลบ record | COUNT(*) — ลบแล้ว counter ลด → ID ชน |
| OK Sampling (every-N + ratio cap) | เก็บ NG ครบ, OK สุ่มพอเทียบได้ ไม่กินที่ | เก็บทุกชิ้น — SD เต็มเร็ว |

---

## Edge Cases

### 🔴 HIGH Severity

| Case | ผลกระทบ | Mitigation |
|---|---|---|
| Multiple instances พร้อมกัน | seq ชน, DB lock contention | **P0**: QLockFile (`/tmp/pipe-inspector.lock`) |
| Clock skew (ไม่มี RTC battery) | timestamp ย้อนกลับ → cleanup ลบผิด | NTP sync ตอน boot |
| Storage full mid-save | record หาย, counter inconsistent | try/except รอบ save + rollback |
| Camera unplug ขณะรัน | UI ค้าง preview เก่า | health check + reconnect loop |

### 🟡 MEDIUM Severity

| Case | ผลกระทบ | Mitigation |
|---|---|---|
| Worker shutdown timeout | DB write ไม่จบ | QThread.wait(timeout) + flush ใน closeEvent |
| DB corruption | เปิดไม่ได้ | WAL ลดความเสี่ยง + nightly backup |

---

## P0 Production Checklist

- [ ] Single-instance lock (`QLockFile`)
- [ ] try/except รอบ `save_inspection` + rollback
- [ ] Nightly DB backup (cron/systemd timer)
- [ ] Camera health check + auto-reconnect + UI indicator
- [ ] NTP sync ที่ Jetson boot

ดูตัวอย่าง code สำหรับแต่ละรายการได้ที่ [Deployment Guide](deployment.md#p0-hardening)
