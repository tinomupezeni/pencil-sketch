"""
Turns the flat, loosely-ordered stroke list from sketch.image_to_strokes()
into a structured DrawingPlan: a scheduled drawing order plus an executable
lift/move/draw/pause action sequence, simulating artistic working order
rather than just the raster-scan order the CV pipeline happened to emit.

Three distinct concepts, not to be confused with each other:
  - Stage:   a global artistic phase (construction, structural contours,
             light tonal blocking, regional development, deep shading,
             final accents). Strokes always progress through stages in
             STAGE_ORDER; a later stage never starts before an earlier one
             (populated) stage finishes.
  - Region:  a generic spatial grid cell (see assign_regions) -- purely
             geometric, not a semantic "eye"/"jaw"/etc. label.
  - Session: the atomic unit of scheduling -- one bounded visit to one
             region during one stage, with a declared `purpose` and the
             specific strokes drawn during that visit (see WorkSession).
             The same region appears in many sessions over the course of a
             plan, including deliberate later "revisit" sessions.

Pipeline (each step is a separate function, reusing the previous step's
output -- kept in one module because the project is small, but each step
is independently testable and importable):

    synthesize_construction_strokes -- optional, image-derived gesture/
                                        proportion marks (stage 1 only)
    annotate_strokes   -- geometry + heuristic estimates, per stroke
    assign_regions     -- deterministic spatial grid grouping
    assign_stages      -- maps existing kind/shade (or construction origin)
                          to one of 6 artistic stages
    schedule_strokes   -- produces the final human-like draw order as a
                          sequence of WorkSessions
    build_actions      -- expands the ordered strokes into lift/move/draw/pause
    build_drawing_plan -- orchestrates the above into a validated DrawingPlan

This module owns no imaging libraries (no cv2/numpy import) -- the convex
hull used for construction-stroke synthesis is plain Python -- keeping
image analysis (sketch.py) and artistic planning (this module) on opposite
sides of a hard dependency boundary.

Stage 6 (final accents/refinement) still has no stroke source and is
intentionally left unpopulated, same as stage 1 was before this phase --
no strokes are invented to fill it.
"""

from __future__ import annotations

import itertools
import math
from collections import deque
from dataclasses import dataclass, field

from plan_models import (
    Action,
    ActionType,
    DrawingPlan,
    StrokeAnnotation,
    StrokeKind,
)

PLANNER_VERSION = "2.0.0"

# --- artistic stages ---------------------------------------------------

STAGE_CONSTRUCTION = 1
STAGE_STRUCTURAL_CONTOURS = 2
STAGE_LIGHT_TONAL_BLOCKING = 3
STAGE_REGIONAL_DEVELOPMENT = 4
STAGE_DEEP_SHADING = 5
STAGE_FINAL_ACCENTS = 6

STAGE_ORDER = [
    STAGE_CONSTRUCTION,
    STAGE_STRUCTURAL_CONTOURS,
    STAGE_LIGHT_TONAL_BLOCKING,
    STAGE_REGIONAL_DEVELOPMENT,
    STAGE_DEEP_SHADING,
    STAGE_FINAL_ACCENTS,
]

STAGE_NAMES = {
    STAGE_CONSTRUCTION: "construction_and_broad_gesture",
    STAGE_STRUCTURAL_CONTOURS: "major_structural_contours",
    STAGE_LIGHT_TONAL_BLOCKING: "light_tonal_blocking",
    STAGE_REGIONAL_DEVELOPMENT: "regional_development",
    STAGE_DEEP_SHADING: "deep_shading_and_cross_hatching",
    STAGE_FINAL_ACCENTS: "final_accents_and_refinement",
}

# Per-stage batch size: how many strokes a single work session in one
# region covers. Stage 3 (light tonal blocking) is deliberately the
# smallest -- a sparse first wash across the *whole* image, so the sketch
# becomes recognizable everywhere before any one region goes further,
# rather than filling one region solid while the rest stays blank.
#
# Phase 10 audit (skull benchmark): with a batch size of 6, stage 3 held
# ~70% of the *entire* drawing's natural estimated time, and more than half
# of that was pure pen-up travel, not drawing -- because a batch this
# small forces a session (and therefore a fresh lift+move) after every
# handful of strokes, repeated across every region many times over. Raised
# to 14: still the smallest batch (the "sparse wash" character is
# unchanged), but cuts the number of session-switches roughly in half
# without filling any one region solid before the rest catches up.
STAGE_BATCH_SIZE = {
    STAGE_CONSTRUCTION: 25,
    STAGE_STRUCTURAL_CONTOURS: 20,
    STAGE_LIGHT_TONAL_BLOCKING: 14,
    STAGE_REGIONAL_DEVELOPMENT: 16,
    STAGE_DEEP_SHADING: 18,
    STAGE_FINAL_ACCENTS: 25,
}

STAGE_PURPOSE = {
    STAGE_CONSTRUCTION: "construction",
    STAGE_STRUCTURAL_CONTOURS: "establish_structure",
    STAGE_LIGHT_TONAL_BLOCKING: "tonal_block",
    STAGE_REGIONAL_DEVELOPMENT: "develop",
    STAGE_FINAL_ACCENTS: "refine",
}

# Shade cut points bucketing hatch strokes into stages 3/4/5. Chosen to line
# up with sketch.py's own light/dark split (0.4) and its cross-hatch
# threshold (shade > 0.5): the deepest, cross-hatched bands land in stage 5.
LIGHT_TONAL_MAX_SHADE = 0.35
REGIONAL_MAX_SHADE = 0.55

# --- importance / duration heuristics -----------------------------------
# These are estimates, not measurements: a documented formula, not a claim
# of realism. Line strokes (structural contours) are weighted as more
# important and are assumed slower/more deliberate to draw than hatching.

LINE_BASE_IMPORTANCE = 0.65
HATCH_BASE_IMPORTANCE = 0.10
CONSTRUCTION_BASE_IMPORTANCE = 0.55
LENGTH_REFERENCE_PX = 140.0
LENGTH_IMPORTANCE_WEIGHT = 0.25
SHADE_IMPORTANCE_WEIGHT = 0.15

LINE_SPEED_PX_PER_SEC = 180.0
HATCH_SPEED_PX_PER_SEC = 340.0
CONSTRUCTION_SPEED_PX_PER_SEC = 420.0  # quick, loose gestural marks
MIN_STROKE_SECONDS = 0.05

TRAVEL_SPEED_PX_PER_SEC = 900.0
# A real hand's short "flick" to the next nearby mark (the overwhelmingly
# common case between adjacent hatch strokes in the same small patch of
# paper) is proportionally much faster than a deliberate sweep across the
# page -- a wrist movement, not an arm movement. Phase 10 audit: without
# this tier, pen-up travel was comparable to actual drawing time overall
# (and the single largest cost in the lightest tonal-blocking stage)
# purely from modeling every short hop at full cross-page travel speed.
LOCAL_HOP_MAX_PX = 60.0
LOCAL_HOP_SPEED_PX_PER_SEC = 2400.0
MIN_MOVE_SECONDS = 0.02
LIFT_SECONDS = 0.0  # instantaneous by definition: a reflex, not a travel
STAGE_TRANSITION_PAUSE_SECONDS = 0.4
POSITION_EPSILON_PX = 0.05

# --- region grid ----------------------------------------------------------

TARGET_CELL_PX = 140.0
MIN_GRID_CELLS = 2
MAX_GRID_CELLS = 8

# --- scheduling ------------------------------------------------------------

ANCHOR_FRACTION = 0.10  # fraction of structural-contour strokes treated as anchors
DEFAULT_BATCH_SIZE = 25  # fallback batch size for any stage not in STAGE_BATCH_SIZE

# Session-scoring weights (section 6): kept small in count and each
# independently documented so the balance is easy to reason about and
# retune. See score_session_candidate.
SCORE_IMPORTANCE_WEIGHT = 0.3
SCORE_BALANCE_WEIGHT = 0.5
SCORE_LOCALITY_BONUS = 0.3
SCORE_REVISIT_PENALTY = 0.4
SCORE_TRAVEL_WEIGHT = 0.2

# Deep-shading revisits (section 5): a region's deep-shading strokes split
# into an initial pass (lighter within the band) and a held-back "deepen
# further" revisit pass (the darkest fraction), scheduled as a separate,
# later sweep across all regions -- a genuine second visit, not a replay of
# the same strokes.
SHADOW_REVISIT_FRACTION = 0.35
MIN_STROKES_FOR_REVISIT_SPLIT = 4

# --- duration-aware pacing classification (Phase 10, corrected by 10.1) ----
# A session paced at or above this multiple of its own natural
# (backend-estimated) pace is labeled "timelapse" rather than "natural" in
# plan metadata -- a small allowance above 1.0x so a barely-brisker-than-
# natural pace isn't flagged just for sitting a hair over it. See
# classify_duration_pacing below for the full rationale, including why
# this never affects which strokes are in the plan.
TIMELAPSE_LABEL_THRESHOLD = 1.15


def classify_duration_pacing(strokes: list[AnnotatedStroke], target_duration_seconds: float | None) -> dict:
    """Reports how the requested session length compares to this plan's
    own natural (backend-estimated) total draw time -- for the frontend's
    "Time-lapse" badge only.

    Phase 10.1 correction: Phase 10 originally used a companion function
    (select_stroke_budget, removed) to also *reduce stroke count* for
    short sessions. That measurably thinned out tonal shading and strong
    graphite buildup in the finished drawing -- a real regression, because
    it coupled a presentation choice (how fast to watch the drawing
    happen) to the artistic content itself. Corrected principle: the user
    picks how long they want to *watch* the drawing happen, never how
    much of the artwork gets drawn. A plan's `strokes` are therefore
    always the complete artistic output regardless of
    `target_duration_seconds` -- this function only classifies and
    reports the implied pacing; it never removes or samples strokes.
    Actual speed-up/slow-down for a short or long session is a pure
    *playback* concern, handled entirely by the frontend's linear
    timeline scaling (frontend/js/timeline.js), which was already
    duration-content-agnostic before Phase 10 existed.

    `target_duration_seconds=None` means no duration was specified;
    reported as the "natural" pace.
    """
    full_natural_time = sum(s.estimated_duration for s in strokes)

    if target_duration_seconds is None:
        return {
            "requested": False,
            "fullNaturalSeconds": round(full_natural_time, 1),
            "pacing": "natural",
            "accelerationFactor": 1.0,
        }

    target_duration_seconds = max(1.0, float(target_duration_seconds))
    acceleration_factor = full_natural_time / target_duration_seconds if full_natural_time > 0 else 0.0
    pacing = "timelapse" if acceleration_factor >= TIMELAPSE_LABEL_THRESHOLD else "natural"

    return {
        "requested": True,
        "targetDurationSeconds": round(target_duration_seconds, 1),
        "fullNaturalSeconds": round(full_natural_time, 1),
        "pacing": pacing,
        "accelerationFactor": round(acceleration_factor, 3),
    }


# --- construction-stroke synthesis (section 1) ------------------------------

MIN_STROKES_FOR_CONSTRUCTION = 8  # "where supported by the image" -- skip if too sparse
MAX_HULL_VERTICES = 10
CONSTRUCTION_SHADE = 0.1  # low-contrast by design -- see module docstring on fading
CONSTRUCTION_WIDTH = 0.8
PROPORTION_ANCHOR_SPAN_FRACTION = 0.65
# A broad silhouette gesture doesn't need every point of every stroke --
# the hatch pass alone can produce tens of thousands of strokes (Phase 1
# audit), and computing a hull over hundreds of thousands of points cost
# several seconds for no visible benefit over a representative sample.
MAX_HULL_SAMPLE_STROKES = 500


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone-chain convex hull, O(n log n), pure Python (no
    cv2/numpy -- see module docstring). Returns hull vertices counter-
    clockwise; fewer than 3 unique input points returns them as-is.
    """
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def synthesize_construction_strokes(raw_strokes: list[dict], width: int, height: int) -> list[dict]:
    """Image-derived, low-contrast construction marks (section 1): a broad
    silhouette gesture (the convex hull of every real stroke's points,
    simplified to a handful of vertices) plus two classic proportion-anchor
    marks (a horizontal and vertical line through the overall bounding
    box's center). Nothing here guesses at facial features or object parts
    -- it is pure convex-hull/bounding-box geometry over strokes sketch.py
    already produced, so it applies equally to a portrait, a building, an
    animal, or a landscape (section 7).

    Returns [] when there isn't enough material to support a meaningful
    gesture (MIN_STROKES_FOR_CONSTRUCTION) -- "where supported by the
    image", not invented from nothing.

    On fading/erasure (section 1): these marks are never erased -- erasing
    already-rendered pixels would contradict the renderer's incremental,
    non-destructive model (Phase 3/4). Instead they are deliberately
    assigned a very low, flat shade/pressure for their entire lifetime (see
    CONSTRUCTION_SHADE and the frontend's "construction" pressure profile),
    so they read as faint guides that stay faint and are naturally
    overshadowed by firmer strokes drawn over the same area later -- the
    same way a real pencil construction line stays visible but recedes
    under subsequent ink, rather than being wiped away.
    """
    if len(raw_strokes) < MIN_STROKES_FOR_CONSTRUCTION:
        return []

    # Single pass for the true overall extent (used for the proportion
    # anchors, where accuracy matters and a linear scan is cheap).
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    for s in raw_strokes:
        for x, y in s["points"]:
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y

    # A representative sample of stroke endpoints (not every point of every
    # stroke) for the hull -- plenty for a *broad* silhouette gesture.
    stride = max(1, len(raw_strokes) // MAX_HULL_SAMPLE_STROKES)
    sample_points: list[tuple[float, float]] = []
    for s in raw_strokes[::stride]:
        pts = s["points"]
        sample_points.append((float(pts[0][0]), float(pts[0][1])))
        sample_points.append((float(pts[-1][0]), float(pts[-1][1])))

    hull = _convex_hull(sample_points)
    if len(hull) < 3:
        return []
    if len(hull) > MAX_HULL_VERTICES:
        hull_stride = max(1, len(hull) // MAX_HULL_VERTICES)
        hull = hull[::hull_stride]
    silhouette = [list(p) for p in hull] + [list(hull[0])]  # closed loop

    cx, cy = (min_x + max_x) / 2, (min_y + max_y) / 2
    half_w = (max_x - min_x) * PROPORTION_ANCHOR_SPAN_FRACTION / 2
    half_h = (max_y - min_y) * PROPORTION_ANCHOR_SPAN_FRACTION / 2

    horizontal = [[cx - half_w, cy], [cx + half_w, cy]]
    vertical = [[cx, cy - half_h], [cx, cy + half_h]]

    return [
        {"points": silhouette, "width": CONSTRUCTION_WIDTH, "shade": CONSTRUCTION_SHADE, "kind": "line"},
        {"points": horizontal, "width": CONSTRUCTION_WIDTH, "shade": CONSTRUCTION_SHADE, "kind": "line"},
        {"points": vertical, "width": CONSTRUCTION_WIDTH, "shade": CONSTRUCTION_SHADE, "kind": "line"},
    ]


@dataclass
class AnnotatedStroke:
    id: int
    points: list[list[float]]
    width: float
    shade: float
    kind: str
    centroid: tuple[float, float]
    arc_length: float
    bbox: tuple[float, float, float, float]
    importance: float
    estimated_duration: float
    region_id: str = ""
    stage: int = 0
    session_id: int = -1  # -1 means "not yet scheduled"; always set by schedule_strokes
    is_construction: bool = False
    color: str | None = None  # "#rrggbb"; None means neutral graphite (Phase 9)

    def to_model(self) -> StrokeAnnotation:
        return StrokeAnnotation(
            id=self.id,
            points=[(float(x), float(y)) for x, y in self.points],
            width=self.width,
            shade=self.shade,
            kind=StrokeKind(self.kind),
            centroid=self.centroid,
            arc_length=self.arc_length,
            bbox=self.bbox,
            region_id=self.region_id,
            stage=self.stage,
            importance=self.importance,
            estimated_duration=self.estimated_duration,
            session_id=self.session_id,
            is_construction=self.is_construction,
            color=self.color,
        )


@dataclass
class WorkSession:
    """One bounded visit to one region during one stage (see module
    docstring). `purpose` is a short, stable label explaining *why* this
    session exists -- the primary observability hook (section 8): e.g.
    "establish_anchors", "tonal_block", "deepen_shadow_initial",
    "deepen_shadow_revisit".
    """

    id: int
    stage: int
    region_id: str
    purpose: str
    stroke_ids: list[int] = field(default_factory=list)


# --- stage 1: annotation ----------------------------------------------------


def _centroid(points: list[list[float]]) -> tuple[float, float]:
    n = len(points)
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    return (sx / n, sy / n)


def _arc_length(points: list[list[float]]) -> float:
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        total += math.hypot(x1 - x0, y1 - y0)
    return total


def _bbox(points: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _importance(kind: str, arc_length: float, shade: float, *, is_construction: bool = False) -> float:
    if is_construction:
        base = CONSTRUCTION_BASE_IMPORTANCE
    else:
        base = LINE_BASE_IMPORTANCE if kind == "line" else HATCH_BASE_IMPORTANCE
    length_score = min(arc_length / LENGTH_REFERENCE_PX, 1.0) * LENGTH_IMPORTANCE_WEIGHT
    shade_score = shade * SHADE_IMPORTANCE_WEIGHT
    return round(min(base + length_score + shade_score, 1.0), 4)


def _estimated_duration(kind: str, arc_length: float, *, is_construction: bool = False) -> float:
    if is_construction:
        speed = CONSTRUCTION_SPEED_PX_PER_SEC
    else:
        speed = LINE_SPEED_PX_PER_SEC if kind == "line" else HATCH_SPEED_PX_PER_SEC
    return round(max(MIN_STROKE_SECONDS, arc_length / speed), 4)


def annotate_strokes(raw_strokes: list[dict], *, num_real_strokes: int | None = None) -> list[AnnotatedStroke]:
    """Attach stable ids and geometry/heuristic estimates. Order-preserving.

    `num_real_strokes` marks the boundary between real (sketch.py-produced)
    strokes and any construction strokes appended after them (see
    build_drawing_plan) -- strokes at or past that index are flagged
    `is_construction`. Defaults to "all of them are real", so existing
    callers that only ever pass real strokes are unaffected.
    """
    if num_real_strokes is None:
        num_real_strokes = len(raw_strokes)
    annotated = []
    for i, s in enumerate(raw_strokes):
        pts = s["points"]
        is_construction = i >= num_real_strokes
        arc_length = round(_arc_length(pts), 2)
        annotated.append(
            AnnotatedStroke(
                id=i,
                points=pts,
                width=s["width"],
                shade=s["shade"],
                kind=s["kind"],
                centroid=_centroid(pts),
                arc_length=arc_length,
                bbox=tuple(round(v, 2) for v in _bbox(pts)),
                importance=_importance(s["kind"], arc_length, s["shade"], is_construction=is_construction),
                estimated_duration=_estimated_duration(s["kind"], arc_length, is_construction=is_construction),
                is_construction=is_construction,
                color=s.get("color"),
            )
        )
    return annotated


# --- stage 2: region grouping ------------------------------------------------


def _grid_dims(width: int, height: int) -> tuple[int, int]:
    cols = max(MIN_GRID_CELLS, min(MAX_GRID_CELLS, round(width / TARGET_CELL_PX)))
    rows = max(MIN_GRID_CELLS, min(MAX_GRID_CELLS, round(height / TARGET_CELL_PX)))
    return cols, rows


def assign_regions(
    strokes: list[AnnotatedStroke], width: int, height: int
) -> tuple[list[AnnotatedStroke], int, int]:
    """Deterministic spatial-grid clustering by stroke centroid.

    This is a generic geometric bucketing, not a semantic facial-feature
    detector -- region ids are plain grid coordinates ("region_c{col}_r{row}"),
    not labels like "eye" or "jaw". Grid size adapts to image size so regions
    stay a roughly constant on-screen size regardless of resolution.
    """
    cols, rows = _grid_dims(width, height)
    cell_w = width / cols
    cell_h = height / rows
    for s in strokes:
        cx, cy = s.centroid
        col = min(cols - 1, max(0, int(cx // cell_w)))
        row = min(rows - 1, max(0, int(cy // cell_h)))
        s.region_id = f"region_c{col}_r{row}"
    return strokes, cols, rows


def _serpentine_region_order(cols: int, rows: int) -> list[str]:
    """Row-major, boustrophedon region order: adjacent regions stay adjacent
    in the sequence. Used only as the scheduler's initial candidate order;
    actual visiting order is decided by score_session_candidate.
    """
    order = []
    for row in range(rows):
        col_range = range(cols) if row % 2 == 0 else range(cols - 1, -1, -1)
        for col in col_range:
            order.append(f"region_c{col}_r{row}")
    return order


# --- stage 3: artistic stage assignment --------------------------------------


def assign_stages(strokes: list[AnnotatedStroke]) -> list[AnnotatedStroke]:
    """Maps each stroke to one of the 6 artistic stages.

    Construction strokes (synthesize_construction_strokes) are always
    stage 1. Otherwise: line strokes -> structural contours (2); hatch
    strokes -> one of the three tonal stages (3/4/5) by shade. Stage 6
    (final accents) has no corresponding stroke source yet -- no strokes
    are invented to populate it.
    """
    for s in strokes:
        if s.is_construction:
            s.stage = STAGE_CONSTRUCTION
        elif s.kind == "line":
            s.stage = STAGE_STRUCTURAL_CONTOURS
        elif s.shade < LIGHT_TONAL_MAX_SHADE:
            s.stage = STAGE_LIGHT_TONAL_BLOCKING
        elif s.shade < REGIONAL_MAX_SHADE:
            s.stage = STAGE_REGIONAL_DEVELOPMENT
        else:
            s.stage = STAGE_DEEP_SHADING
    return strokes


# --- stage 4: scheduling ------------------------------------------------------


def _split_anchors(
    strokes: list[AnnotatedStroke], fraction: float
) -> tuple[list[AnnotatedStroke], list[AnnotatedStroke]]:
    if not strokes or fraction <= 0:
        return [], strokes
    n_anchors = max(1, round(len(strokes) * fraction))
    ranked = sorted(strokes, key=lambda s: (-s.importance, s.id))
    anchors = ranked[:n_anchors]
    anchor_ids = {s.id for s in anchors}
    rest = [s for s in strokes if s.id not in anchor_ids]
    return anchors, rest


def _split_shadow_tranches(
    strokes: list[AnnotatedStroke], revisit_fraction: float = SHADOW_REVISIT_FRACTION
) -> tuple[list[AnnotatedStroke], list[AnnotatedStroke]]:
    """Splits each region's deep-shading strokes into an initial pass
    (lighter within the band) and a held-back revisit pass (the darkest
    fraction) -- see module docstring on revisits. Done per-region (not
    globally) so revisit eligibility isn't concentrated in whichever
    regions happen to be darkest overall; a region too small to make a
    meaningful revisit (MIN_STROKES_FOR_REVISIT_SPLIT) skips the split
    entirely rather than producing a token one-stroke revisit.
    """
    by_region: dict[str, list[AnnotatedStroke]] = {}
    for s in strokes:
        by_region.setdefault(s.region_id, []).append(s)

    initial: list[AnnotatedStroke] = []
    revisit: list[AnnotatedStroke] = []
    for region_strokes in by_region.values():
        if len(region_strokes) < MIN_STROKES_FOR_REVISIT_SPLIT:
            initial.extend(region_strokes)
            continue
        ordered_by_shade = sorted(region_strokes, key=lambda s: (s.shade, s.id))
        split_idx = max(1, round(len(ordered_by_shade) * (1 - revisit_fraction)))
        initial.extend(ordered_by_shade[:split_idx])
        revisit.extend(ordered_by_shade[split_idx:])
    return initial, revisit


def score_session_candidate(
    *,
    avg_importance: float,
    region_progress: float,
    is_same_region_as_last: bool,
    is_revisit: bool,
    travel_fraction: float,
) -> float:
    """Pure, independently-testable scoring function (section 6). Higher
    scores are scheduled sooner.

    - `avg_importance` (0..1): stroke importance -- favors structurally
      significant work.
    - `region_progress` (0..1, this pass only): fraction of this region's
      *current-pass* workload already scheduled. Weighting (1 - progress)
      favors regions the image hasn't developed yet, which is what keeps
      the whole image emerging in visual balance instead of one region
      finishing solid while the rest stays blank (sections 3/4/6).
    - `is_same_region_as_last`: a mild continuity bonus for staying in a
      coherent local session rather than hopping every single step.
    - `is_revisit`: revisit-purpose sessions are deprioritized relative to
      first-visit sessions in the same pass (reinforcing, not replacing,
      the fact that revisits are already scheduled as a separate later
      sweep -- see _split_shadow_tranches).
    - `travel_fraction`: pixel distance to this candidate's region,
      normalized by the image diagonal. Its weight is intentionally the
      *smallest* of the five, so artistic sequencing is never overridden
      by pure shortest-travel optimization (explicitly required: avoid
      global shortest-travel optimization at the expense of artistic
      sequence).
    """
    score = SCORE_IMPORTANCE_WEIGHT * avg_importance
    score += SCORE_BALANCE_WEIGHT * (1.0 - region_progress)
    if is_same_region_as_last:
        score += SCORE_LOCALITY_BONUS
    if is_revisit:
        score -= SCORE_REVISIT_PENALTY
    score -= SCORE_TRAVEL_WEIGHT * min(1.0, max(0.0, travel_fraction))
    return score


def _region_centroid(strokes: list[AnnotatedStroke]) -> tuple[float, float]:
    xs = [s.centroid[0] for s in strokes]
    ys = [s.centroid[1] for s in strokes]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _schedule_region_sessions(
    strokes: list[AnnotatedStroke],
    region_order: list[str],
    batch_size: int,
    stage: int,
    purpose: str,
    *,
    is_revisit: bool,
    width: int,
    height: int,
    last_region_id: str | None,
    last_position: tuple[float, float] | None,
    session_id_counter,
) -> tuple[list[WorkSession], str | None, tuple[float, float] | None]:
    """Greedy, score-driven session scheduler for one (stage, stroke-pool)
    call. At each step, every region still holding strokes offers its next
    batch as a candidate; score_session_candidate picks which one goes
    next. Cost is O(sessions x regions-still-pending), not O(strokes^2) --
    regions are capped at MAX_GRID_CELLS^2 (<=64), so this stays cheap even
    for tens of thousands of strokes (see Phase 1 audit on hatch-pass scale).
    """
    groups: dict[str, list[AnnotatedStroke]] = {}
    for s in strokes:
        groups.setdefault(s.region_id, []).append(s)
    if not groups:
        return [], last_region_id, last_position

    for items in groups.values():
        items.sort(key=lambda s: (s.shade, -s.importance, s.id))

    region_centroids = {r: _region_centroid(items) for r, items in groups.items()}
    totals = {r: len(items) for r, items in groups.items()}
    completed = {r: 0 for r in groups}
    queues: dict[str, deque[list[AnnotatedStroke]]] = {
        r: deque(items[i : i + batch_size] for i in range(0, len(items), batch_size))
        for r, items in groups.items()
    }
    image_diag = math.hypot(width, height) or 1.0

    # Stable starting order (serpentine) for any regions tied on score.
    order_index = {r: i for i, r in enumerate(region_order)}
    pending = sorted(groups.keys(), key=lambda r: order_index.get(r, len(region_order)))

    sessions: list[WorkSession] = []
    while pending:
        best_region = None
        best_score = None
        for region in pending:
            batch = queues[region][0]
            avg_importance = sum(s.importance for s in batch) / len(batch)
            progress = completed[region] / totals[region]
            travel_fraction = 0.0
            if last_position is not None:
                rx, ry = region_centroids[region]
                dist = math.hypot(rx - last_position[0], ry - last_position[1])
                travel_fraction = dist / image_diag
            score = score_session_candidate(
                avg_importance=avg_importance,
                region_progress=progress,
                is_same_region_as_last=(region == last_region_id),
                is_revisit=is_revisit,
                travel_fraction=travel_fraction,
            )
            if (
                best_score is None
                or score > best_score
                or (score == best_score and order_index.get(region, 0) < order_index.get(best_region, 0))
            ):
                best_score = score
                best_region = region

        batch = queues[best_region].popleft()
        sessions.append(
            WorkSession(
                id=next(session_id_counter),
                stage=stage,
                region_id=best_region,
                purpose=purpose,
                stroke_ids=[s.id for s in batch],
            )
        )
        completed[best_region] += len(batch)
        last_region_id = best_region
        last_position = region_centroids[best_region]
        if not queues[best_region]:
            pending = [r for r in pending if r != best_region]

    return sessions, last_region_id, last_position


def schedule_strokes(
    strokes: list[AnnotatedStroke],
    cols: int,
    rows: int,
    width: int,
    height: int,
    *,
    anchor_fraction: float = ANCHOR_FRACTION,
) -> tuple[list[AnnotatedStroke], list[WorkSession]]:
    """Produces the final human-like draw order, as an ordered sequence of
    WorkSessions (see module docstring).

    Rules, and how each is implemented:
      - broad structure before detail        -> primary key is artistic stage
      - construction before everything else  -> stage 1 processed first
      - important structural anchors early   -> top-importance line strokes
                                                 scheduled as their own
                                                 "establish_anchors" session
      - sparse global wash before depth       -> stage 3's small batch size
      - lighter marks before darker marks     -> ascending shade within region
      - coherent local sessions               -> region-batched, not per-stroke
      - regional balance, not one-region-first -> score_session_candidate's
                                                 (1 - region_progress) term
      - avoid big jumps between regions       -> travel-distance term (small
                                                 weight: never overrides
                                                 artistic sequence)
      - selective, purposeful revisits        -> deep-shading strokes split
                                                 into an initial pass and a
                                                 held-back "deepen further"
                                                 pass, scheduled as a later,
                                                 separate sweep
      - not pure nearest-neighbor chaining    -> score_session_candidate is a
                                                 multi-factor function, and
                                                 travel distance is its
                                                 smallest-weighted term

    Cost: O(total_sessions x regions_pending_per_step); regions are capped
    at MAX_GRID_CELLS^2, so this stays well within budget for tens of
    thousands of strokes (see Phase 1 audit).
    """
    by_stage: dict[int, list[AnnotatedStroke]] = {}
    for s in strokes:
        by_stage.setdefault(s.stage, []).append(s)

    region_order = _serpentine_region_order(cols, rows)
    session_id_counter = itertools.count()
    sessions: list[WorkSession] = []
    last_region_id: str | None = None
    last_position: tuple[float, float] | None = None

    for stage in STAGE_ORDER:
        stage_strokes = by_stage.get(stage)
        if not stage_strokes:
            continue

        remaining = stage_strokes
        batch_size = STAGE_BATCH_SIZE.get(stage, DEFAULT_BATCH_SIZE)

        if stage == STAGE_STRUCTURAL_CONTOURS:
            anchors, remaining = _split_anchors(stage_strokes, anchor_fraction)
            if anchors:
                anchors_sorted = sorted(anchors, key=lambda s: (-s.importance, s.id))
                sessions.append(
                    WorkSession(
                        id=next(session_id_counter),
                        stage=stage,
                        region_id=anchors_sorted[0].region_id,
                        purpose="establish_anchors",
                        stroke_ids=[s.id for s in anchors_sorted],
                    )
                )
                last_region_id = anchors_sorted[0].region_id
                last_position = anchors_sorted[0].centroid

        if stage == STAGE_DEEP_SHADING:
            initial, revisit = _split_shadow_tranches(remaining)
            new_sessions, last_region_id, last_position = _schedule_region_sessions(
                initial,
                region_order,
                batch_size,
                stage,
                "deepen_shadow_initial",
                is_revisit=False,
                width=width,
                height=height,
                last_region_id=last_region_id,
                last_position=last_position,
                session_id_counter=session_id_counter,
            )
            sessions += new_sessions
            if revisit:
                new_sessions, last_region_id, last_position = _schedule_region_sessions(
                    revisit,
                    region_order,
                    batch_size,
                    stage,
                    "deepen_shadow_revisit",
                    is_revisit=True,
                    width=width,
                    height=height,
                    last_region_id=last_region_id,
                    last_position=last_position,
                    session_id_counter=session_id_counter,
                )
                sessions += new_sessions
        else:
            purpose = STAGE_PURPOSE.get(stage, "develop")
            new_sessions, last_region_id, last_position = _schedule_region_sessions(
                remaining,
                region_order,
                batch_size,
                stage,
                purpose,
                is_revisit=False,
                width=width,
                height=height,
                last_region_id=last_region_id,
                last_position=last_position,
                session_id_counter=session_id_counter,
            )
            sessions += new_sessions

    stroke_by_id = {s.id: s for s in strokes}
    ordered: list[AnnotatedStroke] = []
    for session in sessions:
        for sid in session.stroke_ids:
            stroke = stroke_by_id[sid]
            stroke.session_id = session.id
            ordered.append(stroke)

    assert len(ordered) == len(strokes), "scheduler must preserve every stroke exactly once"
    return ordered, sessions


# --- stage 5: action generation ----------------------------------------------


def build_actions(ordered_strokes: list[AnnotatedStroke]) -> list[Action]:
    """Expands the scheduled stroke order into an executable lift/move/draw/
    pause sequence. See plan_models.ActionType for pen-state semantics.
    """
    actions: list[Action] = []
    if not ordered_strokes:
        return actions

    pen_down = False
    pos = (0.0, 0.0)  # nominal rest position before the first stroke
    current_stage: int | None = None

    for s in ordered_strokes:
        if current_stage is not None and s.stage != current_stage:
            actions.append(Action(type=ActionType.PAUSE, duration=STAGE_TRANSITION_PAUSE_SECONDS))
        current_stage = s.stage

        start = (float(s.points[0][0]), float(s.points[0][1]))
        if pen_down:
            actions.append(Action(type=ActionType.LIFT, duration=LIFT_SECONDS))
            pen_down = False

        dist = math.hypot(start[0] - pos[0], start[1] - pos[1])
        if dist > POSITION_EPSILON_PX:
            speed = LOCAL_HOP_SPEED_PX_PER_SEC if dist <= LOCAL_HOP_MAX_PX else TRAVEL_SPEED_PX_PER_SEC
            move_duration = round(max(MIN_MOVE_SECONDS, dist / speed), 4)
            actions.append(Action(type=ActionType.MOVE, duration=move_duration, x=start[0], y=start[1]))
            pos = start

        actions.append(Action(type=ActionType.DRAW, duration=s.estimated_duration, stroke_id=s.id))
        pen_down = True
        end = s.points[-1]
        pos = (float(end[0]), float(end[1]))

    if pen_down:
        actions.append(Action(type=ActionType.LIFT, duration=LIFT_SECONDS))

    return actions


# --- stage 6: plan assembly ---------------------------------------------------


def _build_metadata(
    ordered_strokes: list[AnnotatedStroke],
    actions: list[Action],
    cols: int,
    rows: int,
    sessions: list[WorkSession],
    duration_info: dict,
) -> dict:
    stage_counts: dict[int, int] = {}
    for s in ordered_strokes:
        stage_counts[s.stage] = stage_counts.get(s.stage, 0) + 1
    region_ids = {s.region_id for s in ordered_strokes}
    total_seconds = round(sum(a.duration for a in actions), 3)

    purpose_counts: dict[str, int] = {}
    for session in sessions:
        purpose_counts[session.purpose] = purpose_counts.get(session.purpose, 0) + 1

    # Diagnostics (section 8): kept optional and bounded -- a compact
    # per-session summary (stage/region/purpose/count), not a duplicate of
    # every stroke id already recoverable from each stroke's own
    # `session_id` field, so this stays small even with thousands of
    # sessions.
    sessions_summary = [
        {
            "id": session.id,
            "stage": session.stage,
            "regionId": session.region_id,
            "purpose": session.purpose,
            "strokeCount": len(session.stroke_ids),
        }
        for session in sessions
    ]

    return {
        "plannerVersion": PLANNER_VERSION,
        "stageNames": STAGE_NAMES,
        "stageCounts": {str(k): v for k, v in sorted(stage_counts.items())},
        "regionGrid": {"cols": cols, "rows": rows},
        "regionCount": len(region_ids),
        "totalEstimatedSeconds": total_seconds,
        "sessionCount": len(sessions),
        "sessionsByPurpose": purpose_counts,
        "sessions": sessions_summary,
        "durationPlanning": duration_info,
        "weights": {
            "anchorFraction": ANCHOR_FRACTION,
            "stageBatchSize": {str(k): v for k, v in STAGE_BATCH_SIZE.items()},
            "lightTonalMaxShade": LIGHT_TONAL_MAX_SHADE,
            "regionalMaxShade": REGIONAL_MAX_SHADE,
            "shadowRevisitFraction": SHADOW_REVISIT_FRACTION,
            "scoreWeights": {
                "importance": SCORE_IMPORTANCE_WEIGHT,
                "balance": SCORE_BALANCE_WEIGHT,
                "locality": SCORE_LOCALITY_BONUS,
                "revisitPenalty": SCORE_REVISIT_PENALTY,
                "travel": SCORE_TRAVEL_WEIGHT,
            },
        },
    }


def build_drawing_plan(
    width: int,
    height: int,
    raw_strokes: list[dict],
    *,
    target_duration_seconds: float | None = None,
) -> DrawingPlan:
    """Full pipeline: raw sketch.py strokes -> validated DrawingPlan.

    `target_duration_seconds` (Phase 10, corrected by 10.1): the session
    length the caller intends to *play this plan over*. It never changes
    which strokes are in the plan -- see classify_duration_pacing's
    docstring for why -- only `metadata.durationPlanning`'s reported
    pacing ("natural" vs "timelapse"), which the frontend uses solely for
    an honest "Time-lapse" UI label. Actual speed-up/slow-down is a
    playback-only concern (frontend/js/timeline.js). Omitting this
    parameter (the default) reports pacing as "natural" with no other
    effect.
    """
    constructed = synthesize_construction_strokes(raw_strokes, width, height)
    all_raw = raw_strokes + constructed

    annotated = annotate_strokes(all_raw, num_real_strokes=len(raw_strokes))
    annotated, cols, rows = assign_regions(annotated, width, height)
    annotated = assign_stages(annotated)
    duration_info = classify_duration_pacing(annotated, target_duration_seconds)
    ordered, sessions = schedule_strokes(annotated, cols, rows, width, height)
    actions = build_actions(ordered)
    metadata = _build_metadata(ordered, actions, cols, rows, sessions, duration_info)

    return DrawingPlan(
        width=width,
        height=height,
        strokes=[s.to_model() for s in ordered],
        actions=actions,
        metadata=metadata,
    )
