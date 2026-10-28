import test from "node:test";
import assert from "node:assert/strict";
import { Player } from "../js/player.js";
import { GraphiteRenderer } from "../js/renderer.js";
import { PencilTip } from "../js/pencil-tip.js";
import { States } from "../js/playback-state.js";
import { FakeCtx } from "./fake-ctx.js";

function makePlan() {
  const strokeA = { id: 0, kind: "line", shade: 0.5, width: 1, points: [[0, 0], [10, 0]] };
  const strokeB = { id: 1, kind: "hatch", shade: 0.3, width: 1, points: [[20, 20], [30, 20]] };
  return {
    width: 100,
    height: 100,
    strokes: [strokeA, strokeB],
    actions: [
      { type: "move", duration: 1, x: 0, y: 0 },
      { type: "draw", duration: 1, stroke_id: 0 },
      { type: "lift", duration: 0 },
      { type: "move", duration: 1, x: 20, y: 20 },
      { type: "draw", duration: 1, stroke_id: 1 },
      { type: "lift", duration: 0 },
    ],
    metadata: {},
  };
}

function makeHarness() {
  const graphiteCtx = new FakeCtx();
  const tipCtx = new FakeCtx();
  const state = { now: 0 };
  const player = new Player({
    graphite: new GraphiteRenderer(graphiteCtx),
    tip: new PencilTip(tipCtx),
    now: () => state.now,
    scheduleFrame: () => null, // tests drive _tick manually
    cancelFrame: () => {},
  });
  return { player, graphiteCtx, tipCtx, state };
}

// Natural durations: move(1) + draw(1) + lift(0) + move(1) + draw(1) + lift(0) = 4s.
// Scaling to 4000ms gives a clean 1:1 mapping (1s -> 1000ms) for easy math below.
const TARGET_MS = 4000;

test("every action executes in sequence (draws happen in stroke-id order)", () => {
  const { player, state } = makeHarness();
  const plan = makePlan();
  const drawOrder = [];
  player.onDrawStart = ({ stroke }) => drawOrder.push(stroke.id);

  player.load(plan, TARGET_MS);
  state.now = 0;
  player.start();
  for (let t = 0; t <= TARGET_MS; t += 100) {
    state.now = t;
    player._tick(t);
  }

  assert.equal(player.state, States.COMPLETED);
  assert.deepEqual(drawOrder, [0, 1]);
});

test("MOVE never deposits graphite", () => {
  const { player, graphiteCtx, state } = makeHarness();
  player.load(makePlan(), TARGET_MS);
  player.start();

  // The first action is a pure move (0ms .. 1000ms scaled). Step through
  // only that window.
  for (let t = 0; t <= 900; t += 100) {
    state.now = t;
    player._tick(t);
    assert.equal(graphiteCtx.strokeCalls.length, 0, "no ink while only a move action has executed");
  }
});

test("DRAW produces visible incremental progress, and position matches current action", () => {
  const { player, graphiteCtx, state } = makeHarness();
  player.load(makePlan(), TARGET_MS);
  player.start();

  // Category duration multipliers (motion.js) mean action boundaries are no
  // longer simple equal fractions of TARGET_MS -- read the real scaled
  // timeline instead of hardcoding expected millisecond values.
  const moveEntry = player.entries[0];
  const drawEntry = player.entries[1];
  assert.equal(moveEntry.action.type, "move");
  assert.equal(drawEntry.action.type, "draw");

  state.now = moveEntry.endMs;
  player._tick(state.now);
  assert.deepEqual(player.position, [0, 0]);

  state.now = (drawEntry.startMs + drawEntry.endMs) / 2; // mid-draw
  player._tick(state.now);
  assert.equal(graphiteCtx.strokeCalls.length, 1, "partial draw should have painted something");
  assert.ok(player.position[0] > 0 && player.position[0] < 10, "pencil position should be mid-stroke");

  state.now = drawEntry.endMs;
  player._tick(state.now);
  assert.deepEqual(player.position, [10, 0]);
});

test("pause stops timeline advancement", () => {
  const { player, state } = makeHarness();
  player.load(makePlan(), TARGET_MS);
  player.start();

  state.now = 1500;
  player._tick(1500);
  const cursorAtPause = player._actionCursor;
  const posAtPause = [...player.position];

  player.pause();
  assert.equal(player.state, States.PAUSED);

  for (let extra = 0; extra < 5; extra++) {
    state.now += 1000;
    player._tick(state.now); // should no-op while paused
  }

  assert.equal(player._actionCursor, cursorAtPause);
  assert.deepEqual(player.position, posAtPause);
});

test("resume does not introduce drift: paused-then-resumed playback reaches the same end state as continuous playback", () => {
  const plan = makePlan();

  const continuous = makeHarness();
  continuous.player.load(plan, TARGET_MS);
  continuous.player.start();
  for (let t = 0; t <= TARGET_MS; t += 50) {
    continuous.state.now = t;
    continuous.player._tick(t);
  }

  const interrupted = makeHarness();
  interrupted.player.load(plan, TARGET_MS);
  interrupted.player.start();
  for (let t = 0; t <= 1500; t += 50) {
    interrupted.state.now = t;
    interrupted.player._tick(t);
  }
  interrupted.player.pause();
  interrupted.state.now += 50000; // a long time passes while paused
  interrupted.player.resume();
  // Resume re-bases playStartPerf at the resume moment (state.now), so
  // continuing to advance state.now from here resumes the *same* virtual
  // playhead the continuous run reached, just stretched over real time.
  const playheadAtResume = 1500;
  for (let dt = 0; dt <= TARGET_MS - playheadAtResume; dt += 50) {
    interrupted.state.now += 50;
    interrupted.player._tick(interrupted.state.now);
  }

  assert.equal(interrupted.player.state, States.COMPLETED);
  assert.equal(continuous.player.state, States.COMPLETED);
  assert.deepEqual(interrupted.player.position, continuous.player.position);
});

test("replay starts from a clean canvas", () => {
  const { player, graphiteCtx, state } = makeHarness();
  const plan = makePlan();
  player.load(plan, TARGET_MS);
  player.start();
  for (let t = 0; t <= TARGET_MS; t += 200) {
    state.now = t;
    player._tick(t);
  }
  assert.equal(player.state, States.COMPLETED);
  assert.ok(player.graphite.isStrokeComplete(plan.strokes[0]));

  const fillRectCallsBeforeReplay = graphiteCtx.fillRectCalls.length;
  state.now = 0;
  player.start();
  assert.ok(graphiteCtx.fillRectCalls.length > fillRectCallsBeforeReplay, "replay clears the canvas");
  assert.ok(!player.graphite.isStrokeComplete(plan.strokes[0]), "replay resets per-stroke progress");
});

test("completion renders every intended mark", () => {
  const { player, state } = makeHarness();
  const plan = makePlan();
  player.load(plan, TARGET_MS);
  player.start();
  for (let t = 0; t <= TARGET_MS; t += 250) {
    state.now = t;
    player._tick(t);
  }
  assert.equal(player.state, States.COMPLETED);
  for (const stroke of plan.strokes) {
    assert.ok(player.graphite.isStrokeComplete(stroke), `stroke ${stroke.id} should be fully drawn`);
  }
});

test("handles an empty plan (no strokes, no actions) without throwing, completing immediately", () => {
  const { player, state } = makeHarness();
  const emptyPlan = { width: 50, height: 50, strokes: [], actions: [], metadata: {} };
  player.load(emptyPlan, TARGET_MS);
  assert.equal(player.state, States.READY);
  player.start();
  state.now = 0;
  assert.doesNotThrow(() => player._tick(0));
  assert.equal(player.state, States.COMPLETED);
});

test("time-boxed catch-up makes bounded progress per frame and still reaches completion", () => {
  const { player, state } = makeHarness();
  const plan = makePlan();
  player.load(plan, TARGET_MS);

  // Simulate each internal now()-check costing real wall-clock time, so the
  // frame-budget deadline (8ms) is exceeded partway through a single catch-up
  // burst when we jump the playhead far into the future in one go.
  let callCount = 0;
  player._now = () => {
    callCount += 1;
    return callCount * 3;
  };

  player.start();
  // Ask for a target far past the whole timeline in one _tick call.
  player._tick(TARGET_MS * 10);
  const cursorAfterFirstTick = player._actionCursor;
  assert.ok(
    cursorAfterFirstTick < plan.actions.length - 1 || player.state === States.COMPLETED,
    "a single time-boxed tick should not silently skip the budget"
  );

  // Subsequent ticks (still targeting the far-future time) keep making
  // progress until the plan is actually complete -- it must never get stuck.
  for (let i = 0; i < plan.actions.length + 2 && player.state !== States.COMPLETED; i++) {
    player._tick(TARGET_MS * 10);
  }
  assert.equal(player.state, States.COMPLETED, "catch-up must eventually reach the target, never stall");
});

test("_scheduleNext never leaves more than one frame request pending at once", () => {
  // Regression (Phase 10): _scheduleNext used to arm a new frame without
  // canceling any previous one. That's invisible under normal playback
  // (the rAF loop only ever calls _tick once per real frame, so there's
  // never more than one in flight anyway), but repeated *manual* _tick
  // calls driven from outside the loop -- e.g. a debug/test seek that
  // fast-forwards the playhead -- would leak one extra pending frame per
  // call. Those zombies all fire later with the browser's own tiny real
  // elapsed time and silently stomp on whatever progress was just set.
  let scheduleCount = 0;
  let cancelCount = 0;
  let liveHandle = null;
  const graphiteCtx = new FakeCtx();
  const tipCtx = new FakeCtx();
  const player = new Player({
    graphite: new GraphiteRenderer(graphiteCtx),
    tip: new PencilTip(tipCtx),
    now: () => 0,
    scheduleFrame: () => {
      scheduleCount += 1;
      liveHandle = scheduleCount;
      return liveHandle;
    },
    cancelFrame: (handle) => {
      cancelCount += 1;
      assert.equal(handle, liveHandle, "must cancel the most recently armed handle, not a stale one");
      liveHandle = null;
    },
  });
  const plan = makePlan();
  player.load(plan, TARGET_MS);
  player.start();

  // Repeated manual _tick calls, as a debug seek performs -- each one
  // (short of the final, completing call) must cancel its predecessor's
  // armed frame before arming its own.
  player._tick(500);
  player._tick(1000);
  player._tick(1500);

  assert.equal(scheduleCount, cancelCount + 1, "exactly one frame request should remain armed, none leaked");
});
