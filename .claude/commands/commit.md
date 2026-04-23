---
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*), Bash(git diff:*)
argument-hint: [message]
description: Create a git commit for Pipe Inspector PySide project
---

## Context

- Current git status: !`git status`
- Current git diff: !`git diff HEAD`
- Current branch: !`git branch --show-current`
- Recent commits: !`git log --oneline -10`

## Project Structure (for reference)

```
pipe-inspector-pyside/
├── main.py              ← entry point
├── core/
│   ├── pipeline.py      ← detection pipeline (CV logic)
│   ├── database.py      ← DB read/write
│   └── batch_state.py   ← batch processing state
└── ui/
    ├── main_window.py   ← main PySide window
    ├── frame_widget.py  ← video frame display widget
    ├── db_viewer.py     ← database viewer panel
    └── camera_select_dialog.py ← camera selection dialog
```

## Your task

Based on the above changes, create a single git commit.

If a message was provided via arguments, use it: $ARGUMENTS

Otherwise, analyze the changes and write a commit message using this format:

```
<type>(<scope>): <short description>
```

### Types for this project:

| Type | ใช้เมื่อ | Examples |
|------|---------|---------|
| `feat` | เพิ่ม feature ใหม่ | ปุ่มใหม่, logic ใหม่ |
| `fix` | แก้ bug | detection ผิด, crash, UI พัง |
| `ui` | เปลี่ยน PySide UI เท่านั้น | layout, style, widget |
| `core` | เปลี่ยน pipeline/database/batch | algorithm, DB schema |
| `refactor` | ปรับโค้ด พฤติกรรมเดิม | rename, split function |
| `perf` | เพิ่ม performance | ลด lag, เร็วขึ้น |
| `docs` | เปลี่ยน README/ARCHITECTURE | documentation |
| `chore` | งาน maintenance | requirements.txt, .gitignore |

### Scopes (ใส่ตามไฟล์ที่แก้):

`pipeline` | `database` | `batch` | `main-window` | `frame-widget` | `db-viewer` | `camera-dialog` | `main`

### Examples:

```
feat(pipeline): add defect severity scoring
fix(frame-widget): fix freeze when camera disconnects
ui(main-window): adjust layout for smaller screens
core(database): add index on batch_id for faster query
refactor(pipeline): split _detect_defects into smaller functions
```

### Rules:
- ใช้ภาษาอังกฤษ lowercase
- short description ไม่เกิน 72 ตัวอักษร
- ถ้าแก้หลายไฟล์คนละ scope ให้ยก scope ที่สำคัญที่สุด
- ห้าม commit ไฟล์ใน `logs/`, `exports/`, `data/` (runtime output เท่านั้น)
