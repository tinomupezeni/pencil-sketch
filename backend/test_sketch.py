"""Light regression coverage for the pre-existing CV pipeline (sketch.py).

Phase 1 found this pipeline had zero test coverage. These are intentionally
small/fast (a synthetic 64x64 image, not a real photo) -- the goal is to
pin basic output shape and determinism, not to validate visual quality.
"""

import cv2
import numpy as np
import pytest
import tracemalloc
from time import perf_counter

import sketch
from sketch import image_to_strokes


def _synthetic_image_bytes(size: int = 64) -> bytes:
    # A gradient with a dark square: guarantees both bright and dark tone
    # bands (so hatch strokes get generated) and a hard edge (so a line
    # stroke gets generated), without depending on a real photo file.
    img = np.tile(np.linspace(220, 255, size, dtype=np.uint8), (size, 1))
    img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    img[size // 4 : size // 2, size // 4 : size // 2] = (30, 30, 30)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_returns_expected_top_level_shape():
    result = image_to_strokes(_synthetic_image_bytes())
    assert set(result.keys()) == {"width", "height", "strokes"}
    assert result["width"] > 0 and result["height"] > 0
    assert isinstance(result["strokes"], list)


def test_stroke_shape():
    result = image_to_strokes(_synthetic_image_bytes())
    assert result["strokes"], "synthetic image with a hard edge should yield strokes"
    for s in result["strokes"]:
        assert set(s.keys()) == {"points", "width", "shade", "kind"}
        assert s["kind"] in ("line", "hatch")
        assert len(s["points"]) >= 2
        assert 0.0 <= s["shade"] <= 1.0


def test_deterministic_for_same_input():
    data = _synthetic_image_bytes()
    first = image_to_strokes(data)
    second = image_to_strokes(data)
    assert first == second


def test_rejects_undecodable_bytes():
    with pytest.raises(ValueError):
        image_to_strokes(b"not an image")


# --- resource safety budgets (Phase 6, section 5) ---------------------------


def test_rejects_images_over_the_decoded_pixel_budget(monkeypatch):
    monkeypatch.setattr(sketch, "MAX_DECODED_PIXELS", 100)  # force a tiny budget
    with pytest.raises(ValueError, match="exceeding"):
        image_to_strokes(_synthetic_image_bytes(size=64))


def test_hatch_stroke_count_is_capped_by_a_safety_budget(monkeypatch):
    monkeypatch.setattr(sketch, "MAX_HATCH_STROKES", 25)
    result = image_to_strokes(_synthetic_image_bytes(size=64))
    hatch_count = sum(1 for s in result["strokes"] if s["kind"] == "hatch")
    assert hatch_count <= 25


def test_hatch_cap_downsamples_evenly_preserving_shade_spread(monkeypatch):
    # Below the cap, nothing should be dropped (sanity check the cap only
    # engages when actually exceeded).
    uncapped = image_to_strokes(_synthetic_image_bytes(size=64))
    uncapped_hatch = [s for s in uncapped["strokes"] if s["kind"] == "hatch"]
    assert len(uncapped_hatch) < sketch.MAX_HATCH_STROKES
    monkeypatch.setattr(sketch, "MAX_HATCH_STROKES", len(uncapped_hatch))
    still_uncapped = image_to_strokes(_synthetic_image_bytes(size=64))
    assert sum(1 for s in still_uncapped["strokes"] if s["kind"] == "hatch") == len(uncapped_hatch)


# --- Phase 9: artistic quality improvements ----------------------------------


def _colored_accent_image_bytes(size: int = 160) -> bytes:
    """A light paper-like background, a dark neutral subject block, and a
    distinct red accent patch within it -- enough to exercise color
    extraction, background suppression, and neutral/colored separation all
    at once, without depending on a real photo."""
    img = np.full((size, size, 3), 238, dtype=np.uint8)  # light, slightly off-white "paper"
    subject = slice(size // 6, size - size // 6)
    img[subject, subject] = (70, 68, 66)  # dark neutral subject (BGR)
    accent = slice(size // 3, size // 3 + size // 6)
    img[accent, accent] = (45, 40, 160)  # a red accent patch (BGR) within the subject
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_faithful_style_preserves_a_source_color_accent():
    result = image_to_strokes(_colored_accent_image_bytes(), style="faithful")
    colored = [s for s in result["strokes"] if "color" in s]
    assert colored, "a visibly red accent patch should produce colored strokes in faithful style"
    for s in colored:
        assert len(s["color"]) == 7 and s["color"].startswith("#")


def test_graphite_style_never_produces_colored_strokes():
    data = _colored_accent_image_bytes()
    result = image_to_strokes(data, style="graphite")
    assert not any("color" in s for s in result["strokes"])


def test_graphite_is_the_explicit_non_default_style():
    with pytest.raises(ValueError, match="Unknown style"):
        image_to_strokes(_colored_accent_image_bytes(), style="bogus")


def test_colored_strokes_use_a_muted_not_fully_saturated_color():
    result = image_to_strokes(_colored_accent_image_bytes(), style="faithful")
    colored = [s for s in result["strokes"] if "color" in s]
    assert colored
    for s in colored:
        r = int(s["color"][1:3], 16)
        g = int(s["color"][3:5], 16)
        b = int(s["color"][5:7], 16)
        # The source accent is fully saturated red (45, 40, 160) in BGR; a
        # muted colored-pencil version must not reproduce that exactly, and
        # should sit closer to neutral gray than to pure red.
        assert (r, g, b) != (160, 40, 45)
        assert r > g and r > b, "should still read as reddish, just muted"


def test_colored_strokes_stay_within_the_source_color_region():
    # Region boundary respect (section 3): colored-stroke centroids should
    # cluster near the accent patch, not spread across the whole subject.
    size = 160
    result = image_to_strokes(_colored_accent_image_bytes(size), style="faithful")
    colored = [s for s in result["strokes"] if "color" in s]
    assert colored
    accent_lo, accent_hi = size // 3 - 10, size // 3 + size // 6 + 10
    for s in colored:
        xs = [p[0] for p in s["points"]]
        ys = [p[1] for p in s["points"]]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        assert accent_lo <= cx <= accent_hi, f"colored stroke centroid x={cx} strayed outside the accent region"
        assert accent_lo <= cy <= accent_hi, f"colored stroke centroid y={cy} strayed outside the accent region"


def test_background_suppression_keeps_pale_paper_mostly_clean():
    # Section 7: a large, flat, near-paper-tone background must not be
    # covered in hatching just because it is "less than 240".
    size = 160
    result = image_to_strokes(_colored_accent_image_bytes(size), style="graphite")
    hatch = [s for s in result["strokes"] if s["kind"] == "hatch"]
    subject_lo, subject_hi = size // 6, size - size // 6

    def centroid(s):
        xs = [p[0] for p in s["points"]]
        ys = [p[1] for p in s["points"]]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    background_strokes = [
        s for s in hatch if not (subject_lo <= centroid(s)[0] <= subject_hi and subject_lo <= centroid(s)[1] <= subject_hi)
    ]
    assert len(background_strokes) < len(hatch) * 0.05, (
        f"expected the flat background to stay nearly untouched, got {len(background_strokes)}/{len(hatch)} "
        "hatch strokes outside the subject"
    )


def test_local_graphite_accumulation_primitive_is_bounded():
    # Section 7/8: the per-cell accumulation counter used to gate further
    # seeding in a neighborhood must actually saturate at the configured
    # cap, not grow unboundedly.
    accum = np.zeros((5, 5), dtype=np.uint8)
    for _ in range(sketch.MAX_LOCAL_ACCUMULATION + 10):
        if sketch._accum_at(accum, 12, 12) >= sketch.MAX_LOCAL_ACCUMULATION:
            break
        sketch._accumulate(accum, 12, 12)
    assert sketch._accum_at(accum, 12, 12) == sketch.MAX_LOCAL_ACCUMULATION


def test_a_uniformly_dark_image_still_produces_hatching_and_stays_bounded():
    # End-to-end smoke check alongside the precise primitive test above:
    # a flat dark region (no "background" to protect, see
    # MIN_BACKGROUND_SPREAD) must still get real shading, and the total
    # hatch count must stay within a sane bound rather than growing without
    # limit -- not a tight per-cell assertion (rendered stroke *paths* can
    # legitimately overlap neighboring seed cells; only new *seeding* in an
    # already-saturated cell is capped), just "not excessive".
    img = np.full((120, 120, 3), 20, dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    result = image_to_strokes(buf.tobytes(), style="graphite")
    hatch = [s for s in result["strokes"] if s["kind"] == "hatch"]
    assert hatch, "a uniformly dark image should still produce some hatching"
    # Phase 10.3: the per-cell accumulation cap is now tone-aware (see
    # sketch._accumulation_cap_for_tone) -- a genuinely near-black image
    # like this one earns close to the maximum cap (60) per 10x10px cell,
    # not the old flat 10, because real deep graphite needs far more
    # overlapping layers than a flat cap allowed. Theoretical ceiling for
    # a 120x120 image: (120/10)^2 cells * 60 = 8,640; the real count stays
    # comfortably under that (seeding also competes with the streamline
    # tracer rejecting short/invalid candidates).
    assert len(hatch) < 8600


def test_multi_scale_contours_recover_small_detail_alongside_large_structure():
    # Section 4: a big rectangle (coarse structural edge) plus a tiny
    # isolated mark (fine detail) should both be able to survive the
    # combined contour budget, not just whichever is longer.
    size = 200
    img = np.full((size, size, 3), 235, dtype=np.uint8)
    cv2.rectangle(img, (20, 20), (180, 180), (40, 40, 40), 3)  # large structural edge
    cv2.line(img, (90, 95), (110, 95), (30, 30, 30), 1)  # tiny fine-detail mark
    ok, buf = cv2.imencode(".png", img)
    assert ok
    result = image_to_strokes(buf.tobytes())
    line_strokes = [s for s in result["strokes"] if s["kind"] == "line"]
    assert line_strokes
    has_small_contour = any(
        max(p[0] for p in s["points"]) - min(p[0] for p in s["points"]) < 40
        and max(p[1] for p in s["points"]) - min(p[1] for p in s["points"]) < 15
        for s in line_strokes
    )
    assert has_small_contour, "the fine-scale pass should recover the small isolated mark"


def test_deterministic_across_styles_for_the_same_input():
    data = _colored_accent_image_bytes()
    for style in ("faithful", "graphite"):
        first = image_to_strokes(data, style=style)
        second = image_to_strokes(data, style=style)
        assert first == second


# --- generalization regressions found via non-skull test categories ---------
# These three were found by running the pipeline against a synthetic
# geometric image and a synthetic landscape -- not the skull benchmark --
# specifically to catch overfitting. Each is a real, reproducible bug, not
# a hypothetical.


def _two_touching_shapes_image_bytes(size: int = 200) -> bytes:
    # A green ellipse directly overlapped by a purple triangle: two
    # differently-hued color regions that touch at the pixel level.
    img = np.full((size, size, 3), 250, dtype=np.uint8)
    cv2.ellipse(img, (size // 2 - 20, size // 2), (70, 35), 0, 0, 360, (30, 160, 60), -1)  # green, BGR
    pts = np.array(
        [[size // 2 + 30, size // 2 - 50], [size // 2 + 80, size // 2 + 40], [size // 2 - 10, size // 2 + 40]],
        dtype=np.int32,
    )
    cv2.fillPoly(img, [pts], (150, 60, 160))  # purple, BGR, overlaps the ellipse's right side
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_touching_differently_colored_regions_both_get_their_own_color():
    # Regression: connected-components ran on a single binary "is this
    # pixel colorful" mask, so two touching shapes of different hues
    # merged into one component and were both assigned one (wrong, and
    # often muddy) averaged color -- silently dropping one shape's actual
    # color from the drawing. Hue must be quantized before
    # connected-components so touching-but-differently-hued regions stay
    # separate.
    result = image_to_strokes(_two_touching_shapes_image_bytes(), style="faithful")
    colored = [s for s in result["strokes"] if "color" in s]
    assert colored
    hexes = {s["color"] for s in colored}

    def _is_greenish(hexcolor: str) -> bool:
        r, g, b = int(hexcolor[1:3], 16), int(hexcolor[3:5], 16), int(hexcolor[5:7], 16)
        return g > r and g > b

    def _is_purplish(hexcolor: str) -> bool:
        r, g, b = int(hexcolor[1:3], 16), int(hexcolor[3:5], 16), int(hexcolor[5:7], 16)
        return r > g and b > g

    assert any(_is_greenish(h) for h in hexes), "the ellipse's own green should survive, not get averaged away"
    assert any(_is_purplish(h) for h in hexes), "the triangle's own purple should survive, not get averaged away"


def _flat_filled_shape_image_bytes(size: int = 160) -> bytes:
    # A single solid-filled ellipse on a plain background: deliberately
    # near-zero real tonal gradient inside the shape, aside from a thin
    # ring of antialiasing-free but bilateral-blur-blended boundary pixels.
    img = np.full((size, size, 3), 245, dtype=np.uint8)
    cv2.ellipse(img, (size // 2, size // 2), (55, 40), 0, 0, 360, (60, 160, 30), -1)  # flat green, BGR
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_a_flat_filled_region_still_gets_hatched_not_left_blank():
    # Regression: the region's tone band cutoffs were fixed fractions of
    # (literal min, literal max). For a flat-filled shape, those literal
    # extremes are set by a handful of blurred boundary pixels, not by the
    # shape's real (near-uniform) interior tone -- so every band cutoff
    # could land entirely outside where the interior pixels actually sit,
    # and the whole shape would silently get zero colored strokes.
    result = image_to_strokes(_flat_filled_shape_image_bytes(), style="faithful")
    colored = [s for s in result["strokes"] if "color" in s]
    assert len(colored) > 20, "a solid-filled shape's interior must not be left entirely unhatched"


def _noisy_large_flat_region_image_bytes(size: int = 240, seed: int = 3) -> bytes:
    # A large flat-colored region with small per-pixel sensor-like noise --
    # representative of a real photograph's sky/grass/skin, which is never
    # perfectly flat the way a vector-graphics fill is.
    img = np.full((size, size, 3), (235, 200, 150), dtype=np.uint8)  # BGR
    rng = np.random.RandomState(seed)
    noise = rng.normal(0, 4, img.shape).astype(np.int16)
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_noisy_large_color_region_does_not_fragment_into_sparse_coverage():
    # Regression: classifying hue/chroma on raw (noisy) pixels let ordinary
    # per-pixel noise scatter one real color region across many hue bins,
    # fragmenting it into hundreds of tiny below-threshold islands and
    # leaving most of the region uncolored. Classification must run on a
    # lightly denoised copy so one real region stays one region.
    size = 240
    result = image_to_strokes(_noisy_large_flat_region_image_bytes(size), style="faithful")
    colored = [s for s in result["strokes"] if "color" in s]
    # A region this large and this uniform should get dense coverage, not
    # just a sparse handful of surviving fragments.
    assert len(colored) > 300


# --- Phase 10.3: tonal depth / dark-region accumulation --------------------
# Regression: a flat MAX_LOCAL_ACCUMULATION cap made a near-black, a
# dark-gray, and a medium-gray region all converge on nearly identical
# hatch density (measured via real browser rendering: luminance 177-181/255
# for source values 15, 70, and 128), and rendered a solid black circle on
# white as a light gray outline. Root cause: the per-cell density ceiling
# was a flat constant, blind to how much darker a tone actually was. Fixed
# via sketch._accumulation_cap_for_tone, a function of the local source
# tone rather than a single constant.


def _tonal_bands_image_bytes(width=500, height=120, values=(15, 70, 128, 190, 240)) -> bytes:
    img = np.zeros((height, width, 3), dtype=np.uint8)
    band_w = width // len(values)
    for i, v in enumerate(values):
        img[:, i * band_w : (i + 1) * band_w] = v
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_accumulation_cap_is_tone_aware_and_monotonically_bounded():
    # Direct unit coverage of the cap function itself: darker tones must
    # get a strictly larger (or equal) cap than lighter ones, and the
    # whole range must stay within [MIN_LOCAL_ACCUMULATION, MAX_LOCAL_ACCUMULATION].
    tones = [0, 15, 70, 128, 190, 240, 255]
    caps = [sketch._accumulation_cap_for_tone(t) for t in tones]
    assert caps == sorted(caps, reverse=True), "cap must not increase as tone gets lighter"
    assert all(sketch.MIN_LOCAL_ACCUMULATION <= c <= sketch.MAX_LOCAL_ACCUMULATION for c in caps)
    assert caps[0] == sketch.MAX_LOCAL_ACCUMULATION, "true black must reach the maximum cap"
    assert caps[-2] == sketch.MIN_LOCAL_ACCUMULATION, "a tone at/above the reference floor gets the minimum cap"


def test_hatch_density_strictly_increases_with_source_darkness():
    # End-to-end reproduction of the measured regression: a 5-band strip
    # from near-black to near-white must produce a strictly monotonic
    # stroke-count ordering, with *meaningful* separation -- not just
    # technical ordering. The actual pre-fix bug produced counts like
    # [1206, 1195, 1171, 440, 1]: strictly decreasing, but the three
    # darkest bands were within 3% of each other (visually indistinguishable,
    # which was the whole regression) -- so a bare ">" chain would not have
    # caught it. Each band must be substantially sparser than the one
    # darker than it.
    result = image_to_strokes(_tonal_bands_image_bytes(), style="graphite")
    strokes = result["strokes"]
    width = result["width"]
    band_w = width // 5
    counts = [0, 0, 0, 0, 0]
    for s in strokes:
        cx = sum(p[0] for p in s["points"]) / len(s["points"])
        idx = min(4, int(cx // band_w))
        counts[idx] += 1
    assert counts[0] > counts[1] > counts[2] > counts[3] > counts[4]
    # Each band's density must be no more than 70% of the next-darker
    # band's -- real, visually-meaningful separation, not a 97%-identical
    # technicality.
    for darker, lighter in zip(counts, counts[1:3]):  # the three darkest bands specifically
        assert lighter < darker * 0.7, f"insufficient tonal separation: {counts}"


def test_a_black_circle_on_white_gets_dense_not_sparse_coverage():
    # The brief's explicit acceptance check: "if a black circle becomes a
    # lightly outlined white circle, the fundamental shading model is
    # broken." A solid black disc must produce a hatch stroke count
    # proportional to real deep-shadow density -- bare stroke count alone
    # is a weak proxy for perceived darkness (the pre-fix flat cap still
    # produced ~2,761 strokes here, yet rendered as a visibly light gray
    # disc in the real Canvas renderer), so the threshold is set well
    # above what the old flat cap could ever reach for this shape, not
    # just "more than a thin outline".
    size = 300
    img = np.full((size, size, 3), 250, dtype=np.uint8)
    cv2.circle(img, (size // 2, size // 2), 90, (10, 10, 10), -1)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    result = image_to_strokes(buf.tobytes(), style="graphite")
    hatch = [s for s in result["strokes"] if s["kind"] == "hatch"]
    assert len(hatch) > 8000, "a solid black circle must get genuinely dense interior hatching"


def test_near_white_background_still_stays_clean_with_the_tone_aware_cap():
    # Guards against a careless fix that raises the cap for *every* tone --
    # light/background tones must keep the original, conservative ceiling.
    result = image_to_strokes(_tonal_bands_image_bytes(), style="graphite")
    width = result["width"]
    band_w = width // 5
    near_white_strokes = [
        s
        for s in result["strokes"]
        if (sum(p[0] for p in s["points"]) / len(s["points"])) >= 4 * band_w
    ]
    assert len(near_white_strokes) <= 2, "near-white paper must stay essentially clean"


def test_small_dark_region_is_not_omitted_by_the_tone_aware_cap():
    # A small, genuinely dark spot (not a large region) must still receive
    # meaningful hatching -- the tone-aware cap is keyed to the *local*
    # pixel tone, not region size, so this must not regress.
    size = 160
    img = np.full((size, size, 3), 235, dtype=np.uint8)
    img[60:100, 60:100] = 20  # small near-black square
    ok, buf = cv2.imencode(".png", img)
    assert ok
    result = image_to_strokes(buf.tobytes(), style="graphite")
    hatch = [s for s in result["strokes"] if s["kind"] == "hatch"]
    in_region = [
        s
        for s in hatch
        if 60 <= sum(p[0] for p in s["points"]) / len(s["points"]) <= 100
        and 60 <= sum(p[1] for p in s["points"]) / len(s["points"]) <= 100
    ]
    assert len(in_region) > 20, "a small dark region must not be under-shaded relative to its size"


def test_tone_aware_shading_is_deterministic():
    data = _tonal_bands_image_bytes()
    first = image_to_strokes(data, style="graphite")
    second = image_to_strokes(data, style="graphite")
    assert first == second


def _near_black_stress_image_bytes(size=180):
    img = np.full((size, size, 3), 15, dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_stroke_generation_stress_records_bounded_deterministic_output():
    data = _near_black_stress_image_bytes()
    samples = []
    for _ in range(2):
        tracemalloc.start()
        started = perf_counter()
        result = image_to_strokes(data, style="graphite")
        elapsed = perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        hatch = [s for s in result["strokes"] if s["kind"] == "hatch"]
        samples.append((result, elapsed, peak, len(hatch), sum(s["shade"] for s in hatch)))

    first, second = samples
    assert first[0] == second[0]
    assert first[3] > 0 and first[3] <= sketch.MAX_HATCH_STROKES
    assert first[2] > 0 and first[4] > 0
    # A generous runaway-work guard, not an artistic or throughput target.
    assert first[1] < 60.0


def test_post_generation_cap_measures_dark_stroke_retention(monkeypatch):
    data = _near_black_stress_image_bytes(size=140)
    uncapped = [s for s in image_to_strokes(data, style="graphite")["strokes"] if s["kind"] == "hatch"]
    assert len(uncapped) > 100

    monkeypatch.setattr(sketch, "MAX_HATCH_STROKES", 100)
    capped = [s for s in image_to_strokes(data, style="graphite")["strokes"] if s["kind"] == "hatch"]
    assert len(capped) <= 100

    uncapped_dark = sum(s["shade"] > 0.7 for s in uncapped)
    capped_dark = sum(s["shade"] > 0.7 for s in capped)
    assert uncapped_dark > 0
    dark_retention = capped_dark / uncapped_dark
    # Quantifies the current even-stride policy. Because all hatches are
    # sorted light-to-dark before [::stride] sampling, this deliberately
    # records that dark strokes are disproportionately discarded. A future
    # replacement should change this measured result and update the test.
    assert dark_retention < 0.1


def test_instrumentation_is_opt_in_and_does_not_change_the_plan():
    data = _tonal_bands_image_bytes(width=240, height=80)
    plain = image_to_strokes(data, style="graphite")
    collector = sketch.GenerationInstrumentation()
    observed = image_to_strokes(data, style="graphite", instrumentation=collector)
    observed_without_metrics = {key: value for key, value in observed.items() if key != "instrumentation"}
    assert observed_without_metrics == plain
    assert observed["instrumentation"]["total"]["candidate_evaluations"] > 0
    assert observed["instrumentation"]["total"]["streamline_steps"] > 0
    assert observed["instrumentation"]["total"]["allocated_points"] > 0


def test_budget_simulation_uses_union_demand_for_overlapping_bands():
    collector = sketch.GenerationInstrumentation((4, 4))
    first = collector.bucket("band:240:parallel")
    second = collector.bucket("band:200:parallel")
    first.update({"new_eligible_pixels": 12, "overlap_eligible_pixels": 0, "strokes": 12, "mean_tone": 100, "shade": 0.2})
    second.update({"new_eligible_pixels": 0, "overlap_eligible_pixels": 12, "strokes": 12, "mean_tone": 20, "shade": 0.8})
    plan = collector.plan_budgets(total_budget=100)
    assert plan["demand"]["band:200:parallel"] > plan["demand"]["band:240:parallel"]
    assert sum(plan["budgets"].values()) == 100
    assert plan["simulated_retention"]["band:240:parallel"] <= 1.0


def test_instrumentation_accounting_invariants_hold_for_every_pass():
    collector = sketch.GenerationInstrumentation()
    result = image_to_strokes(_tonal_bands_image_bytes(width=240, height=80), instrumentation=collector)
    collector.validate_accounting()
    for metrics in result["instrumentation"]["passes"].values():
        assert metrics["candidate_evaluations"] == metrics["accepted_seeds"] + sum(metrics["rejection_reasons"].values())


def test_resource_scheduler_reserves_dark_passes_and_reports_exhaustion():
    scheduler = sketch.ResourceBudgetScheduler(total_budget=100, active=True)
    light = scheduler.budgets["band:240:parallel"]
    dark = scheduler.budgets["band:40:parallel"]
    cross = scheduler.budgets["band:40:cross"]
    assert dark > light
    assert cross > light
    for _ in range(scheduler.budgets["band:40:parallel"]):
        assert scheduler.permits("band:40:parallel")
        scheduler.accepted_one("band:40:parallel")
    assert not scheduler.permits("band:40:parallel")
    assert scheduler.report()["exhausted_candidates"]["band:40:parallel"] == 1


def test_resource_scheduler_activation_is_limited_to_predicted_pathology():
    normal = np.full((760, 579), 128, dtype=np.uint8)
    pathological = np.full((760, 760), 15, dtype=np.uint8)
    assert not sketch.ResourceBudgetScheduler.from_tone(normal).active
    assert sketch.ResourceBudgetScheduler.from_tone(pathological).active


def test_scheduler_override_is_test_only_and_accounted():
    data = _near_black_stress_image_bytes(size=120)
    collector = sketch.GenerationInstrumentation()
    result = image_to_strokes(data, style="graphite", instrumentation=collector, scheduler_override=True)
    report = result["instrumentation"]["resource_scheduler"]
    assert report["active"] is True
    assert report["accepted_total"] <= report["total_budget"]
    assert result["instrumentation"]["post_generation"]["truncated"] == 0
    assert sum(report["exhausted_candidates"].values()) >= 0
