import test from "node:test";
import assert from "node:assert/strict";
import { createSessionStore } from "../js/session-store.js";
import { createFakeIDBFactory } from "./fake-idb.js";

function validRecord(nowMs) {
  return {
    plan: { strokes: [{ id: 0 }], actions: [{ type: "draw", stroke_id: 0, duration: 1 }] },
    elapsedMs: 1000,
    targetDurationMs: 60000,
    savedAt: nowMs,
  };
}

test("round-trips a saved session", async () => {
  const store = createSessionStore({ idbFactory: createFakeIDBFactory() });
  const record = validRecord(1000);
  const saveResult = await store.save(record);
  assert.equal(saveResult.ok, true);

  const loaded = await store.loadIfRecoverable(2000);
  assert.deepEqual(loaded, record);
});

test("saving a new session overwrites the previous one (bounded to one record)", async () => {
  const store = createSessionStore({ idbFactory: createFakeIDBFactory() });
  await store.save(validRecord(1000));
  const second = validRecord(2000);
  await store.save(second);
  const loaded = await store.loadIfRecoverable(3000);
  assert.deepEqual(loaded, second);
});

test("discard() removes the session, so nothing is recoverable afterward", async () => {
  const store = createSessionStore({ idbFactory: createFakeIDBFactory() });
  await store.save(validRecord(1000));
  await store.discard();
  const loaded = await store.loadIfRecoverable(2000);
  assert.equal(loaded, null);
});

test("an unreasonably old session is not offered for recovery", async () => {
  const store = createSessionStore({ idbFactory: createFakeIDBFactory() });
  await store.save(validRecord(0));
  const loaded = await store.loadIfRecoverable(999 * 24 * 60 * 60 * 1000);
  assert.equal(loaded, null);
});

test("no session saved yet returns null, not an error", async () => {
  const store = createSessionStore({ idbFactory: createFakeIDBFactory() });
  const loaded = await store.loadIfRecoverable(1000);
  assert.equal(loaded, null);
});

test("an unavailable IndexedDB degrades to 'no recovery available' instead of throwing", async () => {
  const store = createSessionStore({ idbFactory: null });
  await assert.doesNotReject(async () => {
    const saveResult = await store.save(validRecord(0));
    assert.equal(saveResult.ok, false);
    const loaded = await store.loadIfRecoverable(1000);
    assert.equal(loaded, null);
  });
});

test("a failing open() degrades gracefully", async () => {
  const store = createSessionStore({ idbFactory: createFakeIDBFactory({ failOpen: true }) });
  const saveResult = await store.save(validRecord(0));
  assert.equal(saveResult.ok, false);
});
