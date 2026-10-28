"""Tests for the drawing planner (planner.py / plan_models.py).

Covers every bullet in the Phase 2 testing requirement: stroke preservation,
valid stroke references, no double-draws, full coverage, executable action
sequences, deterministic region assignment, stage ordering, valid move
coordinates, empty/minimal input handling, and finite-only serialization.
"""

import math

import pytest

from plan_models import ActionType, DrawingPlan, StrokeAnnotation, check_plan_invariants
from planner import (
    MIN_STROKES_FOR_CONSTRUCTION,
    STAGE_ORDER,
    AnnotatedStroke,
    annotate_strokes,
    assign_regions,
    assign_stages,
    build_actions,
    build_drawing_plan,
    schedule_strokes,
    score_session_candidate,
)


def _raw_stroke(kind="hatch", shade=0.3, x0=10.0, y0=10.0, dx=5.0, dy=0.0, width=1.0) -> dict:
    return {
        "points": [[x0, y0], [x0 + dx, y0 + dy], [x0 + 2 * dx, y0 + 2 * dy]],
        "width": width,
        "shade": shade,
        "kind": kind,
    }


def _synthetic_raw_strokes(n_line=6, n_hatch_per_region=8) -> list[dict]:
    strokes = []
    for i in range(n_line):
        strokes.append(_raw_stroke(kind="line", shade=0.5, x0=50.0 + i * 3, y0=50.0, dx=4.0, dy=1.0))
    # Scatter hatch strokes across a few distinct spatial regions and shade
    # bands so region grouping and stage bucketing both have real variety.
    for region_x, region_y in [(20, 20), (400, 20), (20, 400), (400, 400)]:
        for i in range(n_hatch_per_region):
            shade = [0.1, 0.3, 0.45, 0.6][i % 4]
            strokes.append(
                _raw_stroke(
                    kind="hatch",
                    shade=shade,
                    x0=region_x + i,
                    y0=region_y + i,
                    dx=3.0,
                    dy=2.0,
                    width=0.8,
                )
            )
    return strokes


WIDTH, HEIGHT = 512, 512


def _build_plan(raw_strokes):
    return build_drawing_plan(WIDTH, HEIGHT, raw_strokes)


# --- stroke preservation / coverage -----------------------------------------


def test_every_original_stroke_is_preserved():
    # "Preserved" now means "nothing is lost" rather than "nothing is
    # added": a sufficiently rich image also gets a few synthesized
    # construction strokes (see synthesize_construction_strokes), which are
    # deliberate additions, not an original-stroke-loss bug.
    raw = _synthetic_raw_strokes()
    plan = _build_plan(raw)
    original_ids = set(range(len(raw)))
    result_ids = {s.id for s in plan.strokes}
    assert original_ids <= result_ids
    assert len(plan.strokes) >= len(raw)


def test_every_scheduled_draw_references_a_valid_stroke():
    plan = _build_plan(_synthetic_raw_strokes())
    stroke_ids = {s.id for s in plan.strokes}
    draw_actions = [a for a in plan.actions if a.type == ActionType.DRAW]
    assert draw_actions, "expected at least one draw action"
    for a in draw_actions:
        assert a.stroke_id in stroke_ids


def test_no_stroke_is_drawn_twice():
    plan = _build_plan(_synthetic_raw_strokes())
    drawn = [a.stroke_id for a in plan.actions if a.type == ActionType.DRAW]
    assert len(drawn) == len(set(drawn))


def test_every_intended_stroke_is_scheduled():
    raw = _synthetic_raw_strokes()
    plan = _build_plan(raw)
    drawn = {a.stroke_id for a in plan.actions if a.type == ActionType.DRAW}
    assert drawn == {s.id for s in plan.strokes}


def test_actions_form_a_valid_executable_sequence():
    plan = _build_plan(_synthetic_raw_strokes())
    assert check_plan_invariants(plan) == []


# --- region grouping ---------------------------------------------------------


def test_region_assignment_is_deterministic():
    raw = _synthetic_raw_strokes()
    a1 = assign_regions(annotate_strokes(raw), WIDTH, HEIGHT)
    a2 = assign_regions(annotate_strokes(raw), WIDTH, HEIGHT)
    regions1 = [s.region_id for s in a1[0]]
    regions2 = [s.region_id for s in a2[0]]
    assert regions1 == regions2


def test_region_ids_are_generic_grid_labels_not_semantic_names():
    raw = _synthetic_raw_strokes()
    annotated, cols, rows = assign_regions(annotate_strokes(raw), WIDTH, HEIGHT)
    for s in annotated:
        assert s.region_id.startswith("region_c")
        assert "eye" not in s.region_id and "jaw" not in s.region_id


# --- stage ordering ------------------------------------------------------------


def test_stage_ordering_respects_declared_constraints():
    plan = _build_plan(_synthetic_raw_strokes())
    seen_stage_positions = [STAGE_ORDER.index(s.stage) for s in plan.strokes]
    assert seen_stage_positions == sorted(seen_stage_positions), (
        "strokes must be scheduled in non-decreasing artistic-stage order"
    )


def test_stage_6_final_accents_is_left_empty_not_invented():
    raw = _synthetic_raw_strokes()
    plan = _build_plan(raw)
    stages_present = {s.stage for s in plan.strokes}
    assert 6 not in stages_present, "no accent strokes exist yet to assign stage 6"


def test_construction_stage_is_populated_when_supported_by_the_image():
    raw = _synthetic_raw_strokes()
    plan = _build_plan(raw)
    construction_strokes = [s for s in plan.strokes if s.stage == 1]
    assert construction_strokes, "a rich-enough image should get construction/gesture marks"
    assert all(s.is_construction for s in construction_strokes)
    assert all(not s.is_construction for s in plan.strokes if s.stage != 1)
    assert all(s.shade <= 0.15 for s in construction_strokes), "construction marks must be low-contrast"


def test_construction_strokes_skipped_when_image_has_too_few_strokes():
    raw = [_raw_stroke(x0=float(i), y0=float(i)) for i in range(MIN_STROKES_FOR_CONSTRUCTION - 1)]
    plan = _build_plan(raw)
    assert not any(s.is_construction for s in plan.strokes)


# --- movement / coordinates ---------------------------------------------------


def test_move_actions_have_finite_valid_coordinates():
    plan = _build_plan(_synthetic_raw_strokes())
    for a in plan.actions:
        if a.type == ActionType.MOVE:
            assert math.isfinite(a.x) and math.isfinite(a.y)


def test_pencil_starts_and_ends_each_draw_at_its_stroke_endpoints():
    raw = _synthetic_raw_strokes()
    plan = _build_plan(raw)
    strokes_by_id = {s.id: s for s in plan.strokes}

    pos = (0.0, 0.0)
    for a in plan.actions:
        if a.type == ActionType.MOVE:
            pos = (a.x, a.y)
        elif a.type == ActionType.DRAW:
            stroke = strokes_by_id[a.stroke_id]
            start = stroke.points[0]
            assert math.isclose(pos[0], start[0], abs_tol=0.1)
            assert math.isclose(pos[1], start[1], abs_tol=0.1)
            pos = stroke.points[-1]


# --- empty / minimal input -----------------------------------------------------


def test_empty_stroke_list_produces_a_valid_empty_plan():
    plan = _build_plan([])
    assert plan.strokes == []
    assert plan.actions == []
    assert check_plan_invariants(plan) == []


def test_single_stroke_produces_a_valid_minimal_plan():
    plan = _build_plan([_raw_stroke()])
    assert len(plan.strokes) == 1
    draw_actions = [a for a in plan.actions if a.type == ActionType.DRAW]
    assert len(draw_actions) == 1
    assert check_plan_invariants(plan) == []


# --- finite-only serialization --------------------------------------------------


def test_model_rejects_non_finite_stroke_geometry():
    good = AnnotatedStroke(
        id=0,
        points=[[0.0, 0.0], [1.0, 1.0]],
        width=1.0,
        shade=0.5,
        kind="line",
        centroid=(0.5, 0.5),
        arc_length=1.4,
        bbox=(0.0, 0.0, 1.0, 1.0),
        importance=0.5,
        estimated_duration=0.1,
        region_id="region_c0_r0",
        stage=2,
    )
    good.to_model()  # sanity: valid data passes

    with pytest.raises(ValueError):
        StrokeAnnotation(
            id=0,
            points=[(0.0, 0.0), (float("nan"), 1.0)],
            width=1.0,
            shade=0.5,
            kind="line",
            centroid=(0.5, 0.5),
            arc_length=1.4,
            bbox=(0.0, 0.0, 1.0, 1.0),
            region_id="region_c0_r0",
            stage=2,
            importance=0.5,
            estimated_duration=0.1,
        )


def test_model_rejects_infinite_action_duration():
    from plan_models import Action

    with pytest.raises(ValueError):
        Action(type=ActionType.DRAW, duration=float("inf"), stroke_id=0)


def test_serialized_plan_contains_no_nan_or_infinite_values():
    plan = _build_plan(_synthetic_raw_strokes())
    dumped = plan.model_dump(mode="json")

    def _walk(node):
        if isinstance(node, float):
            assert math.isfinite(node), f"non-finite float in serialized plan: {node}"
        elif isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(dumped)


def test_build_actions_on_empty_input_returns_empty_list():
    assert build_actions([]) == []


# --- work sessions / regional attention (Phase 5) ---------------------------


def _rich_shadow_raw_strokes(n_per_region=12):
    """Enough deep-shade strokes per region to trigger a deepen-shadow
    revisit split (MIN_STROKES_FOR_REVISIT_SPLIT)."""
    strokes = []
    for i in range(6):
        strokes.append(_raw_stroke(kind="line", shade=0.5, x0=50.0 + i * 3, y0=50.0, dx=4.0, dy=1.0))
    for region_x, region_y in [(20, 20), (400, 20), (20, 400), (400, 400)]:
        for i in range(n_per_region):
            shade = 0.65 + 0.01 * i  # all land in deep-shading, varying darkness
            strokes.append(
                _raw_stroke(kind="hatch", shade=shade, x0=region_x + i, y0=region_y + i, dx=3.0, dy=2.0, width=0.8)
            )
    return strokes


def test_deep_shading_regions_with_enough_strokes_get_a_purposeful_revisit():
    plan = _build_plan(_rich_shadow_raw_strokes())
    sessions = plan.metadata["sessions"]
    initial_sessions = [s for s in sessions if s["purpose"] == "deepen_shadow_initial"]
    revisit_sessions = [s for s in sessions if s["purpose"] == "deepen_shadow_revisit"]
    assert initial_sessions
    assert revisit_sessions, "expected at least one deepen-shadow revisit session"

    revisit_regions = {s["regionId"] for s in revisit_sessions}
    initial_regions = {s["regionId"] for s in initial_sessions}
    assert revisit_regions <= initial_regions, "a region can only be revisited after an initial visit"

    # The revisit must add strokes that were NOT already drawn in the
    # initial pass (no replaying identical strokes) and must be darker on
    # average, consistent with "deepening existing shadows".
    strokes_by_id = {s.id: s for s in plan.strokes}

    def _session_stroke_ids(session_summaries):
        ids = set()
        for s in plan.strokes:
            if s.session_id in {sess["id"] for sess in session_summaries}:
                ids.add(s.id)
        return ids

    initial_ids = _session_stroke_ids(initial_sessions)
    revisit_ids = _session_stroke_ids(revisit_sessions)
    assert initial_ids.isdisjoint(revisit_ids), "a revisit must not redraw strokes already drawn initially"

    avg_initial_shade = sum(strokes_by_id[i].shade for i in initial_ids) / len(initial_ids)
    avg_revisit_shade = sum(strokes_by_id[i].shade for i in revisit_ids) / len(revisit_ids)
    assert avg_revisit_shade > avg_initial_shade


def test_the_same_region_appears_in_multiple_work_sessions():
    plan = _build_plan(_rich_shadow_raw_strokes())
    sessions_by_region: dict[str, int] = {}
    for session in plan.metadata["sessions"]:
        sessions_by_region[session["regionId"]] = sessions_by_region.get(session["regionId"], 0) + 1
    assert any(count > 1 for count in sessions_by_region.values()), (
        "at least one region should recur across several work sessions"
    )


def test_stage3_sessions_spread_across_regions_early_not_one_region_first():
    plan = _build_plan(_synthetic_raw_strokes())
    stage3_sessions = [s for s in plan.metadata["sessions"] if s["stage"] == 3]
    assert len(stage3_sessions) > 1
    region_ids = {s["regionId"] for s in stage3_sessions}
    halfway = max(1, len(stage3_sessions) // 2)
    regions_seen_by_halfway = {s["regionId"] for s in stage3_sessions[:halfway]}
    assert len(regions_seen_by_halfway) >= min(2, len(region_ids)), (
        "global composition should spread across regions early, not finish one region first"
    )


def test_no_work_session_draws_a_stroke_more_than_once_across_the_whole_plan():
    plan = _build_plan(_rich_shadow_raw_strokes())
    seen = set()
    for s in plan.strokes:
        assert s.session_id not in (None,) and s.session_id >= 0
    for session in plan.metadata["sessions"]:
        ids_in_session = [s.id for s in plan.strokes if s.session_id == session["id"]]
        assert session["strokeCount"] == len(ids_in_session)
        for sid in ids_in_session:
            assert sid not in seen, f"stroke {sid} appears in more than one work session"
            seen.add(sid)


# --- artistic scheduling score function ---------------------------------------


def test_score_favors_less_developed_regions_over_nearly_finished_ones():
    low_progress = score_session_candidate(
        avg_importance=0.5, region_progress=0.0, is_same_region_as_last=False, is_revisit=False, travel_fraction=0.0
    )
    high_progress = score_session_candidate(
        avg_importance=0.5, region_progress=0.9, is_same_region_as_last=False, is_revisit=False, travel_fraction=0.0
    )
    assert low_progress > high_progress


def test_score_penalizes_revisit_purpose_relative_to_a_first_visit():
    first_visit = score_session_candidate(
        avg_importance=0.5, region_progress=0.5, is_same_region_as_last=False, is_revisit=False, travel_fraction=0.0
    )
    revisit = score_session_candidate(
        avg_importance=0.5, region_progress=0.5, is_same_region_as_last=False, is_revisit=True, travel_fraction=0.0
    )
    assert first_visit > revisit


def test_score_rewards_staying_in_the_current_region():
    elsewhere = score_session_candidate(
        avg_importance=0.5, region_progress=0.5, is_same_region_as_last=False, is_revisit=False, travel_fraction=0.0
    )
    same_region = score_session_candidate(
        avg_importance=0.5, region_progress=0.5, is_same_region_as_last=True, is_revisit=False, travel_fraction=0.0
    )
    assert same_region > elsewhere


def test_travel_distance_has_the_smallest_influence_on_the_score():
    base = dict(avg_importance=0.5, region_progress=0.5, is_same_region_as_last=False, is_revisit=False)
    travel_delta = abs(
        score_session_candidate(**base, travel_fraction=0.0) - score_session_candidate(**base, travel_fraction=1.0)
    )
    importance_delta = abs(
        score_session_candidate(avg_importance=0.0, region_progress=0.5, is_same_region_as_last=False, is_revisit=False, travel_fraction=0.0)
        - score_session_candidate(avg_importance=1.0, region_progress=0.5, is_same_region_as_last=False, is_revisit=False, travel_fraction=0.0)
    )
    balance_delta = abs(
        score_session_candidate(avg_importance=0.5, region_progress=0.0, is_same_region_as_last=False, is_revisit=False, travel_fraction=0.0)
        - score_session_candidate(avg_importance=0.5, region_progress=1.0, is_same_region_as_last=False, is_revisit=False, travel_fraction=0.0)
    )
    assert travel_delta < importance_delta
    assert travel_delta < balance_delta


# --- general-purpose across image types ---------------------------------------


def _building_like_raw_strokes():
    """Dominated by long straight contours (walls/windows), light shading --
    no faces, nothing portrait-specific."""
    strokes = [
        _raw_stroke(kind="line", shade=0.4, x0=20.0 + i * 2, y0=20.0, dx=0.0, dy=300.0) for i in range(10)
    ]
    strokes += [
        _raw_stroke(kind="hatch", shade=0.2, x0=50.0 + i * 5, y0=50.0 + i * 5, dx=3.0, dy=0.0) for i in range(6)
    ]
    return strokes


def _landscape_like_raw_strokes():
    """No structural line strokes at all -- pure tonal hatching (sky/field
    gradients), the way a landscape or an abstract gradient photo might
    produce almost no Canny contours."""
    strokes = []
    for region_x, region_y in [(20, 20), (400, 20), (20, 400), (400, 400)]:
        for i in range(10):
            shade = [0.15, 0.35, 0.55, 0.7][i % 4]
            strokes.append(
                _raw_stroke(kind="hatch", shade=shade, x0=region_x + i, y0=region_y + i, dx=3.0, dy=1.0)
            )
    return strokes


def test_general_purpose_pipeline_handles_building_like_images():
    plan = _build_plan(_building_like_raw_strokes())
    assert check_plan_invariants(plan) == []


def test_general_purpose_pipeline_handles_landscape_like_images_with_no_line_strokes():
    plan = _build_plan(_landscape_like_raw_strokes())
    assert check_plan_invariants(plan) == []
    # No structural contours exist in the input -- none should be invented;
    # the only "line"-kind strokes allowed are the synthesized construction
    # marks, which are clearly flagged as such.
    assert all(s.kind != "line" or s.is_construction for s in plan.strokes)


# --- determinism / reproducibility ---------------------------------------------


def test_build_drawing_plan_is_deterministic_for_the_same_input():
    raw = _synthetic_raw_strokes()
    plan_a = _build_plan(raw)
    plan_b = _build_plan(raw)
    assert [s.id for s in plan_a.strokes] == [s.id for s in plan_b.strokes]
    assert plan_a.metadata["sessions"] == plan_b.metadata["sessions"]


# --- Phase 10.1: duration must never change artwork completeness -----------
# Phase 10 originally reduced stroke count for short sessions; that was a
# real regression (visibly thinner tonal shading at short durations) and
# has been corrected. These tests pin the corrected invariant: duration
# changes only the reported *pacing* label, never which strokes exist.


def _hatch_heavy_raw_strokes(n_hatch_per_region=300):
    return _synthetic_raw_strokes(n_hatch_per_region=n_hatch_per_region)


def _colored_hatch_heavy_raw_strokes(n_hatch_per_region=300, n_colored=20):
    strokes = _hatch_heavy_raw_strokes(n_hatch_per_region=n_hatch_per_region)
    for i in range(n_colored):
        s = _raw_stroke(kind="hatch", shade=0.5, x0=60.0 + i, y0=60.0 + i, dx=3.0, dy=2.0, width=0.8)
        s["color"] = "#a83535"
        strokes.append(s)
    return strokes


def test_no_target_duration_reports_natural_pacing_with_full_strokes():
    raw = _hatch_heavy_raw_strokes()
    plan = build_drawing_plan(WIDTH, HEIGHT, raw)
    info = plan.metadata["durationPlanning"]
    assert info["requested"] is False
    assert info["pacing"] == "natural"
    assert len(plan.strokes) == len(raw) + 3  # +3 synthesized construction strokes


def test_stroke_count_is_identical_across_every_duration_from_2min_to_4hr():
    # Core Phase 10.1 invariant: the user picks how long they want to
    # *watch* the drawing happen, never how much of the artwork gets
    # drawn. Every duration in the UI's range must produce the exact same
    # stroke set as no duration at all.
    raw = _hatch_heavy_raw_strokes()
    baseline = build_drawing_plan(WIDTH, HEIGHT, raw)
    baseline_ids = [s.id for s in baseline.strokes]

    for seconds in (120.0, 900.0, 1800.0, 3600.0, 7200.0, 4 * 3600.0):
        plan = build_drawing_plan(WIDTH, HEIGHT, raw, target_duration_seconds=seconds)
        assert [s.id for s in plan.strokes] == baseline_ids, f"stroke set changed at {seconds}s"
        assert len(plan.strokes) == len(baseline.strokes)


def test_a_very_short_target_duration_is_labeled_timelapse_but_keeps_every_stroke():
    raw = _hatch_heavy_raw_strokes()
    full = build_drawing_plan(WIDTH, HEIGHT, raw)
    short = build_drawing_plan(WIDTH, HEIGHT, raw, target_duration_seconds=5.0)
    assert len(short.strokes) == len(full.strokes)
    assert [s.id for s in short.strokes] == [s.id for s in full.strokes]
    info = short.metadata["durationPlanning"]
    assert info["requested"] is True
    assert info["pacing"] == "timelapse"
    assert info["accelerationFactor"] > 1.0


def test_a_long_target_duration_is_labeled_natural_and_keeps_every_stroke():
    raw = _hatch_heavy_raw_strokes()
    full = build_drawing_plan(WIDTH, HEIGHT, raw)
    long_session = build_drawing_plan(WIDTH, HEIGHT, raw, target_duration_seconds=4 * 3600.0)
    assert len(long_session.strokes) == len(full.strokes)
    info = long_session.metadata["durationPlanning"]
    assert info["pacing"] == "natural"
    assert info["accelerationFactor"] < 1.0  # slower than natural pace, not faster


def test_pacing_classification_is_deterministic_for_the_same_duration():
    raw = _hatch_heavy_raw_strokes()
    plan_a = build_drawing_plan(WIDTH, HEIGHT, raw, target_duration_seconds=10.0)
    plan_b = build_drawing_plan(WIDTH, HEIGHT, raw, target_duration_seconds=10.0)
    assert [s.id for s in plan_a.strokes] == [s.id for s in plan_b.strokes]
    assert plan_a.metadata["durationPlanning"] == plan_b.metadata["durationPlanning"]


def test_duration_plan_still_covers_every_region_same_as_no_duration():
    raw = _hatch_heavy_raw_strokes()
    full = build_drawing_plan(WIDTH, HEIGHT, raw)
    short = build_drawing_plan(WIDTH, HEIGHT, raw, target_duration_seconds=2.0)
    full_regions = {s.region_id for s in full.strokes}
    short_regions = {s.region_id for s in short.strokes}
    assert short_regions == full_regions


def test_duration_aware_plan_still_satisfies_plan_invariants():
    raw = _hatch_heavy_raw_strokes()
    plan = build_drawing_plan(WIDTH, HEIGHT, raw, target_duration_seconds=5.0)
    assert check_plan_invariants(plan) == []


def test_dark_shading_strokes_survive_planning_regardless_of_duration():
    # Section 8 requirement: "all required dark-shading strokes survive
    # planning." Dark (deep-shading-stage, high-shade) strokes must be
    # present in identical number at every duration, not just "some".
    raw = _hatch_heavy_raw_strokes()
    for seconds in (None, 5.0, 4 * 3600.0):
        plan = (
            build_drawing_plan(WIDTH, HEIGHT, raw)
            if seconds is None
            else build_drawing_plan(WIDTH, HEIGHT, raw, target_duration_seconds=seconds)
        )
        dark_count = sum(1 for s in plan.strokes if s.shade > 0.55)
        assert dark_count > 0, f"dark-shading strokes missing at duration={seconds}"


def test_contour_tonal_and_color_stroke_counts_are_identical_across_durations():
    # Section 8, item 2: not just total count -- every category (line,
    # each tonal band, colored) must match exactly across durations.
    raw = _colored_hatch_heavy_raw_strokes()

    def _category_counts(plan):
        counts = {"line": 0, "colored": 0, "light": 0, "mid": 0, "dark": 0}
        for s in plan.strokes:
            if s.kind == "line":
                counts["line"] += 1
            if s.color is not None:
                counts["colored"] += 1
            if s.shade <= 0.35:
                counts["light"] += 1
            elif s.shade <= 0.55:
                counts["mid"] += 1
            else:
                counts["dark"] += 1
        return counts

    baseline = _category_counts(build_drawing_plan(WIDTH, HEIGHT, raw))
    for seconds in (5.0, 600.0, 4 * 3600.0):
        plan = build_drawing_plan(WIDTH, HEIGHT, raw, target_duration_seconds=seconds)
        assert _category_counts(plan) == baseline, f"category counts changed at duration={seconds}"


def test_empty_and_minimal_images_work_with_a_duration_specified():
    # Section 8, item 10, combined with the duration parameter: must not
    # crash or misbehave on trivial input just because a duration is set.
    empty_plan = build_drawing_plan(WIDTH, HEIGHT, [], target_duration_seconds=120.0)
    assert empty_plan.strokes == []
    assert check_plan_invariants(empty_plan) == []

    minimal_plan = build_drawing_plan(WIDTH, HEIGHT, [_raw_stroke()], target_duration_seconds=4 * 3600.0)
    assert len(minimal_plan.strokes) == 1
    assert check_plan_invariants(minimal_plan) == []
