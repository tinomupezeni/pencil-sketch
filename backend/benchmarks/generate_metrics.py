"""Phase 10.4: regenerates backend/benchmarks/fixtures/<name>.json -- the
automated-correctness half of the benchmark suite (see benchmarks/README.md
for the split between this and verify_rendered.py).

Deliberately independent of a running server: calls sketch.image_to_strokes
and planner.build_drawing_plan directly, the same pipeline both API
endpoints funnel through, so these numbers are real pipeline output, not a
hand-picked subset. Every image is processed with style="faithful" (the
richer of the two styles -- also exercises color preservation) and no
target_duration_seconds (so these are the full, duration-independent
output Phase 10.1 guarantees).

Run after any intentional engine change, inspect the diff against the
previous fixture, and only commit the new fixture once a human has
visually re-approved the corresponding screenshots from verify_rendered.py
-- see benchmarks/README.md section "Updating a fixture after an intended
change".
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner import build_drawing_plan  # noqa: E402
from sketch import image_to_strokes  # noqa: E402

BENCH_DIR = Path(__file__).resolve().parent
IMAGES_DIR = BENCH_DIR / "images"
FIXTURES_DIR = BENCH_DIR / "fixtures"

IMAGES = {
    "colored_skull": "colored_skull.jpeg",
    "full_color_portrait": "full_color_portrait.png",
    "landscape": "landscape.png",
    "geometric_object": "geometric_object.png",
    "low_contrast_photo": "low_contrast_photo.png",
    "near_white_illustration": "near_white_illustration.png",
}


def _stroke_counts(strokes: list[dict]) -> dict:
    counts = {"line": 0, "hatch": 0, "colored": 0, "total": len(strokes)}
    for s in strokes:
        counts[s["kind"]] = counts.get(s["kind"], 0) + 1
        if s.get("color"):
            counts["colored"] += 1
    return counts


def generate_one(name: str, filename: str) -> dict:
    path = IMAGES_DIR / filename
    data = path.read_bytes()

    t0 = time.time()
    raw = image_to_strokes(data, style="faithful")
    plan = build_drawing_plan(raw["width"], raw["height"], raw["strokes"])
    processing_seconds = time.time() - t0

    dumped = plan.model_dump(mode="json")
    for stroke in dumped["strokes"]:
        if stroke.get("color") is None:
            stroke.pop("color", None)
    payload_bytes = len(json.dumps(dumped))

    fixture = {
        "name": name,
        "sourceFile": filename,
        "style": "faithful",
        "width": raw["width"],
        "height": raw["height"],
        "strokeCounts": _stroke_counts(dumped["strokes"]),
        "processingSeconds": round(processing_seconds, 3),
        "payloadBytes": payload_bytes,
        "durationPlanning": dumped["metadata"]["durationPlanning"],
        "stageCounts": dumped["metadata"]["stageCounts"],
        "totalEstimatedSeconds": dumped["metadata"]["totalEstimatedSeconds"],
    }
    return fixture


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for name, filename in IMAGES.items():
        fixture = generate_one(name, filename)
        out_path = FIXTURES_DIR / f"{name}.json"
        out_path.write_text(json.dumps(fixture, indent=2) + "\n")
        counts = fixture["strokeCounts"]
        print(
            f"{name:24s} strokes={counts['total']:6d} "
            f"(line={counts['line']:4d} hatch={counts['hatch']:6d} colored={counts['colored']:5d}) "
            f"payload={fixture['payloadBytes']/1024:.0f}KB "
            f"time={fixture['processingSeconds']:.2f}s -> {out_path.relative_to(BENCH_DIR.parent.parent)}"
        )


if __name__ == "__main__":
    main()
