"""Phase 10.5: collect observational generation demand and dry-run budgets.

This script never changes MAX_HATCH_STROKES or generation behavior. It emits
JSON to stdout so reports can be captured outside the repository without
modifying committed benchmark fixtures.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sketch import GenerationInstrumentation, image_to_strokes  # noqa: E402

BENCHMARKS = {
    "colored_skull": "colored_skull.jpeg",
    "full_color_portrait": "full_color_portrait.png",
    "landscape": "landscape.png",
    "geometric_object": "geometric_object.png",
    "low_contrast_photo": "low_contrast_photo.png",
    "near_white_illustration": "near_white_illustration.png",
}


def collect(name: str, filename: str) -> dict:
    path = Path(__file__).parent / "images" / filename
    collector = GenerationInstrumentation()
    started = time.perf_counter()
    result = image_to_strokes(path.read_bytes(), style="faithful", instrumentation=collector)
    metrics = result["instrumentation"]
    metrics["wall_seconds"] = time.perf_counter() - started
    metrics["output_strokes"] = len(result["strokes"])
    metrics["budget_simulation"] = collector.plan_budgets()
    return {"name": name, "width": result["width"], "height": result["height"], "metrics": metrics}


def main() -> None:
    print(json.dumps({name: collect(name, filename) for name, filename in BENCHMARKS.items()}, indent=2))


if __name__ == "__main__":
    main()
