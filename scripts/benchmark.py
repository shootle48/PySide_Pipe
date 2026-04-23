"""
benchmark.py
────────────
วัด performance ของ CV pipeline โดยตรง — ไม่มี UI เกี่ยวข้อง
ใช้ได้ทั้ง pipe-inspector-pyside/ และ pipe-inspector/backend/

วัด:
  - Inference latency  (ms per frame)
  - Throughput         (FPS)
  - CPU / RAM usage    (via psutil)
  - Startup time       (import + init)

รัน:
    python benchmark.py --image /path/to/test.jpg --n 30
    python benchmark.py --camera 0 --n 30
"""

from __future__ import annotations

import argparse
import gc
import logging
import statistics
import sys
import time
from pathlib import Path

# Allow importing from project root when running as: python scripts/benchmark.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING)   # ปิด log ระหว่าง benchmark

# ── Optional: psutil สำหรับ CPU/RAM ───────────────────────────────────────
try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False
    print("[WARN] psutil not installed — CPU/RAM metrics skipped.")
    print("       Install: pip install psutil\n")

import cv2
import numpy as np


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _get_process_stats() -> dict:
    if not _PSUTIL:
        return {}
    proc = psutil.Process()
    return {
        "cpu_pct": proc.cpu_percent(interval=0.1),
        "ram_mb":  proc.memory_info().rss / 1024 / 1024,
    }

def _print_bar(label: str, width: int = 60) -> None:
    print(f"\n{'─' * width}")
    print(f"  {label}")
    print(f"{'─' * width}")

def _print_stats(label: str, values_ms: list[float]) -> None:
    if not values_ms:
        return
    print(f"\n  {label}")
    print(f"    Min    : {min(values_ms):>8.2f} ms")
    print(f"    Max    : {max(values_ms):>8.2f} ms")
    print(f"    Mean   : {statistics.mean(values_ms):>8.2f} ms")
    print(f"    Median : {statistics.median(values_ms):>8.2f} ms")
    print(f"    Stdev  : {statistics.stdev(values_ms) if len(values_ms) > 1 else 0:>8.2f} ms")
    print(f"    FPS    : {1000 / statistics.mean(values_ms):>8.1f}")


# ══════════════════════════════════════════════════════════════════════════════
# Import timer — วัด startup cost
# ══════════════════════════════════════════════════════════════════════════════

def measure_import_time() -> float:
    """วัดเวลา import + สร้าง PipeInspector object"""
    import importlib, time
    t0 = time.perf_counter()
    import pipeline as _p
    inspector = _p.PipeInspector()
    elapsed = (time.perf_counter() - t0) * 1000
    return elapsed


# ══════════════════════════════════════════════════════════════════════════════
# Main benchmark
# ══════════════════════════════════════════════════════════════════════════════

def run_benchmark(
    frames:     list[np.ndarray],
    n_runs:     int,
    label:      str,
) -> list[float]:
    """
    รัน PipeInspector.inspect() กับ frame list ซ้ำ n_runs รอบ
    คืน list of latencies (ms)
    """
    from core.pipeline import PipeInspector
    inspector = PipeInspector()

    latencies = []
    n_frames  = len(frames)

    # Warmup — 3 รอบแรกไม่นับ (JIT / cache warmup)
    print(f"\n  Warming up (3 frames)...", end="", flush=True)
    for i in range(min(3, n_frames)):
        inspector.inspect(frames[i % n_frames])
    print(" done")

    # Benchmark
    print(f"  Running {n_runs} iterations...", end="", flush=True)

    stats_before = _get_process_stats()
    t_total_start = time.perf_counter()

    for i in range(n_runs):
        frame = frames[i % n_frames]
        t0 = time.perf_counter()
        result = inspector.inspect(frame)
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000)
        if (i + 1) % 10 == 0:
            print(f" {i+1}", end="", flush=True)

    t_total = (time.perf_counter() - t_total_start)
    stats_after = _get_process_stats()

    print(f"\n\n  Total time  : {t_total:.2f} s for {n_runs} frames")
    print(f"  Throughput  : {n_runs / t_total:.1f} FPS (end-to-end)")

    if stats_before and stats_after:
        print(f"  CPU usage   : {stats_after['cpu_pct']:.1f}%")
        print(f"  RAM usage   : {stats_after['ram_mb']:.1f} MB")

    return latencies


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipe Inspector CV Pipeline Benchmark",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--image",  type=str, help="Path to a test image file")
    src.add_argument("--camera", type=int, help="Camera index (e.g. 0)")
    parser.add_argument("--n", type=int, default=30, help="Number of iterations (default 30)")
    parser.add_argument("--frames", type=int, default=5,
                        help="Number of distinct frames to capture from camera (default 5)")
    args = parser.parse_args()

    print("\n" + "═" * 60)
    print("  PIPE INSPECTOR — CV PIPELINE BENCHMARK")
    print("═" * 60)
    print(f"  Iterations : {args.n}")
    print(f"  Source     : {'image: ' + args.image if args.image else 'camera: ' + str(args.camera)}")

    # ── Import time ────────────────────────────────────────────────────
    _print_bar("1. IMPORT + INIT TIME")
    import_ms = measure_import_time()
    print(f"  PipeInspector init : {import_ms:.1f} ms")

    # ── Prepare frames ─────────────────────────────────────────────────
    _print_bar("2. LOADING FRAMES")
    frames = []

    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            print(f"  ERROR: cannot read image: {args.image}")
            sys.exit(1)
        frames = [frame]
        print(f"  Loaded: {args.image}  ({frame.shape[1]}×{frame.shape[0]})")

    else:
        print(f"  Opening camera {args.camera}...", end="", flush=True)
        cap = cv2.VideoCapture(args.camera)
        if not cap.isOpened():
            print(f"\n  ERROR: cannot open camera {args.camera}")
            sys.exit(1)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        # Warmup
        for _ in range(10):
            cap.read()

        # Capture distinct frames
        for i in range(args.frames):
            ret, frame = cap.read()
            if ret and frame is not None:
                frames.append(frame)
        cap.release()
        print(f" captured {len(frames)} frames ({frames[0].shape[1]}×{frames[0].shape[0]})")

    if not frames:
        print("  ERROR: no frames available.")
        sys.exit(1)

    # ── Run benchmark ──────────────────────────────────────────────────
    _print_bar("3. INFERENCE BENCHMARK")
    latencies = run_benchmark(frames, n_runs=args.n, label="CV Pipeline")

    # ── Results ────────────────────────────────────────────────────────
    _print_bar("4. RESULTS SUMMARY")
    _print_stats("Inference latency per frame", latencies)

    # Grade
    mean_ms = statistics.mean(latencies)
    if mean_ms < 50:
        grade = "🟢 EXCELLENT  (real-time capable)"
    elif mean_ms < 150:
        grade = "🟡 GOOD       (suitable for stop-and-go)"
    elif mean_ms < 500:
        grade = "🟠 ACCEPTABLE (stop-and-go with margin)"
    else:
        grade = "🔴 SLOW       (may need tuning)"

    print(f"\n  Performance Grade: {grade}")
    print(f"\n  Stop-and-go margin:")
    print(f"    Pipe stop window : ~2000 ms")
    print(f"    Inference time   : {mean_ms:.0f} ms")
    print(f"    Remaining margin : {2000 - mean_ms:.0f} ms")

    # ── System info ────────────────────────────────────────────────────
    _print_bar("5. SYSTEM INFO")
    try:
        import platform
        print(f"  Python  : {platform.python_version()}")
        print(f"  OpenCV  : {cv2.__version__}")
        print(f"  NumPy   : {np.__version__}")
        if _PSUTIL:
            mem = psutil.virtual_memory()
            print(f"  RAM     : {mem.used/1024/1024:.0f} MB used / {mem.total/1024/1024:.0f} MB total")
            print(f"  CPU     : {psutil.cpu_count()} cores @ {psutil.cpu_freq().current:.0f} MHz")
    except Exception:
        pass

    print("\n" + "═" * 60 + "\n")


if __name__ == "__main__":
    main()
