// Phase 6, section 3: verifies short/medium/long playback targets --
// including the two extremes the UI actually exposes (2 minutes, 4 hours)
// -- using simulated timeline math instead of waiting for real playback.
import test from "node:test";
import assert from "node:assert/strict";
import { Player } from "../js/player.js";
import { GraphiteRenderer } from "../js/renderer.js";
import { PencilTip } from "../js/pencil-tip.js";
import { States } from "../js/playback-state.js";
import { FakeCtx } from "./fake-ctx.js";

const TWO_MINUTES_MS = 2 * 60 * 1000;
const FOUR_HOURS_MS = 4 * 60 * 60 * 1000;
const THIRTY_MINUTES_MS = 30 * 60 * 1000;

function makePlan() {
  const strokes = [
    { id: 0, kind: "line", shade: 0.5, width: 1, points: [[0, 0], [20, 0]] },
    { id: 1, kind: "hatch", shade: 0.3, width: 0.8, points: [[30, 30], [40, 30]] },
  ];
  const actions = [
    { type: "move", duration: 0.2, x: 0, y: 0 },
    { type: "draw", duration: 1, stroke_id: 0 },
    { type: "lift", duration: 0 },
    { type: "move", duration: 0.2, x: 30, y: 30 },
    { type: "draw", duration: 0.5, stroke_id: 1 },
    { type: "lift", duration: 0 },
  ];
  return { width: 100, height: 100, strokes, actions, metadata: {} };
}

function makeHarness() {
  const state = { now: 0 };
  const player = new Player({
    graphite: new GraphiteRenderer(new FakeCtx()),
    tip: new PencilTip(new FakeCtx()),
    now: () => state.now,
    scheduleFrame: () => null,
    cancelFrame: () => {},
  });
  return { player, state };
}

for (const [label, targetMs] of [
  ["2 minutes", TWO_MINUTES_MS],
  ["30 minutes", THIRTY_MINUTES_MS],
  ["4 hours", FOUR_HOURS_MS],
]) {
  test(`playback completes at exactly the requested endpoint (${label})`, () => {
    const { player, state } = makeHarness();
    player.load(makePlan(), targetMs);

    // totalMs should match the requested target (within floating-point
    // rounding), regardless of how extreme the target is.
    assert.ok(Math.abs(player.totalMs - targetMs) < 1, `totalMs should equal the ${label} target`);

    player.start();
    // Jump straight to (just past) the end -- this is exactly the
    // "simulated timeline" approach: no real waiting, no sleep.
    state.now = player.totalMs + 1000;
    player._tick(state.now);
    assert.equal(player.state, States.COMPLETED, `${label} target should reach COMPLETED`);
  });

  test(`action timing is internally consistent at the ${label} target (durations sum to totalMs)`, () => {
    const { player } = makeHarness();
    player.load(makePlan(), targetMs);
    const sum = player.entries.reduce((s, e) => s + (e.endMs - e.startMs), 0);
    assert.ok(Math.abs(sum - player.totalMs) < 1e-6);
  });
}

test("frame-rate independence: completion state is identical whether driven by many small ticks or one big jump", () => {
  const fine = makeHarness();
  fine.player.load(makePlan(), TWO_MINUTES_MS);
  fine.player.start();
  for (let t = 0; t <= TWO_MINUTES_MS + 1000; t += 16) {
    fine.state.now = t;
    fine.player._tick(t);
    if (fine.player.state === States.COMPLETED) break;
  }

  const coarse = makeHarness();
  coarse.player.load(makePlan(), TWO_MINUTES_MS);
  coarse.player.start();
  coarse.state.now = TWO_MINUTES_MS + 1000;
  coarse.player._tick(coarse.state.now);

  assert.equal(fine.player.state, States.COMPLETED);
  assert.equal(coarse.player.state, States.COMPLETED);
  assert.deepEqual(fine.player.position, coarse.player.position);
});

test("pause/resume and background-tab recovery hold at a real-world (4 hour) target", () => {
  const { player, state } = makeHarness();
  player.load(makePlan(), FOUR_HOURS_MS);
  player.start();

  state.now = 1000; // a few seconds in
  player._tick(state.now);
  player.pause();
  const cursorAtPause = player._actionCursor;

  // Simulate the tab being backgrounded for a long stretch (far longer
  // than any single rAF gap) -- the paused playhead must not move.
  state.now += 60 * 60 * 1000; // +1 hour of real wall-clock time, while paused
  assert.equal(player._actionCursor, cursorAtPause);

  player.resume();
  state.now += 1; // resume "now"
  player._tick(state.now);
  // Then simulate catching up from a long background gap post-resume.
  state.now += FOUR_HOURS_MS;
  player._tick(state.now);
  assert.equal(player.state, States.COMPLETED, "a long background gap must still reach completion, not stall");
});

test("replay after completion resets cleanly at any target duration", () => {
  const { player, state } = makeHarness();
  const plan = makePlan();
  player.load(plan, TWO_MINUTES_MS);
  player.start();
  state.now = TWO_MINUTES_MS + 1000;
  player._tick(state.now);
  assert.equal(player.state, States.COMPLETED);

  state.now = 0;
  player.start(); // replay
  assert.equal(player.state, States.PLAYING);
  assert.ok(!player.graphite.isStrokeComplete(plan.strokes[0]));
});

test("start({resumeAtMs}) seeds the playhead for session recovery and catches up on the next tick", () => {
  const { player, state } = makeHarness();
  const plan = makePlan();
  player.load(plan, TWO_MINUTES_MS);

  const resumePoint = TWO_MINUTES_MS * 0.6;
  state.now = 0;
  player.start({ resumeAtMs: resumePoint });
  // Resuming doesn't instantly fast-forward the canvas before any tick --
  // the first _tick call is what catches it up (same mechanism as a long
  // background gap), so right after start() the canvas is still clean.
  assert.ok(!player.graphite.isStrokeComplete(plan.strokes[0]));

  state.now = 1; // first tick, barely any wall-clock time has passed
  player._tick(state.now);
  // The playhead should already be near resumePoint, not near 0.
  const elapsed = player._pausedAccumMs + (state.now - player._playStartPerf);
  assert.ok(Math.abs(elapsed - resumePoint) < 50);
});

test("start({resumeAtMs}) clamps to a valid range (never negative, never past totalMs)", () => {
  const { player, state } = makeHarness();
  player.load(makePlan(), TWO_MINUTES_MS);

  state.now = 0;
  player.start({ resumeAtMs: -500 });
  assert.equal(player._pausedAccumMs, 0);

  state.now = 0;
  player.start({ resumeAtMs: TWO_MINUTES_MS * 10 });
  assert.equal(player._pausedAccumMs, player.totalMs);
});
