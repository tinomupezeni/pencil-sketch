import test from "node:test";
import assert from "node:assert/strict";
import { injectEvaluationPauses } from "../js/eval-pauses.js";

function stroke(id, stage, region_id) {
  return { id, stage, region_id, kind: "hatch", shade: 0.3, width: 1, points: [[0, 0], [1, 1]] };
}

test("does not pause before every small stroke (most strokes get no extra pause)", () => {
  // Many strokes, same stage, same region -- nothing to evaluate.
  const strokes = Array.from({ length: 200 }, (_, i) => stroke(i, 3, "region_c0_r0"));
  const actions = strokes.map((s) => ({ type: "draw", duration: 0.1, stroke_id: s.id }));
  const result = injectEvaluationPauses(actions, strokes, { seed: 1 });
  const pauseCount = result.filter((a) => a.type === "pause").length;
  assert.equal(pauseCount, 0, "same stage + same region should never trigger an evaluation pause");
});

test("entering the structural-contour stage (stage 2) always gets a pause", () => {
  const strokes = [stroke(0, 3, "region_c0_r0"), stroke(1, 2, "region_c0_r0")];
  const actions = [
    { type: "draw", duration: 0.1, stroke_id: 0 },
    { type: "draw", duration: 0.1, stroke_id: 1 },
  ];
  const result = injectEvaluationPauses(actions, strokes, { seed: 1 });
  const idxOfSecondDraw = result.findIndex((a) => a.type === "draw" && a.stroke_id === 1);
  assert.equal(result[idxOfSecondDraw - 1].type, "pause");
});

test("entering the final stage present (not stage 6, since it's unused) gets a pause", () => {
  // Stages present: 3 and 5 -- 5 is "final" here since stage 6 has no strokes.
  const strokes = [stroke(0, 3, "region_c0_r0"), stroke(1, 5, "region_c0_r0")];
  const actions = [
    { type: "draw", duration: 0.1, stroke_id: 0 },
    { type: "draw", duration: 0.1, stroke_id: 1 },
  ];
  const result = injectEvaluationPauses(actions, strokes, { seed: 1 });
  const idxOfSecondDraw = result.findIndex((a) => a.type === "draw" && a.stroke_id === 1);
  assert.equal(result[idxOfSecondDraw - 1].type, "pause");
});

test("region switch within the same stage pauses only sometimes (probability-gated)", () => {
  const strokes = Array.from({ length: 40 }, (_, i) =>
    stroke(i, 3, i % 2 === 0 ? "region_c0_r0" : "region_c1_r0")
  );
  const actions = strokes.map((s) => ({ type: "draw", duration: 0.1, stroke_id: s.id }));
  const result = injectEvaluationPauses(actions, strokes, { seed: 1, regionSwitchProbability: 0.35 });
  const pauseCount = result.filter((a) => a.type === "pause").length;
  assert.ok(pauseCount > 0, "with 39 region switches at p=0.35, at least one pause is expected");
  assert.ok(pauseCount < strokes.length, "not every region switch should pause");
});

test("is deterministic for a given seed", () => {
  const strokes = Array.from({ length: 30 }, (_, i) =>
    stroke(i, 3, i % 3 === 0 ? "region_c0_r0" : "region_c1_r0")
  );
  const actions = strokes.map((s) => ({ type: "draw", duration: 0.1, stroke_id: s.id }));
  const a = injectEvaluationPauses(actions, strokes, { seed: 42 });
  const b = injectEvaluationPauses(actions, strokes, { seed: 42 });
  assert.deepEqual(a, b);
});

test("never alters, drops, or duplicates the original draw actions", () => {
  const strokes = Array.from({ length: 15 }, (_, i) => stroke(i, (i % 3) + 2, `region_c${i % 4}_r0`));
  const actions = strokes.map((s) => ({ type: "draw", duration: 0.1, stroke_id: s.id }));
  const result = injectEvaluationPauses(actions, strokes, { seed: 5 });
  const drawIds = result.filter((a) => a.type === "draw").map((a) => a.stroke_id);
  assert.deepEqual(drawIds, strokes.map((s) => s.id));
});

test("handles an empty action list", () => {
  assert.deepEqual(injectEvaluationPauses([], []), []);
});
