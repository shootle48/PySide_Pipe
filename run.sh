#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────
# Pipe Inspector (Phase 1) — Jetson launcher
# ให้ Operator กดเปิดจากไอคอนบนหน้าจอ Jetson ได้ (เช่นหลังไฟดับ / แอปปิดเอง)
#
# Jetson ใช้ "system python3" + opencv จาก apt (python3-opencv)
# → ไม่ activate venv (กัน pip opencv ชน Qt ของ PySide6 ตามที่ deploy guide เตือน)
# ───────────────────────────────────────────────────────────────
set -e
ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$ROOT"

# Jetson ต้องมี DISPLAY — ใช้ของ session ถ้ามี (เปิดจากไอคอน) ไม่งั้น default :0 (จอ HDMI)
export DISPLAY="${DISPLAY:-:0}"

exec python3 main.py
