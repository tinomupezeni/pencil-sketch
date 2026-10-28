import test from "node:test";
import assert from "node:assert/strict";
import { isRecoverable, ageDescription, buildSessionRecord, MAX_SESSION_AGE_MS } from "../js/session-policy.js";

function validPlan() {
  return { strokes: [{ id: 0 }], actions: [{ type: "draw", stroke_id: 0, duration: 1 }] };
}

test("rejects missing or malformed records", () => {
  assert.equal(isRecoverable(null, 1000), false);
  assert.equal(isRecoverable(undefined, 1000), false);
  assert.equal(isRecoverable({}, 1000), false);
  assert.equal(isRecoverable({ savedAt: "not a number" }, 1000), false);
});

test("accepts a well-formed, fresh record", () => {
  const record = buildSessionRecord({ plan: validPlan(), elapsedMs: 5000, targetDurationMs: 60000, nowMs: 1000 });
  assert.equal(isRecoverable(record, 2000), true);
});

test("rejects a record older than the bounded retention window", () => {
  const record = buildSessionRecord({ plan: validPlan(), elapsedMs: 5000, targetDurationMs: 60000, nowMs: 0 });
  assert.equal(isRecoverable(record, MAX_SESSION_AGE_MS + 1), false);
  assert.equal(isRecoverable(record, MAX_SESSION_AGE_MS - 1), true);
});

test("rejects a record with no usable plan", () => {
  const record = buildSessionRecord({ plan: { strokes: [] }, elapsedMs: 0, targetDurationMs: 60000, nowMs: 0 });
  assert.equal(isRecoverable(record, 0), false, "missing actions array");
});

test("rejects corrupted/clock-skewed timestamps", () => {
  const record = buildSessionRecord({ plan: validPlan(), elapsedMs: 0, targetDurationMs: 60000, nowMs: 5000 });
  assert.equal(isRecoverable(record, 1000), false, "savedAt in the future relative to now");
});

test("rejects a non-positive target duration", () => {
  const record = buildSessionRecord({ plan: validPlan(), elapsedMs: 0, targetDurationMs: 0, nowMs: 0 });
  assert.equal(isRecoverable(record, 0), false);
});

test("ageDescription gives a short human phrase", () => {
  const record = buildSessionRecord({ plan: validPlan(), elapsedMs: 0, targetDurationMs: 1, nowMs: 0 });
  assert.equal(ageDescription(record, 20_000), "moments ago");
  assert.equal(ageDescription(record, 5 * 60_000), "5 minutes ago");
  assert.equal(ageDescription(record, 3 * 60 * 60_000), "3 hours ago");
});
