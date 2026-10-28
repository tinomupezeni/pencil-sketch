"""Phase 10.4: measures the real Canvas-rendered screenshots (produced by
benchmark-capture.js, see benchmarks/README.md) and merges the results
into each fixture's JSON under a `renderedMetrics` key.

Run after benchmarks/screenshots/<name>-100pct.png exist. This script
itself needs only OpenCV, not Playwright -- it's the "read the pixels"
half, separate from the "drive a real browser" half.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

BENCH_DIR = Path(__file__).resolve().parent
SCREENSHOTS_DIR = BENCH_DIR / "screenshots"
FIXTURES_DIR = BENCH_DIR / "fixtures"

NAMES = [
    "colored_skull",
    "full_color_portrait",
    "landscape",
    "geometric_object",
    "low_contrast_photo",
    "near_white_illustration",
]

DARK_THRESHOLD = 100  # out of 255: pixels at/below this count as "dark coverage"


def measure(name: str) -> dict:
    path = SCREENSHOTS_DIR / f"{name}-100pct.png"
    img = cv2.imread(str(path))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    avg_luminance = float(gray.mean())
    dark_coverage_pct = float((gray <= DARK_THRESHOLD).mean() * 100)
    # Background cleanliness: the lightest 15% of pixels' own average --
    # should sit near 255 regardless of how dark the subject is, since a
    # clean background has nothing to do with how dark the subject got.
    bg_cleanliness = float(np.percentile(gray, 90))

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    chroma = np.sqrt((lab[..., 1] - 128.0) ** 2 + (lab[..., 2] - 128.0) ** 2)
    color_preserved = bool((chroma > 18.0).mean() > 0.001)  # >0.1% of pixels meaningfully chromatic

    return {
        "avgLuminance": round(avg_luminance, 1),
        "darkCoveragePct": round(dark_coverage_pct, 2),
        "backgroundCleanliness": round(bg_cleanliness, 1),
        "colorPreserved": color_preserved,
    }


def main() -> None:
    for name in NAMES:
        fixture_path = FIXTURES_DIR / f"{name}.json"
        fixture = json.loads(fixture_path.read_text())
        fixture["renderedMetrics"] = measure(name)
        fixture_path.write_text(json.dumps(fixture, indent=2) + "\n")
        rm = fixture["renderedMetrics"]
        print(
            f"{name:24s} avgLum={rm['avgLuminance']:6.1f} darkCov={rm['darkCoveragePct']:5.1f}% "
            f"bgClean={rm['backgroundCleanliness']:6.1f} color={rm['colorPreserved']}"
        )


if __name__ == "__main__":
    sys.exit(main())
