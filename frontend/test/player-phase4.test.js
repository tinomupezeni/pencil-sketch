import test from "node:test";
import assert from "node:assert/strict";
import { Player } from "../js/player.js";
import { GraphiteRenderer } from "../js/renderer.js";
import { PencilTip } from "../js/pencil-tip.js";
import { States } from "../js/playback-state.js";
import { FakeCtx } from "./fake-ctx.js";

function longPoints(n, dx = 5) {
  return Array.from({ length: n }, (_, i) => [i * dx, 0]);
}

function makeRichPlan() {
  // A contour (line) and several hatch strokes spread across two regions
  // and two stages, so category-based pacing and evaluation pauses both
  // have something to act on.
  const strokes = [
    { id: 0, kind: "line", shade: 0.5, width: 1.2, points: longPoints(10), stage: 2, region_id: "region_c0_r0" },
    { id: 1, kind: "hatch", shade: 0.2, width: 0.8, points: longPoints(5), stage: 3, region_id: "region_c0_r0" },
    { id: 2, kind: "hatch", shade: 0.6, width: 0.9, points: longPoints(6), stage: 5, region_id: "region_c1_r0" },
  ];
  const actions = [
    { type: "move", duration: 0.2, x: strokes[0].points[0][0], y: strokes[0].points[0][1] },
    { type: "draw", duration: 1, stroke_id: 0 },
    { type: "lift", duration: 0 },
    { type: "pause", duration: 0.4 },
    { type: "move", duration: 0.2, x: strokes[1].points[0][0], y: strokes[1].points[0][1] },
    { type: "draw", duration: 0.5, stroke_id: 1 },
    { type: "lift", duration: 0 },
    { type: "move", duration: 0.2, x: strokes[2].points[0][0], y: strokes[2].points[0][1] },
    { type: "draw", duration: 0.5, stroke_id: 2 },
    { type: "lift", duration: 0 },
  ];
  return { width: 200, height: 200, strokes, actions, metadata: {} };
}

function makePlayer(seed) {
  const graphiteCtx = new FakeCtx();
  const tipCtx = new FakeCtx();
  const state = { now: 0 };
  const player = new Player({
    graphite: new GraphiteRenderer(graphiteCtx, { seed }),
    tip: new PencilTip(tipCtx),
    seed,
    now: () => state.now,
    scheduleFrame: () => null,
    cancelFrame: () => {},
  });
  return { player, graphiteCtx, state };
}

function runToCompletion(player, state, totalMs, step = 25) {
  for (let t = 0; t <= totalMs + step; t += step) {
    state.now = t;
    player._tick(t);
    if (player.state === States.COMPLETED) break;
  }
}

test("evaluation pauses are actually wired into the timeline (stage-2 entry adds a pause)", () => {
  const { player } = makePlayer(1);
  player.load(makeRichPlan(), 60000);
  const pauseCount = player.entries.filter((e) => e.action.type === "pause").length;
  // The plan already has one backend-style pause; entering stage 2 (the
  // structural-contour stage) for stroke 0 should not add one here since
  // it's the very first stroke (no previous stage to transition from), but
  // the region switch from stroke 1 (region_c0_r0) to stroke 2
  // (region_c1_r0) combined with its stage switch (3 -> 5, the final
  // stage) must add an evaluation pause.
  assert.ok(pauseCount >= 2, `expected at least the original pause plus an injected one, got ${pauseCount}`);
});

test("category duration multipliers change relative pacing: the contour draw is weighted differently than hatch draws", () => {
  const { player } = makePlayer(1);
  player.load(makeRichPlan(), 60000);
  const drawEntries = player.entries.filter((e) => e.action.type === "draw");
  const contourEntry = drawEntries.find((e) => e.action.stroke_id === 0);
  const hatchEntry = drawEntries.find((e) => e.action.stroke_id === 1);
  const contourMsPerNaturalSec = (contourEntry.endMs - contourEntry.startMs) / 1; // natural duration was 1s
  const hatchMsPerNaturalSec = (hatchEntry.endMs - hatchEntry.startMs) / 0.5; // natural duration was 0.5s
  assert.ok(
    contourMsPerNaturalSec !== hatchMsPerNaturalSec,
    "contour and hatch categories must not be paced identically per natural second"
  );
});

test("same plan + same seed produces byte-identical timelines and render calls (reproducible)", () => {
  const a = makePlayer(123);
  const b = makePlayer(123);
  a.player.load(makeRichPlan(), 20000);
  b.player.load(makeRichPlan(), 20000);

  assert.deepEqual(
    a.player.entries.map((e) => ({ type: e.action.type, startMs: e.startMs, endMs: e.endMs })),
    b.player.entries.map((e) => ({ type: e.action.type, startMs: e.startMs, endMs: e.endMs }))
  );

  a.player.start();
  b.player.start();
  runToCompletion(a.player, a.state, a.player.totalMs);
  runToCompletion(b.player, b.state, b.player.totalMs);

  assert.deepEqual(a.graphiteCtx.strokeCalls, b.graphiteCtx.strokeCalls);
  assert.deepEqual(a.graphiteCtx.strokeStyles, b.graphiteCtx.strokeStyles);
  assert.deepEqual(a.player.position, b.player.position);
});

test("different seeds can produce different (but each internally valid) timelines", () => {
  const a = makePlayer(1);
  const b = makePlayer(2);
  a.player.load(makeRichPlan(), 20000);
  b.player.load(makeRichPlan(), 20000);
  // Not asserting they MUST differ (jitter is small and could coincide by
  // chance on a tiny plan), only that both are internally consistent and
  // complete correctly -- the reproducibility guarantee is per-seed, not a
  // promise that any two seeds diverge.
  a.player.start();
  b.player.start();
  runToCompletion(a.player, a.state, a.player.totalMs);
  runToCompletion(b.player, b.state, b.player.totalMs);
  assert.equal(a.player.state, States.COMPLETED);
  assert.equal(b.player.state, States.COMPLETED);
});

test("pen-up travel uses a curved (non-straight-line) path when the move distance allows it", () => {
  const { player, state } = makePlayer(1);
  const plan = makeRichPlan();
  player.load(plan, 60000);
  player.start();

  const moveEntry = player.entries.find((e) => e.action.type === "move" && e.action.stroke_id == null);
  // Step to the midpoint of the first move action and confirm the pencil
  // is not exactly on the straight line between its start and end (unless
  // the seeded bow happened to round to ~0, which we tolerate but prefer
  // not to see across every run -- so this checks at least one of several
  // early moves bows away from the straight line).
  let anyOffLine = false;
  for (const entry of player.entries.filter((e) => e.action.type === "move")) {
    state.now = (entry.startMs + entry.endMs) / 2;
    player._tick(state.now);
    const [x, y] = player.position;
    const [sx, sy] = player._moveStartPos || [0, 0];
    const [ex, ey] = [entry.action.x, entry.action.y];
    const dx = ex - sx;
    const dy = ey - sy;
    const lineLen = Math.hypot(dx, dy);
    if (lineLen < 1e-6) continue;
    // perpendicular distance from the straight line start->end
    const cross = Math.abs(dx * (y - sy) - dy * (x - sx)) / lineLen;
    if (cross > 0.01) anyOffLine = true;
  }
  assert.ok(anyOffLine, "expected at least one move to bow off the straight line");
});

test("currentStage tracks the real plan data and onPhaseChange fires only on actual transitions", () => {
  const { player, state } = makePlayer(1);
  const plan = makeRichPlan(); // strokes 0 (stage 2), 1 (stage 3), 2 (stage 5)
  const phaseChanges = [];
  player.onPhaseChange = (stage) => phaseChanges.push(stage);
  player.load(plan, 60000);
  assert.equal(player.currentStage, null, "no stage until a draw action has executed");

  player.start();
  for (let t = 0; t <= player.totalMs + 100; t += 50) {
    state.now = t;
    player._tick(t);
  }

  assert.deepEqual(phaseChanges, [2, 3, 5], "should see each distinct stage exactly once, in order");
  assert.equal(player.currentStage, 5);
});
