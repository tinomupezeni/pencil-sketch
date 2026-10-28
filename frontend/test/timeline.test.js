import test from "node:test";
import assert from "node:assert/strict";
import { buildTimeline, findActionIndex } from "../js/timeline.js";

test("buildTimeline scales total duration to exactly the requested target", () => {
  const actions = [{ duration: 1 }, { duration: 2 }, { duration: 1 }];
  const { entries, totalMs } = buildTimeline(actions, 60000); // 1 minute
  assert.ok(Math.abs(totalMs - 60000) < 1e-6);
  assert.equal(entries.length, 3);
  assert.equal(entries[0].startMs, 0);
  assert.equal(entries[entries.length - 1].endMs, totalMs);
});

test("buildTimeline preserves relative proportions between actions", () => {
  const actions = [{ duration: 1 }, { duration: 2 }];
  const { entries } = buildTimeline(actions, 30000);
  const d0 = entries[0].endMs - entries[0].startMs;
  const d1 = entries[1].endMs - entries[1].startMs;
  assert.ok(Math.abs(d1 / d0 - 2) < 1e-9);
});

test("buildTimeline is progression-by-time, not by count: a zero-duration action occupies zero timeline width", () => {
  const actions = [{ duration: 0 }, { duration: 1 }];
  const { entries } = buildTimeline(actions, 10000);
  assert.equal(entries[0].startMs, entries[0].endMs);
  assert.equal(entries[1].endMs, 10000);
});

test("buildTimeline handles an all-zero-duration plan without dividing by zero", () => {
  const actions = [{ duration: 0 }, { duration: 0 }];
  const { entries, totalMs } = buildTimeline(actions, 60000);
  assert.equal(totalMs, 0);
  assert.ok(entries.every((e) => e.startMs === 0 && e.endMs === 0));
});

test("buildTimeline handles an empty action list", () => {
  const { entries, totalMs } = buildTimeline([], 60000);
  assert.deepEqual(entries, []);
  assert.equal(totalMs, 0);
});

test("findActionIndex finds the entry covering a given time", () => {
  const { entries } = buildTimeline(
    [{ duration: 1 }, { duration: 1 }, { duration: 1 }],
    30000
  );
  assert.equal(findActionIndex(entries, 0), 0);
  assert.equal(findActionIndex(entries, 10000), 1);
  assert.equal(findActionIndex(entries, 29999), 2);
});

test("findActionIndex returns -1 for an empty timeline", () => {
  assert.equal(findActionIndex([], 500), -1);
});

test("buildTimeline never drops actions regardless of the requested target duration", () => {
  // Phase 10.1: duration must only change playback pacing, never which
  // actions (and therefore which strokes) exist. A 2-minute time-lapse
  // and a 4-hour session must schedule the exact same action count.
  const actions = Array.from({ length: 50 }, (_, i) => ({ duration: 0.05 + (i % 5) * 0.02 }));
  const durationsMs = [120000, 900000, 1800000, 3600000, 4 * 3600000];
  for (const targetMs of durationsMs) {
    const { entries, totalMs } = buildTimeline(actions, targetMs);
    assert.equal(entries.length, actions.length, `entry count changed at targetMs=${targetMs}`);
    assert.ok(Math.abs(totalMs - targetMs) < 1e-6);
  }
});
