"""
Converts a raster image into an ordered list of pencil-style strokes.

Three kinds of strokes are produced:
  - "line" strokes: a small number of long, strong contours (glasses,
    jawline, collar, jewelry) — the structural work an artist lays down
    first, kept sparse and low-opacity rather than outlining everything.
    A second, finer-scale edge pass (Phase 9) reserves some of that budget
    for small but meaningful detail (tooth gaps, a nostril, an eye-socket
    boundary) that a single coarse Canny pass tends to merge away or miss.
  - "hatch" strokes: short strokes that follow the local form (the
    tangent of the image gradient), the way pencil shading follows hair
    direction or the curve of a cheek, instead of lying at a fixed global
    angle. Darker tone bands get denser, longer, heavier strokes, layered
    from light to dark so tone builds up the way real shading does.
  - colored "hatch" strokes (Phase 9, "faithful" style only): the same
    hatching machinery, restricted to connected regions of the image that
    are meaningfully chromatic (not grayscale), carrying a muted version
    of that region's own color instead of neutral graphite -- a red
    hatband stays recognizably red, without hardcoding "red" or any
    specific object.

Everything is returned in pixel coordinates of the (resized) working image,
along with that image's width/height, so the frontend can scale a canvas
to match and animate strokes in the order given.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

import cv2
import numpy as np

MAX_DIMENSION = 760

# Resource safety budgets (Phase 6, section 5). These are backstops for
# pathological/adversarial input, not limits a normal photo should ever
# approach: a real image is downsampled to MAX_DIMENSION before any stroke
# generation happens, and the hatch pass's own band spacing already keeps
# typical stroke counts far below MAX_HATCH_STROKES.
#
# Phase 10.4: this headroom shrank a lot once the tone-aware accumulation
# cap (MAX_LOCAL_ACCUMULATION, below) started allowing far denser hatching
# for genuinely dark regions. Measured directly: a maximally pathological
# synthetic test (a large, uniformly near-black photo) now generates
# ~115,000 strokes -- within 5,000 of the old 120,000 ceiling, leaving
# almost no margin for anything even darker or larger. Tightened to
# 100,000 to restore real headroom. This bounds final payload/stroke
# count, not processing *time* (truncation happens after generation, not
# during it) -- the pathological case above still takes ~20s to generate
# before any truncation applies. Safely bounding generation time itself
# would need to skip remaining *light* bands first (the ones contributing
# least to perceived darkness), never the darkest ones processed last --
# exactly the bands the Phase 10.3 fix depends on -- which is a more
# invasive change than this phase's scope; flagged as a follow-up rather
# than guessed at here. None of this project's approved benchmark images
# (backend/benchmarks/) come anywhere near 100,000 strokes, so this change
# has no effect on any of them.
MAX_DECODED_PIXELS = 40_000_000  # ~6500x6500: guards decompression-bomb-style images
MAX_HATCH_STROKES = 100_000

STYLE_FAITHFUL = "faithful"  # preserves source color accents (section 2A)
STYLE_GRAPHITE = "graphite"  # intentional black-and-white interpretation (section 2B)
VALID_STYLES = (STYLE_FAITHFUL, STYLE_GRAPHITE)

# --- Phase 9: background suppression / "fewer, better strokes" -------------
# Configurable controls (section 7), not assumptions baked into the
# algorithm: BACKGROUND_PERCENTILE + MIN_TONAL_SIGNIFICANCE derive "what
# counts as paper" from the actual image's own brightest tones (never
# assumed to be pure white), so a pixel only gets hatched once it is
# meaningfully darker than that image's own paper/background tone -- not
# merely "less than 240".
BACKGROUND_PERCENTILE = 90
MIN_TONAL_SIGNIFICANCE = 14.0
# Below this much spread between the image's lightest and darker tones,
# there's no real background/subject split to protect -- see
# image_to_strokes's tone_spread guard.
MIN_BACKGROUND_SPREAD = 25.0
HATCH_SPACING_SCALE = 1.15  # >1 = sparser overall ("fewer, better strokes")
CROSS_HATCH_MIN_SHADE = 0.5
# Coarse per-cell cap on how many accepted strokes (any band, any pass) may
# land in the same neighborhood -- a direct bound on "maximum local
# graphite accumulation", independent of how many bands a region's tone
# would otherwise qualify for.
ACCUM_CELL_PX = 10
# Phase 10.3: a single flat cap here was empirically proven wrong, not
# just suboptimal -- a synthetic near-black/dark-gray/medium-gray/
# light-gray/near-white test strip, run through the real pipeline and
# real Canvas renderer, rendered the three darkest bands at nearly
# identical luminance (measured ~178/255 for source values 15, 70, AND
# 128), and a solid black circle on white rendered as a light gray
# hatched disc, not a dark one. Root cause: every tone dark enough to
# qualify for several bands hit the *same* flat per-cell density ceiling
# regardless of how much darker than its neighbors it actually was, so
# "near-black" and "medium-gray" converged on identical stroke density.
# The cap is now a function of the local source tone: light/mid tones
# keep the original conservative ceiling (texture, not a solid mass);
# tones approaching true black are allowed far more overlapping layers,
# because real deep graphite shadow is built from many dozens of
# crossing strokes, not ~10.
MIN_LOCAL_ACCUMULATION = 10  # unchanged ceiling for light/mid tones
MAX_LOCAL_ACCUMULATION = 60  # ceiling for true near-black tones
# Tone (0-255) at and above which the ceiling is already at its minimum --
# i.e. "not meaningfully darker than a midtone," no extra density earned.
ACCUMULATION_DARKNESS_REFERENCE = 170.0


def _accumulation_cap_formula(tone_value: float) -> int:
    darkness = max(0.0, min(1.0, (ACCUMULATION_DARKNESS_REFERENCE - tone_value) / ACCUMULATION_DARKNESS_REFERENCE))
    # Squared, not linear (Phase 10.4): a linear ramp gave an ordinary
    # photographic midtone (measured: a low-contrast photo whose darkest
    # gradient edge was only tone~90, nowhere near black) a cap around 34
    # -- enough to render it as a dense, nearly solid dark mass, losing the
    # low-contrast character of the source entirely. Squaring the darkness
    # fraction keeps moderate midtones close to the original, conservative
    # ceiling and reserves the large caps for tones that are genuinely
    # close to true black, without weakening separation at the dark end
    # (where it matters for the Phase 10.3 fix): tone=15 still reaches 52,
    # tone=70 drops to a more modest 27, tone=128 stays near the floor at 13.
    darkness = darkness * darkness
    return int(round(MIN_LOCAL_ACCUMULATION + darkness * (MAX_LOCAL_ACCUMULATION - MIN_LOCAL_ACCUMULATION)))


# Phase 10.4 (profiling): _accumulation_cap_formula is evaluated once per
# *candidate* hatch seed -- including every rejected one -- so its cost
# scales with attempted density, not just accepted strokes. Tone is
# always an 8-bit source value, so the formula has only 256 distinct
# possible inputs: precomputing them once into a lookup table turns a
# floating-point formula + round() + function call into a single array
# index, with no change to the result for any real input.
_ACCUMULATION_CAP_LUT = np.array([_accumulation_cap_formula(t) for t in range(256)], dtype=np.int16)


def _accumulation_cap_for_tone(tone_value: float) -> int:
    idx = 0 if tone_value < 0 else 255 if tone_value > 255 else int(tone_value)
    return int(_ACCUMULATION_CAP_LUT[idx])

# --- Phase 9: source-color preservation (Faithful Pencil only) -------------
CHROMA_THRESHOLD = 18.0  # CIELAB chroma; generalizes to any accent hue, not just red
MIN_COLOR_REGION_AREA = 40  # px; ignores color noise/speckle too small to matter
HUE_BIN_COUNT = 12  # splits connected-components by hue so two touching/overlapping
# differently-colored shapes (e.g. a red hatband against a blue jacket) never merge into
# one component and get averaged into a single wrong color
COLOR_MUTE_FACTOR = 0.72  # 0 = fully desaturated to gray, 1 = fully saturated source color

# --- Phase 9: smoothly-varying hatch orientation (section 6) ---------------
# A single coarse random field, upsampled with bilinear interpolation, so
# the hatch angle drifts gently from one area to another instead of being
# the exact same angle everywhere -- deterministic and reproducible (fixed
# seed), never chaotic (low grid resolution + linear upsampling keeps
# changes smooth, not noisy).
ANGLE_JITTER_GRID = 8
ANGLE_JITTER_DEG = 12.0
ANGLE_JITTER_SEED = 11

# --- Phase 9: multi-scale contour selection (section 4) --------------------
STRUCTURAL_CONTOUR_SLOTS = 110
FINE_CONTOUR_SLOTS = 50
COARSE_MIN_CONTOUR_LENGTH = 28.0
# Deliberately much shorter than the coarse floor: a tooth gap, a nostril,
# or an eye-socket boundary can be well under 28px even in a 760px-tall
# image. The fine pass's own lower Canny thresholds only change which
# edges are *found*; without a separate, lower length floor here, small
# real contours were still being discarded at this filtering step
# regardless (caught by test_multi_scale_contours_recover_small_detail...).
FINE_MIN_CONTOUR_LENGTH = 9.0
TOTAL_LINE_STROKE_CAP = STRUCTURAL_CONTOUR_SLOTS + FINE_CONTOUR_SLOTS
class GenerationInstrumentation:
    """Opt-in, observational counters for Phase 10.5.

    The collector is deliberately separate from the stroke data model. When
    omitted, the generation path performs no metric bookkeeping and returns
    exactly the historical drawing plan.
    """

    def __init__(self, image_shape: tuple[int, int] | None = None):
        self.started = time.perf_counter()
        self.image_shape = image_shape
        self.total = {"candidate_evaluations": 0, "accepted_seeds": 0, "rejected_seeds": 0,
                      "streamline_steps": 0, "allocated_points": 0, "strokes": 0}
        self.passes: dict[str, dict] = {}
        self._eligible_union = np.zeros(image_shape, dtype=bool) if image_shape else None
        self._eligible_coverage = np.zeros(image_shape, dtype=np.uint16) if image_shape else None

    def bucket(self, name: str) -> dict:
        return self.passes.setdefault(name, {
            "candidate_evaluations": 0, "accepted_seeds": 0, "rejected_seeds": 0,
            "streamline_steps": 0, "allocated_points": 0, "strokes": 0,
            "eligible_pixels": 0, "new_eligible_pixels": 0, "regions": {},
            "rejection_reasons": {"outside_mask": 0, "accumulation_cap": 0,
                                   "budget_exhausted": 0, "trace_invalid": 0, "background_suppression": 0},
        })

    def finish(self) -> dict:
        return {"elapsed_seconds": time.perf_counter() - self.started,
                "image_shape": self.image_shape, "total": dict(self.total),
                "passes": self.passes}

    def validate_accounting(self) -> None:
        """Assert candidate accounting is complete for every pass and total."""
        reason_names = ("outside_mask", "background_suppression", "accumulation_cap", "budget_exhausted", "trace_invalid")
        for name, bucket in self.passes.items():
            rejected = sum(bucket["rejection_reasons"].get(reason, 0) for reason in reason_names)
            assert bucket["candidate_evaluations"] == bucket["accepted_seeds"] + rejected, name
        aggregate_candidates = sum(bucket["candidate_evaluations"] for bucket in self.passes.values())
        aggregate_accepted = sum(bucket["accepted_seeds"] for bucket in self.passes.values())
        aggregate_rejected = sum(
            bucket["rejection_reasons"].get(reason, 0)
            for bucket in self.passes.values() for reason in reason_names
        )
        assert self.total["candidate_evaluations"] == aggregate_candidates
        assert self.total["accepted_seeds"] == aggregate_accepted
        assert self.total["rejected_seeds"] == aggregate_rejected
        assert self.total["candidate_evaluations"] == self.total["accepted_seeds"] + self.total["rejected_seeds"]

    def plan_budgets(self, total_budget: int = MAX_HATCH_STROKES) -> dict:
        """Deterministically simulate demand-weighted band budgets.

        `new_eligible_pixels` is measured against a union of earlier masks,
        so cumulative tonal bands do not count the same dark pixels repeatedly.
        This is a dry-run only; it never changes generation.
        """
        demand = {}
        for name, bucket in self.passes.items():
            if not (name.startswith("band:") or name.startswith("color:")):
                continue
            darkness = max(0.0, 255.0 - bucket.get("mean_tone", 255.0))
            # Cumulative masks overlap by design. Count new pixels fully and
            # repeated pixels as weighted tonal-depth demand, rather than
            # pretending every band's full area is independent image area.
            overlap = bucket.get("overlap_eligible_pixels", 0)
            depth_weight = 0.25 + bucket.get("shade", 0.0)
            demand[name] = max(1.0, bucket["new_eligible_pixels"] + overlap * depth_weight)
        total_demand = sum(demand.values()) or 1.0
        budgets = {name: int(total_budget * value / total_demand) for name, value in demand.items()}
        # Deterministic largest-remainder distribution makes the simulated
        # allocation sum exactly to the requested budget.
        remainder = total_budget - sum(budgets.values())
        for name in sorted(demand, key=lambda n: (-(total_budget * demand[n] / total_demand - budgets[n]), n))[:remainder]:
            budgets[name] += 1
        return {"total_budget": total_budget, "demand": demand, "budgets": budgets,
                "simulated_retention": {name: min(1.0, budgets[name] / max(1, self.passes[name]["strokes"]))
                                         for name in budgets},
                "simulated_loss": {name: max(0, self.passes[name]["strokes"] - budgets[name])
                                   for name in budgets},
                "spatial_demand": self._spatial_demand(),
                "spatial_quota_conflicts": self._spatial_quota_conflicts()}

    def _spatial_demand(self) -> dict[str, int]:
        demand: dict[str, int] = {}
        for bucket in self.passes.values():
            for region, values in bucket.get("regions", {}).items():
                demand[region] = demand.get(region, 0) + values["candidates"]
        return dict(sorted(demand.items()))

    def _spatial_quota_conflicts(self) -> dict[str, int]:
        """Reports cells where accepted seeds exceed the local cap.

        Current generation must never produce a positive conflict. This is a
        diagnostic for whether a future spatial quota would fight the existing
        tone-dependent accumulation cap, not an enforcement mechanism.
        """
        accepted_by_region: dict[str, int] = {}
        for bucket in self.passes.values():
            for region, values in bucket.get("regions", {}).items():
                accepted_by_region[region] = accepted_by_region.get(region, 0) + values["accepted"]
        return {region: accepted - MAX_LOCAL_ACCUMULATION for region, accepted in accepted_by_region.items()
                if accepted > MAX_LOCAL_ACCUMULATION}


class ResourceBudgetScheduler:
    """Initial, opt-in pass scheduler for predicted pathological demand.

    It is deliberately conservative: normal benchmark-shaped images stay on
    the historical path. When active, pass budgets are allocated from fixed
    tonal-demand weights, with progressively darker and cross-hatched passes
    receiving larger shares. This is enforcement before tracing, not the old
    post-generation stride truncation.
    """

    PASS_WEIGHTS = {
        **{f"band:{threshold}:parallel": 1.0 + index * 0.15
           for index, threshold in enumerate((240, 222, 200, 178, 155, 132, 108, 85, 62, 40))},
        **{f"band:{threshold}:cross": 1.3 + index * 0.18
           for index, threshold in enumerate((108, 85, 62, 40))},
    }

    def __init__(self, total_budget: int = MAX_HATCH_STROKES, *, active: bool = False):
        self.total_budget = total_budget
        self.active = active
        self.budgets: dict[str, int] = {}
        self.accepted: dict[str, int] = {}
        self.exhausted: dict[str, int] = {}
        self.color_budget = int(total_budget * 0.15)
        self.color_accepted = 0
        if active:
            total_weight = sum(self.PASS_WEIGHTS.values())
            neutral_budget = total_budget - self.color_budget
            self.budgets = {name: int(neutral_budget * weight / total_weight)
                            for name, weight in self.PASS_WEIGHTS.items()}
            remainder = neutral_budget - sum(self.budgets.values())
            for name in sorted(self.PASS_WEIGHTS)[-remainder:]:
                self.budgets[name] += 1

    @classmethod
    def from_tone(cls, tone: np.ndarray) -> "ResourceBudgetScheduler":
        # The first implementation intentionally activates only for a broad,
        # uniformly near-black field large enough to threaten the ceiling.
        # Ordinary benchmark images therefore retain byte-identical output.
        h, w = tone.shape
        active = h * w >= 500_000 and float(np.percentile(tone, 90)) < 40
        return cls(active=active)

    def permits(self, pass_name: str) -> bool:
        if not self.active:
            return True
        if pass_name.startswith("color:"):
            if self.color_accepted >= self.color_budget:
                self.exhausted[pass_name] = self.exhausted.get(pass_name, 0) + 1
                return False
            return True
        budget = self.budgets.get(pass_name, 0)
        used = self.accepted.get(pass_name, 0)
        if used >= budget:
            self.exhausted[pass_name] = self.exhausted.get(pass_name, 0) + 1
            return False
        return True

    def accepted_one(self, pass_name: str) -> None:
        if self.active:
            if pass_name.startswith("color:"):
                self.color_accepted += 1
                return
            self.accepted[pass_name] = self.accepted.get(pass_name, 0) + 1

    def report(self) -> dict:
        return {"active": self.active, "total_budget": self.total_budget,
                "budgets": dict(self.budgets), "accepted": dict(self.accepted),
                "exhausted_candidates": dict(self.exhausted),
                "color_budget": self.color_budget, "color_accepted": self.color_accepted,
                "accepted_total": sum(self.accepted.values()) + self.color_accepted}


@dataclass
class Stroke:
    points: list[list[float]]
    width: float
    shade: float  # 0 (light) - 1 (dark), drives stroke opacity
    kind: str  # "line" | "hatch"
    color: str | None = None  # "#rrggbb"; None means neutral graphite (default, backward compatible)

    def to_json(self) -> dict:
        # Phase 10.4: profiling showed per-point Python round()+float()
        # calls here were the single largest contributor to processing
        # time for a dense, dark image (4.18M calls, ~3s, for the skull
        # benchmark) -- pure serialization overhead, unrelated to the
        # actual stroke-generation math. Rounding the whole points array
        # in one NumPy call is the same output, computed roughly an order
        # of magnitude faster.
        pts = np.round(np.asarray(self.points, dtype=np.float64), 1)
        d = {
            "points": pts.tolist(),
            "width": round(self.width, 2),
            "shade": round(self.shade, 3),
            "kind": self.kind,
        }
        if self.color is not None:
            d["color"] = self.color
        return d


def _resize_to_max_dim(img: np.ndarray, max_dim: int = MAX_DIMENSION) -> np.ndarray:
    h, w = img.shape[:2]
    scale = max_dim / max(h, w)
    if scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img


def _contour_candidates(edges: np.ndarray, min_length: float = 28.0) -> list[tuple[float, list[list[float]]]]:
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_TC89_L1)
    candidates: list[tuple[float, list[list[float]]]] = []
    for c in contours:
        length = cv2.arcLength(c, False)
        if length < min_length:
            continue
        approx = cv2.approxPolyDP(c, epsilon=1.3, closed=False)
        pts = approx.reshape(-1, 2).astype(float).tolist()
        if len(pts) < 2:
            continue
        candidates.append((length, pts))
    return candidates


def _extract_line_strokes(gray: np.ndarray) -> list[Stroke]:
    """Sparse structural contours only — not a full outline of every edge.

    Two edge scales compete for a shared, still-bounded budget: a coarse
    pass (unchanged from before) for big structural contours, and a finer,
    less-smoothed pass that recovers small but meaningful detail a single
    coarse threshold tends to merge away -- individual teeth, a nostril, an
    eye-socket boundary. Reserving dedicated slots for the fine pass stops
    the (much larger) population of big contours from crowding small ones
    out purely by raw arc length.
    """
    blurred = cv2.bilateralFilter(gray, d=7, sigmaColor=50, sigmaSpace=50)
    # No dilation: thickening the edge before tracing its contour makes the
    # tracer walk both sides of the thickened blob as if they were two
    # separate, slightly offset strokes (the "doubled line" look on frames
    # and lip outlines). A thin, undilated edge traces as one clean line.
    edges_coarse = cv2.Canny(blurred, 70, 160)

    fine_blurred = cv2.bilateralFilter(gray, d=3, sigmaColor=30, sigmaSpace=30)
    edges_fine = cv2.Canny(fine_blurred, 40, 110)

    coarse_candidates = sorted(_contour_candidates(edges_coarse, COARSE_MIN_CONTOUR_LENGTH), key=lambda c: -c[0])
    fine_candidates = sorted(_contour_candidates(edges_fine, FINE_MIN_CONTOUR_LENGTH), key=lambda c: -c[0])

    selected = coarse_candidates[:STRUCTURAL_CONTOUR_SLOTS] + fine_candidates[:FINE_CONTOUR_SLOTS]
    selected = selected[:TOTAL_LINE_STROKE_CAP]

    return [Stroke(points=pts, width=1.1, shade=0.55, kind="line") for _length, pts in selected]


def _flow_field(
    gray: np.ndarray, default_angle_deg: float = 35.0, seed: int = ANGLE_JITTER_SEED
) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel unit tangent direction that hatch strokes should follow.

    This is mostly a gently-varying pencil-hatch angle (how a real sketch
    shades skin or clothing); it only bends to follow the contour near
    strong, large-scale edges — a hairline, a jaw, a glasses frame — the
    way an artist's hatching turns to follow hair strands or a cheek's
    curve. Fine photo texture (skin micro-contrast, fabric wrinkles) is
    blurred away first so it doesn't drag the hatching into a wood-grain
    swirl.
    """
    h, w = gray.shape

    # A smoothly-varying angle offset (not a single fixed angle everywhere)
    # -- a coarse seeded random grid, bilinearly upsampled, so the default
    # hatch direction drifts gently across the image rather than repeating
    # identically, without ever becoming chaotic (section 6).
    rng = np.random.RandomState(seed)
    coarse_jitter = rng.uniform(-ANGLE_JITTER_DEG, ANGLE_JITTER_DEG, size=(ANGLE_JITTER_GRID, ANGLE_JITTER_GRID))
    jitter_deg = cv2.resize(coarse_jitter.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    angle = np.radians(default_angle_deg) + np.radians(jitter_deg)
    dax = np.cos(angle)
    day = np.sin(angle)

    # A wide blur means thin shapes (fingers, a glasses temple) don't create
    # tight, high-frequency direction flips that make the tracer loop back
    # on itself — only broad, large-scale edges (a hairline, a jaw) survive
    # to bend the hatch angle.
    smooth = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), sigmaX=16)
    gx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=5)
    gy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=5)
    mag = np.sqrt(gx * gx + gy * gy)

    tx = -gy
    ty = gx
    norm = np.sqrt(tx * tx + ty * ty) + 1e-6
    tx /= norm
    ty /= norm

    # Hard floor: truly flat regions (background vignette, smooth photo
    # noise) get zero tangent influence — otherwise their faint, near-zero
    # gradient direction is unstable and traces spiral around its critical
    # points. Only once the gradient is clearly a real edge does it start
    # nudging the hatch angle, capped well under 1.0 so it's never fully
    # overridden.
    weight = np.where(mag > 6.0, np.clip((mag - 6.0) / 60.0, 0.0, 0.4), 0.0)
    fx = weight * tx + (1 - weight) * dax
    fy = weight * ty + (1 - weight) * day
    fnorm = np.sqrt(fx * fx + fy * fy) + 1e-6
    return fx / fnorm, fy / fnorm


def _trace_streamline(
    x0: float, y0: float, fx: np.ndarray, fy: np.ndarray, mask: np.ndarray, half_len: float, step: float,
    metrics: dict | None = None,
) -> list[list[float]]:
    h, w = mask.shape
    forward: list[list[float]] = []
    backward: list[list[float]] = []

    for direction, bucket in ((1.0, forward), (-1.0, backward)):
        x, y = x0, y0
        px, py = None, None  # previous (smoothed) direction, for momentum
        n_steps = int(half_len / step)
        for _ in range(n_steps):
            if metrics is not None:
                metrics["streamline_steps"] += 1
            xi, yi = int(round(x)), int(round(y))
            if not (0 <= xi < w and 0 <= yi < h) or not mask[yi, xi]:
                break
            dx, dy = fx[yi, xi] * direction, fy[yi, xi] * direction
            if px is not None:
                # Momentum damps sharp turns, so strokes stay straight-ish
                # pencil marks instead of curling into tight loops.
                dx = 0.88 * px + 0.12 * dx
                dy = 0.88 * py + 0.12 * dy
                n = math.hypot(dx, dy) + 1e-6
                dx, dy = dx / n, dy / n
            px, py = dx, dy
            x += dx * step
            y += dy * step
            bucket.append([x, y])

    backward.reverse()
    points = backward + [[x0, y0]] + forward
    return points


def _accum_at(accum: np.ndarray, xi: int, yi: int) -> int:
    return int(accum[yi // ACCUM_CELL_PX, xi // ACCUM_CELL_PX])


def _accumulate(accum: np.ndarray, xi: int, yi: int) -> None:
    cy, cx = yi // ACCUM_CELL_PX, xi // ACCUM_CELL_PX
    if accum[cy, cx] < 255:
        accum[cy, cx] += 1


def _hatch_pass(
    tone: np.ndarray,
    fx: np.ndarray,
    fy: np.ndarray,
    max_intensity: float,
    spacing: float,
    length: float,
    shade: float,
    width: float,
    rng: random.Random,
    accum: np.ndarray,
    *,
    perpendicular: bool = False,
    extra_mask: np.ndarray | None = None,
    color: str | None = None,
    instrumentation: GenerationInstrumentation | None = None,
    pass_name: str = "band:unknown",
    scheduler: ResourceBudgetScheduler | None = None,
) -> list[Stroke]:
    h, w = tone.shape
    mask = tone < max_intensity
    if extra_mask is not None:
        mask = mask & extra_mask
    if not mask.any():
        return []

    if perpendicular:
        fx, fy = -fy, fx

    strokes: list[Stroke] = []
    stats = instrumentation.bucket(pass_name) if instrumentation else None
    def reject(reason: str, region_stats: dict | None = None) -> None:
        if stats is not None:
            stats["rejected_seeds"] += 1
            stats["rejection_reasons"][reason] += 1
            if region_stats is not None:
                region_stats["rejected"] += 1
            instrumentation.total["rejected_seeds"] += 1
    if stats is not None:
        stats["eligible_pixels"] += int(mask.sum())
        stats["mean_tone"] = float(tone[mask].mean()) if mask.any() else 255.0
        if instrumentation._eligible_union is not None:
            new_pixels = mask & ~instrumentation._eligible_union
            stats["new_eligible_pixels"] += int(new_pixels.sum())
            stats["overlap_eligible_pixels"] = stats.get("overlap_eligible_pixels", 0) + int((mask & instrumentation._eligible_union).sum())
            instrumentation._eligible_union |= mask
            instrumentation._eligible_coverage[mask] += 1
        stats["shade"] = shade
    step_y = max(1, int(round(spacing)))
    for gy0 in range(0, h, step_y):
        for gx0 in range(0, w, step_y):
            if stats is not None:
                stats["candidate_evaluations"] += 1
                instrumentation.total["candidate_evaluations"] += 1
            jx = gx0 + rng.uniform(-spacing, spacing) * 0.5
            jy = gy0 + rng.uniform(-spacing, spacing) * 0.5
            xi, yi = int(round(jx)), int(round(jy))
            if not (0 <= xi < w and 0 <= yi < h) or not mask[yi, xi]:
                if (0 <= xi < w and 0 <= yi < h and extra_mask is not None
                        and tone[yi, xi] < max_intensity and not extra_mask[yi, xi]):
                    reject("background_suppression")
                else:
                    reject("outside_mask")
                continue
            if stats is not None:
                region = f"{yi // ACCUM_CELL_PX},{xi // ACCUM_CELL_PX}"
                region_stats = stats["regions"].setdefault(region, {"candidates": 0, "accepted": 0, "rejected": 0})
                region_stats["candidates"] += 1
            if scheduler is not None and not scheduler.permits(pass_name):
                reject("budget_exhausted", stats["regions"][region] if stats is not None else None)
                continue
            if _accum_at(accum, xi, yi) >= _accumulation_cap_for_tone(tone[yi, xi]):
                reject("accumulation_cap", stats["regions"][region] if stats is not None else None)
                continue
            before_steps = stats["streamline_steps"] if stats is not None else 0
            pts = _trace_streamline(jx, jy, fx, fy, mask, half_len=length / 2, step=2.0,
                                    metrics=stats)
            if stats is not None:
                instrumentation.total["streamline_steps"] += stats["streamline_steps"] - before_steps
            if len(pts) < 4:
                reject("trace_invalid", stats["regions"][region] if stats is not None else None)
                continue
            local_shade = shade * rng.uniform(0.85, 1.05)
            strokes.append(Stroke(points=pts, width=width, shade=min(local_shade, 0.95), kind="hatch", color=color))
            _accumulate(accum, xi, yi)
            if scheduler is not None:
                scheduler.accepted_one(pass_name)
            if stats is not None:
                stats["accepted_seeds"] += 1
                stats["regions"][region]["accepted"] += 1
                stats["allocated_points"] += len(pts)
                stats["strokes"] += 1
                instrumentation.total["accepted_seeds"] += 1
                instrumentation.total["allocated_points"] += len(pts)
                instrumentation.total["strokes"] += 1

    return strokes


def _extract_hatch_strokes(
    tone: np.ndarray,
    fx: np.ndarray,
    fy: np.ndarray,
    rng: random.Random,
    accum: np.ndarray,
    background_ceiling: float | None,
    exclude_mask: np.ndarray | None,
    instrumentation: GenerationInstrumentation | None = None,
    scheduler: ResourceBudgetScheduler | None = None,
) -> list[Stroke]:
    # (max_intensity_for_band, seed_spacing_px, stroke_length_px, shade, width)
    # Light bands get dense-but-faint strokes so paper doesn't stay bare —
    # a real pencil sketch has an even light wash, not scattered scratches.
    # Dark bands get denser, longer, heavier strokes, layered from light to
    # dark so tone builds up the way real shading does.
    bands = [
        (240, 9.0, 16, 0.08, 0.7),
        (222, 8.0, 18, 0.14, 0.75),
        (200, 7.0, 20, 0.20, 0.8),
        (178, 6.0, 23, 0.27, 0.9),
        (155, 5.2, 26, 0.35, 1.0),
        (132, 4.6, 28, 0.44, 1.05),
        (108, 4.0, 31, 0.53, 1.1),
        (85, 3.5, 34, 0.63, 1.2),
        (62, 3.0, 36, 0.74, 1.3),
        (40, 2.6, 38, 0.86, 1.4),
    ]

    not_background = None if exclude_mask is None else ~exclude_mask

    all_strokes: list[Stroke] = []
    for max_intensity, spacing, length, shade, width in bands:
        band_name = f"band:{max_intensity}:parallel"
        # Never hatch within MIN_TONAL_SIGNIFICANCE of this image's own
        # brightest tones -- derived from the actual image, not assumed to
        # be pure white, so pale (but not literally white) paper stays
        # clean instead of being covered in faint "light wash" strokes.
        # `background_ceiling` is None when the image has no meaningful
        # background/subject split to begin with (see image_to_strokes) --
        # a uniformly dark or uniformly toned image has nothing to call
        # "background", so every band's own threshold is left unclamped
        # (caught by test_local_graphite_accumulation_is_bounded: the
        # unconditional version of this clamp was suppressing *all*
        # hatching on a flat dark image, not just real background).
        effective_max = max_intensity if background_ceiling is None else min(max_intensity, background_ceiling)
        all_strokes.extend(
            _hatch_pass(
                tone, fx, fy, effective_max, spacing * HATCH_SPACING_SCALE, length, shade, width, rng, accum,
                extra_mask=not_background,
                instrumentation=instrumentation, pass_name=band_name,
                scheduler=scheduler,
            )
        )
        # The deepest shadows get a second, perpendicular pass — real
        # cross-hatching — so they build to a solid dark instead of showing
        # a single streaky direction.
        if shade > CROSS_HATCH_MIN_SHADE:
            all_strokes.extend(
                _hatch_pass(
                    tone, fx, fy, effective_max, spacing * 1.4 * HATCH_SPACING_SCALE, length * 0.8, shade * 0.8,
                    width, rng, accum, perpendicular=True, extra_mask=not_background,
                    instrumentation=instrumentation, pass_name=f"band:{max_intensity}:cross",
                    scheduler=scheduler,
                )
            )

    return all_strokes


def _muted_hex_color(median_bgr: np.ndarray, gray_equivalent: float, mute_factor: float = COLOR_MUTE_FACTOR) -> str:
    """Blends the region's own median color toward neutral gray -- a
    "translucent colored-pencil" look (section 3), not a flat, fully
    saturated paste of the source color.
    """
    gray_bgr = np.array([gray_equivalent, gray_equivalent, gray_equivalent])
    muted = median_bgr * mute_factor + gray_bgr * (1 - mute_factor)
    b, g, r = (int(np.clip(v, 0, 255)) for v in muted)
    return f"#{r:02x}{g:02x}{b:02x}"


def _extract_colored_strokes(
    img_bgr: np.ndarray, tone: np.ndarray, fx: np.ndarray, fy: np.ndarray, rng: random.Random, accum: np.ndarray,
    instrumentation: GenerationInstrumentation | None = None,
    scheduler: ResourceBudgetScheduler | None = None,
) -> tuple[list[Stroke], np.ndarray]:
    """Source-color preservation for Faithful Pencil (section 3): finds
    connected regions of the image that are meaningfully chromatic in
    CIELAB (not merely "not pure gray" -- a real, visible color accent),
    and hatches *those regions only* using a muted version of each
    region's own representative color. Nothing here is hardcoded to any
    specific color or object -- any image with a distinct color accent
    (a red hatband, a blue scarf, a yellow sign) is handled the same way.

    Returns (colored strokes, combined region mask) -- the mask lets the
    caller exclude these pixels from the neutral graphite pass, so color
    doesn't get diluted by gray hatching on top of it.
    """
    # Classify on a lightly denoised copy, not the raw pixels: a real
    # photograph's grass, skin, or sky is never perfectly flat -- ordinary
    # sensor/compression noise jitters hue pixel-to-pixel enough to scatter
    # one real color region across several hue bins, fragmenting it into
    # hundreds of tiny below-threshold islands instead of one coherent
    # region. The median_bgr sampled from each resulting region still comes
    # from the original (non-blurred) pixels, so this only affects which
    # pixels get grouped together, never the final sampled color.
    denoised = cv2.medianBlur(img_bgr, 5)
    lab = cv2.cvtColor(denoised, cv2.COLOR_BGR2LAB).astype(np.float32)
    chroma = np.sqrt((lab[..., 1] - 128.0) ** 2 + (lab[..., 2] - 128.0) ** 2)
    chroma_mask = (chroma > CHROMA_THRESHOLD).astype(np.uint8)
    combined_mask = np.zeros(chroma_mask.shape, dtype=bool)
    if not chroma_mask.any():
        return [], combined_mask

    # Quantize hue before connected-components so two touching or
    # overlapping shapes of different colors (a red hatband against a
    # blue jacket, a green ellipse behind a purple triangle) are kept as
    # separate regions rather than merging into one blob that gets
    # averaged into a single, wrong color -- which would silently drop
    # one of the two colors from the drawing entirely.
    hue = cv2.cvtColor(denoised, cv2.COLOR_BGR2HSV)[..., 0].astype(np.int32)
    hue_bins = (hue * HUE_BIN_COUNT // 180) % HUE_BIN_COUNT

    strokes: list[Stroke] = []

    for bin_index in range(HUE_BIN_COUNT):
        bin_mask = (chroma_mask > 0) & (hue_bins == bin_index)
        if not bin_mask.any():
            continue
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
            bin_mask.astype(np.uint8), connectivity=8
        )

        for label in range(1, num_labels):
            if stats[label, cv2.CC_STAT_AREA] < MIN_COLOR_REGION_AREA:
                continue
            region_mask = labels == label
            combined_mask |= region_mask

            median_bgr = np.median(img_bgr[region_mask], axis=0)
            region_tone = tone[region_mask]
            gray_equivalent = float(np.median(region_tone))
            hex_color = _muted_hex_color(median_bgr, gray_equivalent)

            # Band cutoffs are anchored directly to this region's own tone
            # *percentiles*, not to fixed fractions of (min, max). A region
            # whose tone is tightly clustered near one end of its min/max
            # range (a flat-filled shape where a handful of antialiased
            # boundary pixels stretch the apparent range far past where the
            # real mass of pixels sits) would otherwise get band cutoffs
            # that miss the entire population: fixed fractions assume tone
            # is spread roughly evenly across the span, which true flat
            # regions and strongly skewed photographic regions both
            # violate. Anchoring to percentiles guarantees each band's
            # cutoff actually passes close to that fraction of the
            # region's real pixels regardless of the distribution's shape.
            # The +1.0 epsilon ensures the strict "<" check in _hatch_pass
            # still accepts pixels sitting exactly at a percentile value,
            # which is common when large swaths of the region share one
            # identical flat tone.
            p85, p55, p20 = (float(v) for v in np.percentile(region_tone, [85, 55, 20]))
            color_bands = [
                (p85 + 1.0, 4.2, 20, 0.40, 0.95),
                (p55 + 1.0, 3.4, 25, 0.60, 1.05),
                (p20 + 1.0, 2.7, 29, 0.80, 1.2),
            ]
            for max_intensity, spacing, length, shade, width in color_bands:
                pass_name = f"color:{bin_index}:band:{max_intensity:.1f}"
                strokes.extend(
                    _hatch_pass(
                        tone, fx, fy, max_intensity, spacing * HATCH_SPACING_SCALE, length, shade, width, rng, accum,
                        extra_mask=region_mask, color=hex_color,
                        instrumentation=instrumentation, pass_name=pass_name,
                        scheduler=scheduler,
                    )
                )

    return strokes, combined_mask


def image_to_strokes(image_bytes: bytes, style: str = STYLE_FAITHFUL,
                     instrumentation: GenerationInstrumentation | None = None,
                     scheduler_override: bool | None = None,
                     scheduler_budget_override: int | None = None) -> dict:
    if style not in VALID_STYLES:
        raise ValueError(f"Unknown style: {style!r} (expected one of {VALID_STYLES})")

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not decode image")
    if img.shape[0] * img.shape[1] > MAX_DECODED_PIXELS:
        raise ValueError(
            f"Decoded image is {img.shape[1]}x{img.shape[0]} "
            f"({img.shape[0] * img.shape[1]:,} px), exceeding the {MAX_DECODED_PIXELS:,}px safety limit"
        )

    img = _resize_to_max_dim(img)
    h, w = img.shape[:2]
    if instrumentation is not None and instrumentation._eligible_union is None:
        instrumentation.image_shape = (h, w)
        instrumentation._eligible_union = np.zeros((h, w), dtype=bool)
        instrumentation._eligible_coverage = np.zeros((h, w), dtype=np.uint16)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Smoothed tone target that hatch density follows — broad continuous
    # shading rather than a handful of flat bands. No local-contrast
    # equalization here: CLAHE stretches subtle shading in otherwise bright,
    # smooth areas (a forehead, a cheek) into dark bands, overdarkening
    # exactly the regions that should stay light.
    tone = cv2.bilateralFilter(gray, d=9, sigmaColor=45, sigmaSpace=45)

    fx, fy = _flow_field(gray)
    # Test-only A/B control. Production callers omit this argument and retain
    # the normal activation predicate; the override is never read from an
    # HTTP request or user-controlled input.
    scheduler = (ResourceBudgetScheduler(total_budget=scheduler_budget_override or MAX_HATCH_STROKES,
                                         active=scheduler_override)
                 if scheduler_override is not None else ResourceBudgetScheduler.from_tone(tone))

    rng = random.Random(7)
    accum = np.zeros((math.ceil(h / ACCUM_CELL_PX), math.ceil(w / ACCUM_CELL_PX)), dtype=np.uint8)

    # A background-relative hatch ceiling only makes sense when the image
    # actually *has* a distinguishable flat background separate from a
    # darker subject -- i.e. a real spread between its lightest and more
    # typical tones. For a uniformly dark or uniformly toned image there is
    # no "paper" to protect, so the ceiling is left disengaged (None)
    # rather than clamping every band down to near-zero.
    tone_spread = float(np.percentile(tone, BACKGROUND_PERCENTILE) - np.percentile(tone, 100 - BACKGROUND_PERCENTILE))
    background_ceiling: float | None = None
    if tone_spread >= MIN_BACKGROUND_SPREAD:
        paper_tone = float(np.percentile(tone, BACKGROUND_PERCENTILE))
        background_ceiling = paper_tone - MIN_TONAL_SIGNIFICANCE

    colored_strokes: list[Stroke] = []
    color_region_mask: np.ndarray | None = None
    if style == STYLE_FAITHFUL:
        colored_strokes, color_region_mask = _extract_colored_strokes(
            img, tone, fx, fy, rng, accum, instrumentation, scheduler
        )

    line_strokes = _extract_line_strokes(gray)
    neutral_hatch_strokes = _extract_hatch_strokes(
        tone, fx, fy, rng, accum, background_ceiling, color_region_mask, instrumentation
        , scheduler
    )

    all_hatch = neutral_hatch_strokes + colored_strokes
    all_hatch.sort(key=lambda s: s.shade)
    pre_truncation_hatch_count = len(all_hatch)
    if len(all_hatch) > MAX_HATCH_STROKES:
        # Safety backstop, not a normal-path limit: keeps worst-case memory/
        # payload/processing time bounded for a pathological input (e.g. a
        # very large, very dark, highly detailed image) without silently
        # hanging or OOMing. Deterministic even-stride downsample preserves
        # the light-to-dark shade spread rather than truncating one end of it.
        stride = math.ceil(len(all_hatch) / MAX_HATCH_STROKES)
        all_hatch = all_hatch[::stride]

    # Draw order: light hatching first (block in broad tone), then sparse
    # structural lines, then progressively darker hatching (including
    # colored accents, wherever their shade falls) on top.
    light = [s for s in all_hatch if s.shade <= 0.4]
    dark = [s for s in all_hatch if s.shade > 0.4]
    ordered = light + line_strokes + dark

    result = {
        "width": w,
        "height": h,
        "strokes": [s.to_json() for s in ordered],
    }
    if instrumentation is not None:
        instrumentation.validate_accounting()
        result["instrumentation"] = instrumentation.finish()
        result["instrumentation"]["budget_simulation"] = instrumentation.plan_budgets()
        result["instrumentation"]["resource_scheduler"] = scheduler.report()
        result["instrumentation"]["post_generation"] = {
            "hatch_before_truncation": pre_truncation_hatch_count,
            "hatch_after_truncation": len(all_hatch),
            "truncated": max(0, pre_truncation_hatch_count - len(all_hatch)),
        }
    elif scheduler.active:
        result["resourceScheduler"] = scheduler.report()
    return result
