import test from "node:test";
import assert from "node:assert/strict";
import { WakeLockController } from "../js/wake-lock.js";

function makeFakeSentinel() {
  const listeners = {};
  return {
    released: false,
    addEventListener(type, fn) {
      listeners[type] = fn;
    },
    async release() {
      this.released = true;
      listeners.release?.();
    },
  };
}

test("unsupported browsers fail gracefully instead of throwing", async () => {
  const controller = new WakeLockController({ navigatorRef: {} });
  assert.equal(controller.isSupported, false);
  const result = await controller.request();
  assert.deepEqual(result, { ok: false, reason: "unsupported" });
  assert.equal(controller.isActive, false);
});

test("a successful request becomes active", async () => {
  const sentinel = makeFakeSentinel();
  const navigatorRef = { wakeLock: { request: async () => sentinel } };
  const controller = new WakeLockController({ navigatorRef });
  const result = await controller.request();
  assert.deepEqual(result, { ok: true });
  assert.equal(controller.isActive, true);
});

test("a rejected request resolves cleanly, never throws", async () => {
  const navigatorRef = {
    wakeLock: {
      request: async () => {
        const err = new Error("denied");
        err.name = "NotAllowedError";
        throw err;
      },
    },
  };
  const controller = new WakeLockController({ navigatorRef });
  const result = await controller.request();
  assert.equal(result.ok, false);
  assert.equal(result.reason, "NotAllowedError");
  assert.equal(controller.isActive, false);
});

test("release() clears the active lock", async () => {
  const sentinel = makeFakeSentinel();
  const navigatorRef = { wakeLock: { request: async () => sentinel } };
  const controller = new WakeLockController({ navigatorRef });
  await controller.request();
  await controller.release();
  assert.equal(controller.isActive, false);
  assert.equal(sentinel.released, true);
});

test("reacquireIfWanted does nothing after an explicit release", async () => {
  let requestCount = 0;
  const navigatorRef = {
    wakeLock: {
      request: async () => {
        requestCount += 1;
        return makeFakeSentinel();
      },
    },
  };
  const controller = new WakeLockController({ navigatorRef });
  await controller.request();
  await controller.release();
  await controller.reacquireIfWanted();
  assert.equal(requestCount, 1, "release() means no further auto-reacquire");
});

test("reacquireIfWanted re-requests after the OS released it out from under us", async () => {
  let requestCount = 0;
  const navigatorRef = {
    wakeLock: {
      request: async () => {
        requestCount += 1;
        return makeFakeSentinel();
      },
    },
  };
  const controller = new WakeLockController({ navigatorRef });
  const firstResult = await controller.request();
  assert.equal(firstResult.ok, true);
  // Simulate the browser revoking it on its own (tab hidden, etc).
  await controller._sentinel.release();
  assert.equal(controller.isActive, false);

  await controller.reacquireIfWanted();
  assert.equal(requestCount, 2);
  assert.equal(controller.isActive, true);
});
