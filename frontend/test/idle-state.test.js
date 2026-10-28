import test from "node:test";
import assert from "node:assert/strict";
import { createIdleState, recordActivity, setFocusWithinControls, shouldShowControls } from "../js/idle-state.js";

test("controls are visible immediately after activity", () => {
  const state = createIdleState({ idleTimeoutMs: 1000 });
  recordActivity(state, 1000);
  assert.equal(shouldShowControls(state, 1000), true);
  assert.equal(shouldShowControls(state, 1500), true);
});

test("controls hide after the idle timeout elapses", () => {
  const state = createIdleState({ idleTimeoutMs: 1000 });
  recordActivity(state, 1000);
  assert.equal(shouldShowControls(state, 2001), false);
});

test("new activity resets the idle clock", () => {
  const state = createIdleState({ idleTimeoutMs: 1000 });
  recordActivity(state, 1000);
  recordActivity(state, 1900);
  assert.equal(shouldShowControls(state, 2500), true, "activity at 1900 should keep it visible past 2000");
});

test("controls never hide while keyboard focus is within them, regardless of idle time", () => {
  const state = createIdleState({ idleTimeoutMs: 1000 });
  recordActivity(state, 0);
  setFocusWithinControls(state, true);
  assert.equal(shouldShowControls(state, 1_000_000), true);
});

test("losing focus re-applies the normal idle timeout", () => {
  const state = createIdleState({ idleTimeoutMs: 1000 });
  recordActivity(state, 0);
  setFocusWithinControls(state, true);
  setFocusWithinControls(state, false);
  assert.equal(shouldShowControls(state, 1_000_000), false);
});
