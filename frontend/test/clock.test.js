import test from "node:test";
import assert from "node:assert/strict";
import { computePlayheadMs } from "../js/clock.js";
import { States } from "../js/playback-state.js";

test("playhead advances with wall-clock time while playing", () => {
  const t = computePlayheadMs({
    state: States.PLAYING,
    pausedAccumMs: 1000,
    playStartPerf: 500,
    nowPerf: 800,
  });
  assert.equal(t, 1300); // 1000 + (800 - 500)
});

test("playhead is frozen while paused, regardless of nowPerf", () => {
  const a = computePlayheadMs({ state: States.PAUSED, pausedAccumMs: 4200, playStartPerf: 0, nowPerf: 0 });
  const b = computePlayheadMs({ state: States.PAUSED, pausedAccumMs: 4200, playStartPerf: 0, nowPerf: 999999 });
  assert.equal(a, 4200);
  assert.equal(b, 4200);
});

test("resume introduces no drift: playhead continues exactly where it paused", () => {
  // Play from perf=0 to perf=100 -> playhead should read 100.
  const atPause = computePlayheadMs({ state: States.PLAYING, pausedAccumMs: 0, playStartPerf: 0, nowPerf: 100 });
  assert.equal(atPause, 100);

  // Pause freezes it at 100 (pausedAccumMs becomes 100).
  // Resume at perf=500 (some real time passed while paused -- must not count).
  const justAfterResume = computePlayheadMs({
    state: States.PLAYING,
    pausedAccumMs: atPause,
    playStartPerf: 500,
    nowPerf: 500,
  });
  assert.equal(justAfterResume, 100, "no jump at the instant of resume");

  const later = computePlayheadMs({
    state: States.PLAYING,
    pausedAccumMs: atPause,
    playStartPerf: 500,
    nowPerf: 560,
  });
  assert.equal(later, 160, "continues from the paused value, not from 0 or from perf time directly");
});
