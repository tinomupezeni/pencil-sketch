"""Phase 10.9 sequential A/B validation of the resource scheduler.

The scheduler override is test-only and is passed directly to image_to_strokes;
it is not part of the API request surface. Cases run one at a time and each
has a wall-time timeout so an incomplete run is reported, never treated as a
success.
"""

from __future__ import annotations

import json
import signal
import time
import tracemalloc
from pathlib import Path

import cv2
import numpy as np

from sketch import GenerationInstrumentation, image_to_strokes


class RunTimeout(Exception):
    pass


def _timeout(_signum, _frame):
    raise RunTimeout


def _png(image):
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def cases(size):
    dark = np.full((size, size, 3), 15, dtype=np.uint8)
    lighter = dark.copy()
    lighter[size // 3 : size // 2, size // 3 : size // 2] = 150
    textured = np.clip(dark.astype(np.int16) + np.random.RandomState(7).randint(0, 55, (size, size, 1)), 0, 255).astype(np.uint8)
    chroma = np.zeros((size, size, 3), dtype=np.uint8)
    chroma[:] = (20, 50, 150)
    cv2.rectangle(chroma, (size // 4, size // 4), (size // 2, size // 2), (180, 30, 30), -1)
    details = dark.copy()
    cv2.rectangle(details, (size // 4, size // 4), (3 * size // 4, 3 * size // 4), (35, 35, 35), -1)
    cv2.circle(details, (size // 2, size // 2), max(2, size // 20), (5, 5, 5), -1)
    mixed = np.full((size, size, 3), 210, dtype=np.uint8)
    cv2.rectangle(mixed, (size // 5, size // 5), (4 * size // 5, 4 * size // 5), (70, 70, 70), -1)
    cv2.circle(mixed, (size // 2, size // 2), max(3, size // 12), (5, 5, 5), -1)
    return {"uniform_near_black": dark, "near_black_lighter_region": lighter,
            "textured_dark": textured, "high_chroma_dark": chroma,
            "dark_small_detail": details, "mixed_isolated_shadow": mixed}


def run_case(name, image, enabled, timeout_seconds=45, budget=10_000):
    collector = GenerationInstrumentation()
    started = time.perf_counter()
    tracemalloc.start()
    old_handler = signal.signal(signal.SIGALRM, _timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        result = image_to_strokes(_png(image), style="faithful", instrumentation=collector,
                                  scheduler_override=enabled,
                                  scheduler_budget_override=budget)
    except RunTimeout:
        return {"case": name, "scheduler_enabled": enabled, "timeout": True,
                "elapsed_seconds": time.perf_counter() - started}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    metrics = result["instrumentation"]
    return {
        "case": name, "scheduler_enabled": enabled, "timeout": False,
        "elapsed_seconds": time.perf_counter() - started, "peak_memory_bytes": peak,
        "payload_bytes": len(json.dumps({k: v for k, v in result.items() if k != "instrumentation"})),
        "output_hatch_strokes": sum(s["kind"] == "hatch" for s in result["strokes"]),
        "dark_strokes": sum(s["kind"] == "hatch" and s["shade"] > 0.7 for s in result["strokes"]),
        "colored_strokes": sum(bool(s.get("color")) for s in result["strokes"]),
        "metrics": metrics,
    }


def main():
    outputs = []
    for size in (120, 240, 360):
        for name, image in cases(size).items():
            for enabled in (False, True):
                outputs.append(run_case(f"{name}_{size}", image, enabled))
    print(json.dumps(outputs, indent=2))


if __name__ == "__main__":
    main()
