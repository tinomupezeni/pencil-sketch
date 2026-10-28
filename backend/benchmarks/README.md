# Atelier artistic benchmark suite (Phase 10.4)

A reproducible, visually-approved baseline for Atelier's drawing output
-- built so a future engine change can be checked against what we've
already achieved, instead of silently drifting.

## Layout

- `images/` -- 6 source images, each exercising a different artistic
  demand (see below). Committed, deterministic, never regenerated.
- `fixtures/<name>.json` -- the automated baseline: stroke counts by
  kind, payload size, processing time, duration-planning metadata, and
  (once `compute_rendered_metrics.py` has run) rendered-screenshot
  luminance/darkness/background-cleanliness/color-presence numbers.
- `screenshots/<name>-<pct>.png` -- real Canvas renders (not the crude
  Python preview) at 25/50/75/100% progress, captured via Playwright.
  These are what a human looks at to give artistic approval.
- `generate_metrics.py` -- regenerates `fixtures/*.json`'s backend-only
  numbers (no browser needed).
- `compute_rendered_metrics.py` -- measures `screenshots/*-100pct.png`
  and merges the results into the matching fixture.
- (not committed, scripted ad hoc) a Playwright capture script drives
  the real browser to produce `screenshots/*.png` -- see "Regenerating
  screenshots" below.

## The 6 benchmark images

| Name | Tests |
|---|---|
| `colored_skull` | The project's primary benchmark: deep tonal shading + Faithful Pencil color preservation (red hatband/tie/cross) |
| `full_color_portrait` | Skin-tone/hair/clothing color extraction on a synthetic full-color portrait |
| `landscape` | Large, varied-color flat regions (sky/hills/ground), non-white background |
| `geometric_object` | Simple, distinctly-colored shapes -- composition/coverage with no photographic texture |
| `low_contrast_photo` | A narrow, muted tonal range -- specifically added to check the Phase 10.3 accumulation-cap fix doesn't over-darken subtle images (see "Known findings" below) |
| `near_white_illustration` | Mostly blank page, a few sparse marks -- background cleanliness and "don't over-shade near-nothing" |

## What's automated vs. what needs a human (Phase 10.4, task 9)

**Automated (`test_benchmarks.py`, part of the normal `pytest` run):**
regenerates each image's plan right now and compares stroke counts (by
kind), payload size, color-accent presence, and determinism against the
saved fixture within a generous tolerance. This catches *drift* -- a
category silently disappearing, a count swinging by an order of
magnitude -- not whether the output looks artistically good. A test can
pass with ugly output, and a human-meaningful improvement can fail it
(expected: update the fixture, per below).

**Needs a human (`screenshots/*.png` + the benchmark report):** does the
skull's fedora actually look deeply shaded? Do colors read as muted
colored pencil, not a flat paste? Is a background actually clean, not
just numerically light? These are genuinely subjective calls; the
automated suite deliberately does not try to answer them. The "Visual
approval status" table in the benchmark report
(`reports/ATELIER-2026-10-28-phase-10.4-benchmark-report.md` in the
`SOFTWARE-ENGINEER` dev-log repo) records what still needs a person to
look at it.

## Regenerating screenshots

There's no committed Playwright dependency (consistent with this
project's existing pattern of using Playwright only as an ad hoc
verification tool, never a `package.json` dependency). To recapture:

1. Start the dev server: `cd backend && .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8787`
2. Use `playwright-core` (already available in this environment) to
   drive `/usr/bin/google-chrome`: upload each `images/<file>`, select
   any duration (content is duration-independent since Phase 10.1), seek
   to 0.25/0.5/0.75/1.0 via `window.__debugSeekToFraction` (a test-only
   hook in `frontend/main.js`), and screenshot the `#canvas` element
   specifically (not the full page) so the fixture stays tightly cropped
   to just the drawing.
3. Run `generate_metrics.py` then `compute_rendered_metrics.py` to
   refresh the JSON fixtures.

## Updating a fixture after an intended change

1. Make the engine change.
2. Regenerate screenshots (above) and **look at them** -- this is the
   artistic-approval step, not optional.
3. Only once a human has approved the new screenshots: run
   `generate_metrics.py` and `compute_rendered_metrics.py`, commit the
   new fixtures *and* the new screenshots together, and note in the
   commit message what changed and why the new output was approved.
4. Never update a fixture to make a failing `test_benchmarks.py` pass
   without doing step 2 first -- that defeats the entire point of this
   suite.

## Known findings (Phase 10.4 investigation)

- **The `colored_skull` benchmark's processing time rose from ~2.2s
  (pre-Phase-10.3) to ~10s** after the tone-aware accumulation cap fix
  (`MAX_LOCAL_ACCUMULATION = 60`). This is a measured, real cost of
  deliberately allowing genuinely dark regions (hat, eye sockets) far
  more overlapping strokes -- confirmed via a direct comparison (cap
  forced back to the old flat value of 10 reproduces the original ~2.7s),
  not incidental overhead. **Not yet resolved one way or the other** --
  flagged for a human decision: is ~10s acceptable for a one-time image
  upload in a single-user, local-first app, or does it need a tighter
  resource budget (e.g. lowering `MAX_HATCH_STROKES` as a payload
  backstop, which bounds the *output* but not the *generation time*, or
  reducing the cap itself, which would partially undo the Phase 10.3
  tonal fix)? See the benchmark report's "Still requires manual
  decision" section.
- **`low_contrast_photo` initially over-shaded**: a linear
  darkness-to-cap mapping gave an ordinary photographic midtone (~90,
  nowhere near black) a cap around 34, rendering a deliberately subtle,
  low-contrast source as a dense, nearly solid dark mass. Root-caused and
  fixed by squaring the darkness fraction in `_accumulation_cap_for_tone`
  (`backend/sketch.py`) -- moderate midtones now get a much more modest
  cap (~21 at tone 90, ~13 at tone 128) while true near-black tones keep
  nearly the same large cap as before (~52 at tone 15 vs. ~56
  previously). Note this image was already fairly dense under the
  *original* flat cap of 10 (12,941 hatch strokes) due to the pre-existing
  10-band hatch table naturally layering many bands across a broad
  gradient -- the cap fix added roughly 35% more strokes on top of that
  pre-existing characteristic, not the whole effect.
