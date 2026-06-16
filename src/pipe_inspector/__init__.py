"""pipe_inspector — PySide6 industrial vision HMI for pipe defect inspection.

Layered package (introduced incrementally during the standard-layout refactor):
    config/   — settings loader + logging setup
    domain/   — enums + batch state (no Qt, no I/O)
    vision/   — detection, inspector, camera worker, size classifier
    hardware/ — RS485 DIO + worker
    storage/  — SQLite persistence
    ui/       — PySide6 widgets/dialogs
    utils/    — pure helpers
"""

__version__ = "0.1.0"
