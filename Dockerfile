# Pipe Inspector — HEADLESS image (tests / CI / headless code paths only).
#
# ⚠️  This image deliberately does NOT run the live HMI.
#     On Windows, Docker Desktop cannot pass USB cameras or USB-RS485 serial into
#     a Linux container, and the PySide6 GUI needs an X server. Run the real
#     inspection app NATIVELY (python main.py). Use this image for `pytest`,
#     linting and headless verification with QT_QPA_PLATFORM=offscreen.
FROM python:3.12-slim

# System libraries OpenCV (opencv-python) needs at import time, even headless.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    QT_QPA_PLATFORM=offscreen \
    PIPE_INSPECTOR_ROOT=/app

WORKDIR /app

# Install dependencies + package first (better layer caching).
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install ".[dev]"

# Config (settings.yaml) + tests, copied after the heavy install layer.
COPY config/ ./config/
COPY tests/ ./tests/

CMD ["pytest", "-q"]
