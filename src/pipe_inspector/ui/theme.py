"""theme.py — HMI colour palettes and fonts.

Moved from core/constants.py during the standard-layout refactor (these are a
UI concern, not domain). The verdict/status keys reference the domain enums so
existing lookups like ``VERDICT_COLORS[Verdict.OK]`` keep working unchanged.
"""

from __future__ import annotations

from pipe_inspector.domain.enums import Verdict, WorkerStatus

#: Verdict → background hex colour (used in badges and history rows)
VERDICT_COLORS: dict[str, str] = {
    Verdict.OK: "#2e7d32",   # dark green (WCAG AA on white)
    Verdict.NG: "#c62828",   # dark red
}

#: WorkerStatus → HMI status-dot colour
STATUS_COLORS: dict[str, str] = {
    WorkerStatus.IDLE:       "#52606d",   # medium gray
    WorkerStatus.SCANNING:   "#0288d1",   # deep cyan/blue
    WorkerStatus.PROCESSING: "#ef6c00",   # dark orange
    WorkerStatus.ERROR:      "#c62828",   # dark red
    "connected":             "#2e7d32",   # dark green (camera online)
    "offline":               "#c62828",
}

#: monospace font for piece IDs, timestamps, confidence values
MONO_FONT = "Consolas"


def main_window_stylesheet() -> str:
    """Full QSS for the main window (extracted verbatim from the old MainWindow._apply_stylesheet)."""
    return """
            /* ── Global ───────────────────────────────────────────────── */
            QMainWindow, QWidget {
                background: #ffffff;
                color: #1a1d23;
                font-family: "Segoe UI", "Inter", system-ui, sans-serif;
                font-size: 14px;
            }
            QToolTip {
                background: #1a1d23;
                color: #ffffff;
                border: 1px solid #52606d;
                padding: 6px 10px;
                font-size: 13px;
            }

            /* ── Tab Bar ──────────────────────────────────────────────── */
            QTabWidget#mainTabs::pane {
                border: none;
                background: #ffffff;
            }
            QTabBar::tab {
                background: #f1f5f9;
                color: #52606d;
                padding: 12px 26px;
                border: 1px solid #e2e8f0;
                border-bottom: none;
                min-width: 160px;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 0.5px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #1565c0;
                border-bottom: 3px solid #1565c0;
            }
            QTabBar::tab:hover:!selected {
                background: #e8eef5;
                color: #1a1d23;
            }

            /* ── Header ───────────────────────────────────────────────── */
            #header {
                background: #ffffff;
                border-bottom: 1px solid #e2e8f0;
            }
            #appTitle {
                font-size: 17px;
                font-weight: bold;
                letter-spacing: 1px;
                color: #1a1d23;
            }
            #batchId {
                font-family: "Consolas", monospace;
                font-size: 14px;
                font-weight: bold;
                color: #ffffff;
                background: #1565c0;
                border: 1px solid #0d47a1;
                padding: 5px 12px;
                border-radius: 6px;
            }
            #statusText {
                font-size: 14px;
                font-weight: 600;
                color: #1a1d23;
            }
            #statusDot {
                font-size: 18px;
            }
            #inferenceLabel {
                font-family: "Consolas", monospace;
                font-size: 13px;
                font-weight: bold;
                color: #52606d;
                background: #f5f7f9;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 5px 10px;
                min-width: 70px;
            }
            #cameraBtn, #dbBtn {
                background: #ffffff;
                color: #1a1d23;
                border: 2px solid #a8b0ba;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                padding: 6px 14px;
            }
            #cameraBtn:hover, #dbBtn:hover {
                background: #e3f2fd;
                border-color: #1565c0;
                color: #0d47a1;
            }
            #cameraBtn:pressed, #dbBtn:pressed {
                background: #bbdefb;
            }

            /* ── Frame panel ──────────────────────────────────────────── */
            #framePanel {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                margin: 10px 6px 10px 10px;
            }
            #viewContainer {
                background: #1a1d23;
                border-radius: 6px;
            }

            /* ── LIVE badge ───────────────────────────────────────────── */
            #liveBadge {
                background: #c62828;
                color: #ffffff;
                border: 2px solid #ffffff;
                border-radius: 6px;
                padding: 5px 12px;
                font-family: "Consolas", monospace;
                font-size: 13px;
                font-weight: bold;
                letter-spacing: 2px;
            }

            /* ── Capture bar ──────────────────────────────────────────── */
            #captureBar {
                background: #ffffff;
                border-top: 1px solid #e2e8f0;
            }

            /* ── Mode toggle buttons ──────────────────────────────────── */
            QPushButton#modeActive {
                background: #1565c0;
                color: #ffffff;
                border: 2px solid #0d47a1;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                padding: 6px 16px;
            }
            QPushButton#modeInactive {
                background: #ffffff;
                color: #52606d;
                border: 2px solid #cbd1d9;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 600;
                padding: 6px 16px;
            }
            QPushButton#modeInactive:hover {
                background: #e3f2fd;
                color: #1565c0;
                border-color: #1565c0;
            }

            /* ── Upload button ────────────────────────────────────────── */
            #uploadBtn {
                background: #2e7d32;
                color: #ffffff;
                border: 2px solid #1b5e20;
                border-radius: 8px;
                font-size: 17px;
                font-weight: bold;
                letter-spacing: 1px;
                padding: 12px;
            }
            #uploadBtn:hover:!disabled {
                background: #388e3c;
                border-color: #1b5e20;
            }
            #uploadBtn:pressed {
                background: #1b5e20;
            }
            #uploadBtn:disabled {
                color: #a8b0ba;
                border-color: #e2e8f0;
                background: #f5f7f9;
            }

            #captureBtn {
                background: #1565c0;
                color: #ffffff;
                border: 2px solid #0d47a1;
                border-radius: 8px;
                font-size: 17px;
                font-weight: bold;
                letter-spacing: 1px;
                padding: 12px;
            }
            #captureBtn:hover:!disabled {
                background: #1976d2;
                border-color: #0d47a1;
            }
            #captureBtn:pressed {
                background: #0d47a1;
            }
            #captureBtn:disabled {
                color: #a8b0ba;
                border-color: #e2e8f0;
                background: #f5f7f9;
            }

            /* ── Info panel ───────────────────────────────────────────── */
            #infoPanel {
                background: #ffffff;
                margin: 10px 10px 10px 6px;
            }
            /* label ใน infoPanel โปร่งใส — ไม่ให้พื้นขาว global ขึ้นเป็นกล่องบนการ์ด */
            #infoPanel QLabel { background: transparent; }

            /* ── Cards ────────────────────────────────────────────────── */
            #card {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
            #cardTitle {
                font-size: 12px;
                font-weight: bold;
                letter-spacing: 2px;
                color: #52606d;
                text-transform: uppercase;
            }

            /* ── Counters ─────────────────────────────────────────────── */
            #counterBox {
                background: #f7f8fa;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
            }
            #counterLabel {
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 2px;
                color: #52606d;
            }
            #counterValue {
                font-family: "Segoe UI", "Consolas", monospace;
                font-size: 56px;
                font-weight: bold;
                line-height: 1;
            }
            #ngRate {
                font-family: "Consolas", monospace;
                font-size: 16px;
                font-weight: bold;
                color: #1a1d23;
            }
            #expectedLabel {
                font-family: "Consolas", monospace;
                font-size: 14px;
                font-weight: bold;
                color: #1a1d23;
            }
            #missingLabel {
                font-size: 13px;
                font-weight: bold;
                padding: 4px;
            }

            /* ── Canonical button palette (authoritative — child widgets inherit) ─ */
            QPushButton#primaryBtn {
                background: #1565c0;
                color: #ffffff;
                border: 2px solid #0d47a1;
                border-radius: 8px;
                font-size: 15px;
                font-weight: bold;
                letter-spacing: 0.5px;
                padding: 10px;
            }
            QPushButton#primaryBtn:hover   { background: #1976d2; }
            QPushButton#primaryBtn:pressed { background: #0d47a1; }

            QPushButton#successBtn {
                background: #2e7d32;
                color: #ffffff;
                border: 2px solid #1b5e20;
                border-radius: 8px;
                font-size: 15px;
                font-weight: bold;
                letter-spacing: 0.5px;
                padding: 10px;
            }
            QPushButton#successBtn:hover   { background: #388e3c; }
            QPushButton#successBtn:pressed { background: #1b5e20; }

            /* ── Secondary / Reset buttons ────────────────────────────── */
            #secondaryBtn {
                background: #ffffff;
                color: #1565c0;
                border: 2px solid #1565c0;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 0.5px;
                padding: 10px;
            }
            #secondaryBtn:hover    { background: #e3f2fd; }
            #secondaryBtn:pressed  { background: #bbdefb; }
            #secondaryBtn:disabled { color: #a8b0ba; border-color: #cbd1d9; background: #eef0f3; }

            /* Danger button (outline red) */
            #dangerBtn {
                background: #ffffff;
                color: #c62828;
                border: 2px solid #c62828;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                padding: 10px;
            }
            #dangerBtn:hover   { background: #ffebee; }
            #dangerBtn:pressed { background: #ffcdd2; }
            #resetBtn {
                background: #ffffff;
                color: #52606d;
                border: 2px solid #a8b0ba;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 0.5px;
                padding: 10px;
            }
            #resetBtn:hover {
                background: #fff3e0;
                color: #ef6c00;
                border-color: #ef6c00;
            }
            #resetBtn:pressed {
                background: #ffe0b2;
            }

            /* ── Verdict badges (last result) ─────────────────────────── */
            QLabel[objectName="verdictBadge_OK"] {
                background: #2e7d32;
                color: #ffffff;
                border: 2px solid #1b5e20;
                border-radius: 8px;
                font-size: 22px;
                font-weight: bold;
                letter-spacing: 2px;
                padding: 8px 0;
            }
            QLabel[objectName="verdictBadge_NG"] {
                background: #c62828;
                color: #ffffff;
                border: 2px solid #8b1e1e;
                border-radius: 8px;
                font-size: 22px;
                font-weight: bold;
                letter-spacing: 2px;
                padding: 8px 0;
            }

            /* ── Utility colours ─────────────────────────────────────── */
            #dimText  { color: #7b8794; font-size: 13px; }
            #okText   { color: #2e7d32; font-size: 14px; font-weight: bold; }
            #ngText   { color: #c62828; font-size: 14px; font-weight: bold; }

            /* ── History list ─────────────────────────────────────────── */
            #historyList {
                background: #ffffff;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                font-family: "Consolas", monospace;
                font-size: 12px;
            }
            #historyList::item {
                padding: 2px 4px;
                border-bottom: 1px solid #eef0f3;
            }

            /* ── Splitter handle ─────────────────────────────────────── */
            QSplitter::handle {
                background: #cbd1d9;
                width: 2px;
            }

            /* ── Scrollbars (bigger for touch) ─────────────────────────── */
            QScrollBar:vertical {
                background: #eef0f3;
                width: 14px;
                margin: 0;
                border-radius: 7px;
            }
            QScrollBar::handle:vertical {
                background: #a8b0ba;
                border-radius: 7px;
                min-height: 40px;
            }
            QScrollBar::handle:vertical:hover {
                background: #52606d;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }

            /* ── Labels ───────────────────────────────────────────────── */
            QLabel { color: #1a1d23; }

            /* ── Dialogs (inherit bigger fonts) ──────────────────────── */
            QDialog, QMessageBox, QInputDialog {
                background: #ffffff;
                color: #1a1d23;
                font-size: 14px;
            }
            /* label ใน alert/dialog โปร่งใส — ไม่ให้พื้น global ขึ้นเป็นกล่อง */
            QMessageBox QLabel, QInputDialog QLabel {
                background: transparent;
            }
            QDialog QPushButton, QMessageBox QPushButton, QInputDialog QPushButton {
                min-height: 34px;
                min-width: 90px;
                padding: 6px 16px;
                font-size: 14px;
                font-weight: bold;
                background: #1565c0;
                color: #ffffff;
                border: 2px solid #0d47a1;
                border-radius: 6px;
            }
            QDialog QPushButton:hover, QMessageBox QPushButton:hover, QInputDialog QPushButton:hover {
                background: #1976d2;
            }
        """


def db_viewer_stylesheet() -> str:
    """QSS for the DB viewer dialog (extracted verbatim from DbViewerDialog._apply_stylesheet)."""
    return """
            QDialog, QWidget {
                background: #eef0f3;
                color: #1a1d23;
                font-family: "Segoe UI", system-ui, sans-serif;
                font-size: 14px;
            }

            #dialogTitle {
                font-size: 17px;
                font-weight: bold;
                letter-spacing: 1px;
                color: #1a1d23;
            }

            #divider {
                color: #cbd1d9;
                background: #cbd1d9;
                max-height: 1px;
            }

            #sectionTitle {
                font-size: 12px;
                font-weight: bold;
                letter-spacing: 2px;
                color: #52606d;
                text-transform: uppercase;
            }

            /* Batch list */
            #batchList {
                background: #ffffff;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                font-family: "Consolas", monospace;
                font-size: 12px;
                padding: 2px;
            }
            #batchList::item {
                padding: 12px 10px;
                border-bottom: 1px solid #eef0f3;
                color: #1a1d23;
            }
            #batchList::item:selected {
                background: #e3f2fd;
                border-left: 4px solid #1565c0;
                color: #0d47a1;
            }
            #batchList::item:hover:!selected {
                background: #f1f3f6;
            }

            /* Inspection table */
            #inspectionTable {
                background: #ffffff;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                gridline-color: #e3e6eb;
                font-family: "Consolas", monospace;
                font-size: 13px;
                alternate-background-color: #f7f8fa;
                color: #1a1d23;
            }
            #inspectionTable::item {
                padding: 10px 8px;
            }
            #inspectionTable::item:selected {
                background: #e3f2fd;
                color: #0d47a1;
            }
            QHeaderView::section {
                background: #f1f3f6;
                color: #1a1d23;
                border: none;
                border-bottom: 2px solid #cbd1d9;
                border-right: 1px solid #e3e6eb;
                padding: 12px 8px;
                font-size: 12px;
                font-weight: bold;
                letter-spacing: 1px;
                text-transform: uppercase;
            }

            /* Stats */
            #statLabel {
                font-family: "Consolas", monospace;
                font-size: 14px;
                font-weight: bold;
                color: #1a1d23;
                padding: 4px 8px;
            }

            /* ── Top bar ─────────────────────────────────────────────── */
            #topBar {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
            /* label ใน topBar โปร่งใส — ไม่ให้พื้นเทา global ขึ้นเป็นกล่อง */
            #topBar QLabel { background: transparent; }
            #topBar QDateEdit {
                background: #ffffff;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 13px;
                min-width: 110px;
            }
            #topBar QDateEdit::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: center right;
                width: 22px;
                background: transparent;
                border-left: 1px solid #cbd1d9;
            }

            /* ── Calendar popup ──────────────────────────────────────────
               ⚠️ ต้อง re-style เพราะกฎ global "QWidget { background }" ด้านบน
               จะไหลลงไปทับ internal view ของปฏิทิน → กดเลือกวันไม่ได้     */
            QCalendarWidget QWidget {
                background: #ffffff;
                color: #1a1d23;
            }
            QCalendarWidget QAbstractItemView:enabled {
                background: #ffffff;
                color: #1a1d23;
                selection-background-color: #1565c0;
                selection-color: #ffffff;
                outline: 0;
            }
            QCalendarWidget QAbstractItemView:disabled {
                color: #cbd1d9;
            }
            QCalendarWidget QWidget#qt_calendar_navigationbar {
                background: #f7f8fa;
            }
            QCalendarWidget QToolButton {
                background: transparent;
                color: #1a1d23;
                font-size: 13px;
                font-weight: bold;
                icon-size: 18px;
                padding: 2px 6px;
            }
            QCalendarWidget QToolButton:hover {
                background: #e3f2fd;
                border-radius: 4px;
            }
            QCalendarWidget QMenu {
                background: #ffffff;
                color: #1a1d23;
            }
            QCalendarWidget QSpinBox {
                background: #ffffff;
                color: #1a1d23;
                selection-background-color: #1565c0;
                selection-color: #ffffff;
            }
            #showingLabel {
                font-size: 13px;
                color: #52606d;
                padding: 0 8px;
            }
            #pageIconBtn {
                background: #ffffff;
                color: #1565c0;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                font-size: 15px;
                font-weight: bold;
            }
            #pageIconBtn:hover:!disabled  { background: #e3f2fd; border-color: #1565c0; }
            #pageIconBtn:disabled         { color: #cbd1d9; }

            /* ── Panel header + chips ────────────────────────────────── */
            #panelHeader {
                background: transparent;
                font-size: 15px;
                font-weight: bold;
                color: #1a1d23;
            }
            #okChip {
                background: #e8f5e9;
                color: #2e7d32;
                border-radius: 11px;
                padding: 3px 12px;
                font-size: 12px;
                font-weight: bold;
            }
            #ngChip {
                background: #ffebee;
                color: #c62828;
                border-radius: 11px;
                padding: 3px 12px;
                font-size: 12px;
                font-weight: bold;
            }
            #idChip {
                background: transparent;
                color: #52606d;
                padding: 3px 4px;
                font-family: "Consolas", monospace;
                font-size: 12px;
                font-weight: bold;
            }


            /* ── Preview footer ──────────────────────────────────────── */
            #footerCaption {
                background: transparent;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
                color: #7b8794;
            }
            #defectTypeValue {
                background: transparent;
                font-size: 14px;
                font-weight: bold;
                color: #c62828;
                padding-left: 8px;
            }

            /* Button palette (secondaryBtn/dangerBtn) — defined canonically in MainWindow QSS */

            /* Big primary CLOSE button */
            #closeBtn {
                background: #1565c0;
                color: #ffffff;
                border: 2px solid #0d47a1;
                border-radius: 10px;
                font-size: 17px;
                font-weight: bold;
                letter-spacing: 2px;
                padding: 12px 24px;
            }
            #closeBtn:hover  { background: #1976d2; }
            #closeBtn:pressed { background: #0d47a1; }

            /* Image preview */
            #previewPanel {
                background: #ffffff;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                padding: 4px;
            }
            #imagePreview {
                background: #1a1d23;
                border-radius: 4px;
                color: #a8b0ba;
                font-size: 13px;
            }

            /* Splitter */
            QSplitter::handle {
                background: #cbd1d9;
                width: 2px;
            }

            /* Scrollbars (bigger for touch) */
            QScrollBar:vertical {
                background: #eef0f3;
                width: 14px;
                border-radius: 7px;
            }
            QScrollBar::handle:vertical {
                background: #a8b0ba;
                border-radius: 7px;
                min-height: 40px;
            }
            QScrollBar::handle:vertical:hover { background: #52606d; }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal {
                background: #eef0f3;
                height: 14px;
                border-radius: 7px;
            }
            QScrollBar::handle:horizontal {
                background: #a8b0ba;
                border-radius: 7px;
                min-width: 40px;
            }
            QScrollBar::handle:horizontal:hover { background: #52606d; }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal { width: 0; }

            /* Message/Confirm dialogs spawned from this viewer */
            QMessageBox {
                background: #ffffff;
                font-size: 14px;
            }
            QMessageBox QLabel {
                background: transparent;
                color: #1a1d23;
                font-size: 14px;
            }
            QMessageBox QPushButton {
                background: #1565c0;
                color: #ffffff;
                border: 2px solid #0d47a1;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                min-width: 100px;
                min-height: 38px;
                padding: 6px 18px;
            }
            QMessageBox QPushButton:hover  { background: #1976d2; }
            QMessageBox QPushButton:pressed { background: #0d47a1; }
        """
