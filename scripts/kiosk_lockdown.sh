#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────────────────
# kiosk_lockdown.sh — ล็อค GNOME บน Jetson ให้ ghost touch จากจอ touchscreen
# ย่อ/ลาก/สลับ desktop หนีจากโปรแกรมไม่ได้
#
# ทำไมต้องมีสคริปต์นี้ (Qt อย่างเดียวไม่พอ):
#   อาการ "ปัดแล้วไปโผล่ desktop เปล่า" คือ GNOME Shell ดักนิ้วไปทำ gesture
#   (ปัดหลายนิ้ว = สลับ workspace) — คอมโพสิเตอร์กินตั้งแต่ก่อนถึงแอป
#   ฝั่งแอปล็อคไว้แล้วที่ KIOSK_MODE ใน ui/main_window.py
#
# ใช้งาน (รันเป็น user ปกติของ desktop — ห้าม sudo):
#     bash scripts/kiosk_lockdown.sh            # ล็อค
#     bash scripts/kiosk_lockdown.sh --status   # ดูค่าปัจจุบัน
#     bash scripts/kiosk_lockdown.sh --restore  # ถอนคืนค่าเดิมทั้งหมด
#     bash scripts/kiosk_lockdown.sh --probe    # สำรวจอุปกรณ์ touchscreen
#
# หมายเหตุ: ค่าทั้งหมดเก็บใน dconf ของ user → คงอยู่ข้าม reboot, ถอนคืนได้เสมอ
# ───────────────────────────────────────────────────────────────────────────
set -uo pipefail   # ไม่ใช้ -e — คีย์ไหนไม่มีในเครื่องนี้ให้ข้ามแล้วไปต่อ

GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; RED=$'\033[0;31m'; NC=$'\033[0m'

# ── ตารางค่าเดียว ใช้ร่วมกันทั้ง apply / restore / status ──────────────────
#    รูปแบบ: schema|key|ค่าที่ต้องการตอนล็อค
SETTINGS=(
  # 1) ตัดต้นตอ "สลับไป desktop ใหม่" — บังคับเหลือ workspace เดียว ปัดแล้วไม่มีที่ให้ไป
  "org.gnome.mutter|dynamic-workspaces|false"
  "org.gnome.shell.overrides|dynamic-workspaces|false"     # GNOME รุ่นเก่าเก็บคีย์ไว้ที่นี่
  "org.gnome.desktop.wm.preferences|num-workspaces|1"

  # 2) ปิดทางเข้า Activities/overview (มุมจอ + ปุ่ม Super)
  "org.gnome.desktop.interface|enable-hot-corners|false"
  "org.gnome.mutter|overlay-key|''"

  # 3) ถอดคีย์ลัดที่ย้าย/ย่อ/ปิดหน้าต่าง หรือสลับ workspace/แอป
  "org.gnome.desktop.wm.keybindings|minimize|[]"
  "org.gnome.desktop.wm.keybindings|close|[]"
  "org.gnome.desktop.wm.keybindings|begin-move|[]"
  "org.gnome.desktop.wm.keybindings|begin-resize|[]"
  "org.gnome.desktop.wm.keybindings|toggle-maximized|[]"
  "org.gnome.desktop.wm.keybindings|show-desktop|[]"
  "org.gnome.desktop.wm.keybindings|switch-to-workspace-left|[]"
  "org.gnome.desktop.wm.keybindings|switch-to-workspace-right|[]"
  "org.gnome.desktop.wm.keybindings|switch-to-workspace-up|[]"
  "org.gnome.desktop.wm.keybindings|switch-to-workspace-down|[]"
  "org.gnome.desktop.wm.keybindings|move-to-workspace-left|[]"
  "org.gnome.desktop.wm.keybindings|move-to-workspace-right|[]"
  "org.gnome.desktop.wm.keybindings|switch-applications|[]"
  "org.gnome.desktop.wm.keybindings|switch-applications-backward|[]"
  "org.gnome.shell.keybindings|toggle-overview|[]"
  "org.gnome.shell.keybindings|toggle-application-view|[]"

  # 4) จอโรงงาน — ห้ามดับ/ล็อค/หลับ (ไฟดับกลางกะ = operator กดไม่ได้)
  "org.gnome.desktop.session|idle-delay|0"
  "org.gnome.desktop.screensaver|lock-enabled|false"
  "org.gnome.desktop.screensaver|idle-activation-enabled|false"
  "org.gnome.settings-daemon.plugins.power|sleep-inactive-ac-type|'nothing'"
)

# ── ตรวจว่ารันในบริบทที่ถูกต้อง (user ของ desktop ไม่ใช่ root/ssh เปล่าๆ) ──
check_session() {
  if [ "$(id -u)" -eq 0 ]; then
    echo "${RED}✗ ห้ามรันด้วย sudo/root${NC} — ค่านี้เก็บใน dconf ของ user"
    echo "  รันด้วย user ที่ login desktop อยู่ (เช่น jetson) แทน"
    exit 1
  fi
  if ! command -v gsettings >/dev/null 2>&1; then
    echo "${RED}✗ ไม่พบคำสั่ง gsettings${NC} — เครื่องนี้อาจไม่ได้ใช้ GNOME"
    exit 1
  fi
  if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
    echo "${YELLOW}⚠ ไม่มี DBUS_SESSION_BUS_ADDRESS${NC} — ถ้าต่อผ่าน SSH ค่าอาจไม่มีผลกับ desktop"
    echo "  แนะนำ: เปิด terminal บนหน้าจอ Jetson แล้วรันที่นั่น"
  fi
}

# มีคีย์นี้ในเครื่องนี้จริงไหม (GNOME แต่ละรุ่นคีย์ไม่เหมือนกัน)
key_exists() {
  gsettings list-keys "$1" 2>/dev/null | grep -qx "$2"
}

do_apply() {
  echo "── ล็อค GNOME (kiosk) ──────────────────────────────────"
  local ok=0 skip=0
  for row in "${SETTINGS[@]}"; do
    IFS='|' read -r schema key val <<< "$row"
    if key_exists "$schema" "$key"; then
      if gsettings set "$schema" "$key" "$val" 2>/dev/null; then
        printf "  ${GREEN}✓${NC} %-46s = %s\n" "$schema $key" "$val"
        ok=$((ok + 1))
      else
        printf "  ${RED}✗${NC} %-46s (set ไม่สำเร็จ)\n" "$schema $key"
      fi
    else
      printf "  ${YELLOW}–${NC} %-46s (ไม่มีคีย์นี้ใน GNOME รุ่นนี้ — ข้าม)\n" "$schema $key"
      skip=$((skip + 1))
    fi
  done
  echo ""
  echo "เสร็จ: ตั้งได้ $ok ค่า / ข้าม $skip ค่า"
  echo ""
  echo "${YELLOW}ทดสอบต่อ:${NC} ลองปัดหลายนิ้ว / ลากขอบจอ / Super / Alt+Tab / Ctrl+Alt+←→"
  echo "          → โปรแกรมต้องนิ่ง ไม่ย่อ ไม่สลับ desktop"
  echo "${YELLOW}ถอนคืน:${NC}  bash $0 --restore"
}

do_restore() {
  echo "── ถอนคืนค่า GNOME เดิม ────────────────────────────────"
  for row in "${SETTINGS[@]}"; do
    IFS='|' read -r schema key _ <<< "$row"
    if key_exists "$schema" "$key"; then
      gsettings reset "$schema" "$key" 2>/dev/null \
        && printf "  ${GREEN}✓${NC} reset %s %s\n" "$schema" "$key"
    fi
  done
  echo ""
  echo "คืนค่าเรียบร้อย — อาจต้อง log out/in ให้ GNOME โหลดค่าใหม่ครบ"
}

do_status() {
  echo "── ค่าปัจจุบัน (เทียบกับค่าที่ kiosk ต้องการ) ──────────"
  for row in "${SETTINGS[@]}"; do
    IFS='|' read -r schema key want <<< "$row"
    if key_exists "$schema" "$key"; then
      cur="$(gsettings get "$schema" "$key" 2>/dev/null)"
      # ตัด quote/ช่องว่างออกก่อนเทียบ — gsettings get คืนรูปแบบต่างจากที่เราตั้งเล็กน้อย
      if [ "$(echo "$cur" | tr -d " '\"")" = "$(echo "$want" | tr -d " '\"")" ]; then
        printf "  ${GREEN}✓${NC} %-46s = %s\n" "$schema $key" "$cur"
      else
        printf "  ${RED}✗${NC} %-46s = %s ${YELLOW}(ควรเป็น %s)${NC}\n" "$schema $key" "$cur" "$want"
      fi
    else
      printf "  ${YELLOW}–${NC} %-46s (ไม่มีคีย์นี้)\n" "$schema $key"
    fi
  done
}

# ── สำรวจ touchscreen — ดูว่า driver ให้ตั้งอะไรได้บ้าง ────────────────────
# (ยังไม่ตั้งค่าอะไร — เอา output ไปดูก่อนว่ามี property จำกัดจำนวนนิ้วไหม
#  ถ้าไม่มี ก็ไม่เป็นไร ข้อ 1-3 ข้างบนตัดผลลัพธ์ของ gesture ไปแล้ว)
do_probe() {
  if ! command -v xinput >/dev/null 2>&1; then
    echo "${YELLOW}ไม่มีคำสั่ง xinput${NC} — ติดตั้ง: sudo apt install xinput"
    exit 1
  fi
  echo "── อุปกรณ์ input ทั้งหมด ───────────────────────────────"
  xinput list
  echo ""
  echo "── properties ของอุปกรณ์ที่ชื่อมี touch ────────────────"
  local found=0
  while IFS= read -r line; do
    id="$(echo "$line" | grep -oP 'id=\K[0-9]+')"
    [ -z "$id" ] && continue
    found=1
    echo ""
    echo "${GREEN}▶ $(echo "$line" | sed 's/^[^A-Za-z]*//; s/[[:space:]]*id=.*//')  (id=$id)${NC}"
    xinput list-props "$id"
  done < <(xinput list --short 2>/dev/null | grep -i "touch")
  [ "$found" -eq 0 ] && echo "  (ไม่เจออุปกรณ์ที่ชื่อมีคำว่า touch — ดูจากรายการข้างบนเอง)"
  echo ""
  echo "${YELLOW}ส่งผลนี้กลับมาให้ทีมดู${NC} ถ้ามี property จำกัดจำนวนนิ้ว/ปิด gesture"
  echo "จะได้เติมคำสั่งลงสคริปต์นี้ให้ (ตอนนี้ยังไม่ตั้งค่าอะไรกับอุปกรณ์)"
}

case "${1:-}" in
  ""|--apply)  check_session; do_apply   ;;
  --restore)   check_session; do_restore ;;
  --status)    check_session; do_status  ;;
  --probe)     do_probe ;;
  -h|--help)   sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//' ;;
  *)           echo "ไม่รู้จักตัวเลือก: $1"; echo "ใช้: $0 [--apply|--status|--restore|--probe]"; exit 1 ;;
esac
