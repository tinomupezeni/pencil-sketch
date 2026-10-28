import test from "node:test";
import assert from "node:assert/strict";
import { States, canTransition, nextState } from "../js/playback-state.js";

test("full happy-path lifecycle", () => {
  let s = States.IDLE;
  s = nextState(s, "LOAD_START");
  assert.equal(s, States.LOADING);
  s = nextState(s, "LOAD_SUCCESS");
  assert.equal(s, States.READY);
  s = nextState(s, "START");
  assert.equal(s, States.PLAYING);
  s = nextState(s, "PAUSE");
  assert.equal(s, States.PAUSED);
  s = nextState(s, "RESUME");
  assert.equal(s, States.PLAYING);
  s = nextState(s, "COMPLETE");
  assert.equal(s, States.COMPLETED);
});

test("START restarts (replay) from ready, playing, paused, and completed", () => {
  for (const s of [States.READY, States.PLAYING, States.PAUSED, States.COMPLETED]) {
    assert.equal(nextState(s, "START"), States.PLAYING);
  }
});

test("PAUSE only valid while playing", () => {
  assert.ok(canTransition(States.PLAYING, "PAUSE"));
  assert.ok(!canTransition(States.PAUSED, "PAUSE"));
  assert.ok(!canTransition(States.READY, "PAUSE"));
});

test("invalid transitions throw rather than silently doing nothing", () => {
  assert.throws(() => nextState(States.IDLE, "PLAY"));
  assert.throws(() => nextState(States.COMPLETED, "PAUSE"));
});

test("LOAD_START is reachable from every state (uploading a new image anytime)", () => {
  for (const s of Object.values(States)) {
    assert.ok(canTransition(s, "LOAD_START"), `expected LOAD_START from ${s}`);
  }
});
