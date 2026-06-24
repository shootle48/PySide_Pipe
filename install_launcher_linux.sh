#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────
# ติดตั้ง desktop launcher (ไอคอนกดได้) บน Jetson — รันครั้งเดียว:
#     bash install_launcher_linux.sh
# ได้ไอคอน "Pipe Inspector" ทั้งบน Desktop และในเมนูแอป → operator กดเปิดเองได้
# ───────────────────────────────────────────────────────────────
set -e
ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

ICON="$ROOT/assets/icon.svg"
[ -f "$ICON" ] || ICON="applications-engineering"   # fallback ไอคอน stock

chmod +x "$ROOT/run.sh" 2>/dev/null || true

ENTRY="[Desktop Entry]
Type=Application
Version=1.0
Name=Pipe Inspector
Comment=Pipe defect inspection (Phase 1)
Exec=$ROOT/run.sh
Icon=$ICON
Terminal=false
Categories=Utility;Science;
StartupNotify=true"

# 1) ลงในเมนูแอป (ค้นหาเจอใน Activities)
APPDIR="$HOME/.local/share/applications"
mkdir -p "$APPDIR"
printf '%s\n' "$ENTRY" > "$APPDIR/pipe-inspector.desktop"
chmod +x "$APPDIR/pipe-inspector.desktop"
update-desktop-database "$APPDIR" 2>/dev/null || true

# 2) วางไอคอนบน Desktop + mark trusted (ไม่งั้น GNOME ขึ้น "Untrusted")
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
mkdir -p "$DESK"
cp "$APPDIR/pipe-inspector.desktop" "$DESK/pipe-inspector.desktop"
chmod +x "$DESK/pipe-inspector.desktop"
gio set "$DESK/pipe-inspector.desktop" metadata::trusted true 2>/dev/null || true

echo "✅ ติดตั้งเสร็จ"
echo "   • Desktop : ไอคอน 'Pipe Inspector' (ดับเบิลคลิกเปิดได้)"
echo "   • เมนูแอป : Activities แล้วพิมพ์ 'Pipe'"
echo "   ถ้า Desktop ยังขึ้น 'Allow Launching' → คลิกขวาไอคอน เลือก Allow Launching"
