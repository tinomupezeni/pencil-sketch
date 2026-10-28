"""Phase 10.4: automated-correctness half of the benchmark suite.

Regenerates each benchmark image's pipeline output *right now* and
compares it against the saved fixture (backend/benchmarks/fixtures/*.json)
within tolerance -- these tests catch unintentional drift (a future change
that silently alters stroke counts, payload size, or color/contour
presence), not whether the output looks good artistically. See
benchmarks/README.md: "Keep artistic visual approval separate from
automated correctness" (Phase 10.4, task 9) -- visual approval lives in
the fixture's committed screenshots and this suite's sibling,
benchmarks/verify_rendered.py (a Playwright script, not part of pytest,
since this repo has no committed browser-test dependency).

Tolerances are deliberately generous (stroke counts can shift a few
percent run-to-run from ordinary, *intentional* tuning of unrelated
constants) -- tight enough to catch a real regression (a cut cap, a
disabled pass, a broken color extraction), loose enough not to force a
fixture update for every unrelated change elsewhere in the pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planner import build_drawing_plan
from sketch import image_to_strokes

BENCH_DIR = Path(__file__).resolve().parent / "benchmarks"
IMAGES_DIR = BENCH_DIR / "images"
FIXTURES_DIR = BENCH_DIR / "fixtures"

# Generous: a real engine change can legitimately shift counts by a
# meaningful amount; this guards against gross regression (an order-of-
# magnitude swing, a category disappearing), not minor drift.
STROKE_COUNT_TOLERANCE = 0.35
PAYLOAD_TOLERANCE = 0.35


def _load_fixtures():
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        yield json.loads(path.read_text())


FIXTURES = list(_load_fixtures())
assert FIXTURES, "no benchmark fixtures found -- run benchmarks/generate_metrics.py first"

# The full pipeline (sketch.py + planner.py) on the heaviest benchmark
# image takes several seconds; this suite calls it four times per
# fixture (plan validity, stroke counts, payload size, determinism x2).
# Cached per fixture name so the whole module runs once per image, not
# once per test -- determinism is still genuinely checked by calling
# _regenerate() twice *within* test_benchmark_is_deterministic itself.
_regenerate_cache: dict[str, dict] = {}


def _regenerate(fixture: dict, *, use_cache: bool = True) -> dict:
    if use_cache and fixture["name"] in _regenerate_cache:
        return _regenerate_cache[fixture["name"]]
    data = (IMAGES_DIR / fixture["sourceFile"]).read_bytes()
    raw = image_to_strokes(data, style=fixture["style"])
    plan = build_drawing_plan(raw["width"], raw["height"], raw["strokes"])
    dumped = plan.model_dump(mode="json")
    for stroke in dumped["strokes"]:
        if stroke.get("color") is None:
            stroke.pop("color", None)
    if use_cache:
        _regenerate_cache[fixture["name"]] = dumped
    return dumped


def _within_tolerance(actual: float, expected: float, tolerance: float) -> bool:
    if expected == 0:
        return actual == 0
    return abs(actual - expected) / expected <= tolerance


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["name"])
def test_benchmark_still_produces_a_valid_plan(fixture):
    dumped = _regenerate(fixture)
    assert dumped["width"] == fixture["width"]
    assert dumped["height"] == fixture["height"]
    assert len(dumped["strokes"]) > 0


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["name"])
def test_benchmark_stroke_counts_within_tolerance(fixture):
    dumped = _regenerate(fixture)
    counts = {"line": 0, "hatch": 0, "colored": 0, "total": len(dumped["strokes"])}
    for s in dumped["strokes"]:
        counts[s["kind"]] = counts.get(s["kind"], 0) + 1
        if s.get("color"):
            counts["colored"] += 1

    expected = fixture["strokeCounts"]
    for key in ("line", "hatch", "total"):
        assert _within_tolerance(counts[key], expected[key], STROKE_COUNT_TOLERANCE), (
            f"{fixture['name']}: {key} count drifted from {expected[key]} to {counts[key]} "
            f"(tolerance {STROKE_COUNT_TOLERANCE:.0%})"
        )
    # Colored strokes either exist on both sides or neither -- color
    # presence/absence is a correctness property, not a tunable count.
    assert (counts["colored"] > 0) == (expected["colored"] > 0), (
        f"{fixture['name']}: color-accent presence changed (was {expected['colored']}, now {counts['colored']})"
    )


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["name"])
def test_benchmark_payload_size_within_tolerance(fixture):
    dumped = _regenerate(fixture)
    payload_bytes = len(json.dumps(dumped))
    assert _within_tolerance(payload_bytes, fixture["payloadBytes"], PAYLOAD_TOLERANCE), (
        f"{fixture['name']}: payload drifted from {fixture['payloadBytes']} to {payload_bytes} bytes "
        f"(tolerance {PAYLOAD_TOLERANCE:.0%})"
    )


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["name"])
def test_benchmark_is_deterministic(fixture):
    first = _regenerate(fixture, use_cache=False)
    second = _regenerate(fixture, use_cache=False)
    assert [s["id"] for s in first["strokes"]] == [s["id"] for s in second["strokes"]]
