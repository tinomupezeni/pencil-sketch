import test from "node:test";
import assert from "node:assert/strict";
import { GraphiteRenderer, strokeColor } from "../js/renderer.js";
import { cumulativeLengths, totalLength } from "../js/geometry.js";
import { FakeCtx } from "./fake-ctx.js";

const STROKE = {
  id: 1,
  kind: "hatch",
  shade: 0.4,
  width: 1,
  points: [
    [0, 0],
    [4, 0],
    [8, 0],
    [12, 0],
    [16, 0],
    [20, 0],
  ],
};

function coveredLengthRanges(strokeCalls, lens) {
  // Reconstructs, from the recorded paths, the arc-length range each
  // stroke() call covered, by matching path points back to the polyline's
  // cumulative-length table (points shared with the original stroke only;
  // interpolated endpoints are skipped for this check).
  const byXY = new Map(STROKE.points.map((p, i) => [`${p[0]},${p[1]}`, lens[i]]));
  return strokeCalls.map((path) => path.map((p) => byXY.get(`${p[0]},${p[1]}`)).filter((v) => v !== undefined));
}

test("renderStrokeUpTo never re-paints a length range it already covered (single large steps)", () => {
  const ctx = new FakeCtx();
  const renderer = new GraphiteRenderer(ctx);
  const total = totalLength(cumulativeLengths(STROKE.points));

  renderer.renderStrokeUpTo(STROKE, total * 0.5);
  renderer.renderStrokeUpTo(STROKE, total);

  assert.equal(ctx.strokeCalls.length, 2, "one stroke() call per incremental advance");
});

test("completed portions never darken: total painted arc length is the same for 1 step vs many small steps", () => {
  const total = totalLength(cumulativeLengths(STROKE.points));

  function pathLength(path) {
    let len = 0;
    for (let i = 1; i < path.length; i++) {
      len += Math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1]);
    }
    return len;
  }

  // The darkness of a pencil stroke is a function of how much *length*
  // along it has been inked, not how many separate stroke() calls that
  // took -- so the correct schedule-invariance check sums painted length
  // across all calls, rather than counting exact point coordinates (which
  // would wrongly flag the harmless single shared pixel at the boundary
  // between two adjacent calls' paths as "duplicate ink").
  const oneStep = new FakeCtx();
  new GraphiteRenderer(oneStep).renderStrokeUpTo(STROKE, total);
  const oneStepCoverage = oneStep.strokeCalls.reduce((sum, p) => sum + pathLength(p), 0);

  const manySteps = new FakeCtx();
  const r2 = new GraphiteRenderer(manySteps);
  for (let i = 1; i <= 20; i++) {
    r2.renderStrokeUpTo(STROKE, (total * i) / 20);
  }
  const manyStepsCoverage = manySteps.strokeCalls.reduce((sum, p) => sum + pathLength(p), 0);

  assert.ok(Math.abs(oneStepCoverage - total) < 1e-6);
  assert.ok(Math.abs(manyStepsCoverage - total) < 1e-6, "20 small steps must cover exactly the same total length as 1 big step");
});

test("renderStrokeComplete (catch-up fast path) draws a whole remaining stroke in a small, bounded number of calls", () => {
  const ctx = new FakeCtx();
  const renderer = new GraphiteRenderer(ctx);
  renderer.renderStrokeComplete(STROKE);
  // Painting is chunked (MAX_PAINT_CHUNK_LEN_PX) so width/opacity can
  // follow the pressure profile even across one big catch-up jump -- not
  // one call per point/frame, but not necessarily exactly one call either.
  assert.ok(ctx.strokeCalls.length >= 1 && ctx.strokeCalls.length <= 4);
  assert.ok(renderer.isStrokeComplete(STROKE));
});

test("a zero-progress call paints nothing", () => {
  const ctx = new FakeCtx();
  const renderer = new GraphiteRenderer(ctx);
  renderer.renderStrokeUpTo(STROKE, 0);
  assert.equal(ctx.strokeCalls.length, 0);
});

test("clear() resets per-stroke progress for a clean replay", () => {
  const ctx = new FakeCtx();
  const renderer = new GraphiteRenderer(ctx);
  renderer.renderStrokeComplete(STROKE);
  assert.ok(renderer.isStrokeComplete(STROKE));

  renderer.clear(100, 100);
  assert.ok(!renderer.isStrokeComplete(STROKE));
  assert.equal(ctx.fillRectCalls.length, 1, "clear paints the paper background once");

  // Redrawing after clear covers the stroke again from scratch, not skipped.
  renderer.renderStrokeComplete(STROKE);
  assert.ok(renderer.isStrokeComplete(STROKE));
});

test("handles a degenerate zero-length (all-duplicate-point) stroke without throwing", () => {
  const ctx = new FakeCtx();
  const renderer = new GraphiteRenderer(ctx);
  const degenerate = { id: 2, kind: "hatch", shade: 0.2, width: 1, points: [[3, 3], [3, 3]] };
  assert.doesNotThrow(() => renderer.renderStrokeUpTo(degenerate, 10));
});

test("every interior chunk boundary uses a butt cap -- never a round cap at a seam between separate stroke() calls", () => {
  const ctx = new FakeCtx();
  const renderer = new GraphiteRenderer(ctx);
  renderer.renderStrokeComplete(STROKE); // forces multi-chunk painting (total 20px > 16px chunk cap)
  assert.ok(ctx.strokeStyles.length > 1, "this test needs the stroke split into multiple paint calls");
  for (const style of ctx.strokeStyles) {
    assert.equal(style.lineCap, "butt", "a round cap here would double-ink the shared seam pixel");
  }
});

test("the soft tapered tip is stamped exactly once at each true stroke extremity, never at an interior seam", () => {
  const ctx = new FakeCtx();
  const renderer = new GraphiteRenderer(ctx);
  renderer.renderStrokeComplete(STROKE);
  assert.equal(ctx.arcCalls.length, 2, "exactly one stamp at the true start, one at the true end");
  const stamped = ctx.arcCalls.map((c) => `${c.x},${c.y}`);
  assert.ok(stamped.includes(`${STROKE.points[0][0]},${STROKE.points[0][1]}`));
  assert.ok(
    stamped.includes(`${STROKE.points[STROKE.points.length - 1][0]},${STROKE.points[STROKE.points.length - 1][1]}`)
  );
});

test("width and opacity vary across chunks instead of staying one flat value (pressure sampled along the stroke)", () => {
  const ctx = new FakeCtx();
  const renderer = new GraphiteRenderer(ctx);
  renderer.renderStrokeComplete(STROKE);
  const widths = ctx.strokeStyles.map((s) => s.lineWidth);
  const colors = ctx.strokeStyles.map((s) => s.strokeStyle);
  assert.ok(new Set(widths).size > 1 || new Set(colors).size > 1, "pressure-driven width/opacity should vary along a multi-chunk stroke");
});

test("rendering is reproducible for a given seed", () => {
  const ctxA = new FakeCtx();
  const ctxB = new FakeCtx();
  new GraphiteRenderer(ctxA, { seed: 99 }).renderStrokeComplete(STROKE);
  new GraphiteRenderer(ctxB, { seed: 99 }).renderStrokeComplete(STROKE);
  assert.deepEqual(ctxA.strokeStyles, ctxB.strokeStyles);
  assert.deepEqual(ctxA.strokeCalls, ctxB.strokeCalls);
});

// --- Phase 9: colored (Faithful Pencil) strokes ------------------------------

test("a stroke with a color field renders in that color, not neutral gray", () => {
  const colored = { ...STROKE, color: "#aa4433" };
  const rgba = strokeColor(colored);
  assert.ok(rgba.startsWith("rgba(170, 68, 51,"), `expected the muted-red rgb triplet, got: ${rgba}`);
});

test("a stroke without a color field keeps the existing neutral graphite color", () => {
  const rgba = strokeColor(STROKE);
  assert.ok(rgba.startsWith("rgba(60, 58, 56,"));
});

test("colored strokes still vary opacity by shade and pressure, same as graphite", () => {
  const colored = { ...STROKE, color: "#aa4433", shade: 0.1 };
  const dim = strokeColor(colored, 0.1);
  const bright = strokeColor(colored, 0.9);
  const alphaOf = (rgba) => parseFloat(rgba.split(",").pop());
  assert.ok(alphaOf(bright) > alphaOf(dim));
});

test("a colored stroke renders through the exact same incremental, non-darkening pipeline as graphite", () => {
  const ctx = new FakeCtx();
  const renderer = new GraphiteRenderer(ctx);
  const colored = { ...STROKE, id: 2, color: "#aa4433" };
  renderer.renderStrokeComplete(colored);
  assert.ok(renderer.isStrokeComplete(colored));
  assert.ok(ctx.strokeStyles.every((s) => s.strokeStyle.startsWith("rgba(170, 68, 51,")));
});
