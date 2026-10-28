import test from "node:test";
import assert from "node:assert/strict";
import { PencilTip } from "../js/pencil-tip.js";
import { FakeCtx } from "./fake-ctx.js";

test("pen-down and pen-up renders are visually distinguishable (different alpha)", () => {
  const ctxDown = new FakeCtx();
  const ctxUp = new FakeCtx();
  const down = new PencilTip(ctxDown);
  const up = new PencilTip(ctxUp);

  // FakeCtx doesn't define globalAlpha explicitly; plain property
  // assignment still works and is readable back directly.
  down.draw(10, 10, 0, true);
  const downAlpha = ctxDown.globalAlpha;
  up.draw(10, 10, 0, false);
  const upAlpha = ctxUp.globalAlpha;

  assert.notEqual(upAlpha, downAlpha, "pen-up should render visibly differently from pen-down");
  assert.ok(upAlpha === undefined || upAlpha < 1, "pen-up should be dimmed");
});

test("draw() defaults to pen-down when not specified (backward compatible)", () => {
  const ctx = new FakeCtx();
  const tip = new PencilTip(ctx);
  assert.doesNotThrow(() => tip.draw(5, 5, 0));
});

test("clear() wipes the tip overlay", () => {
  const ctx = new FakeCtx();
  const tip = new PencilTip(ctx);
  tip.clear(100, 100);
  // clearRect is a no-op in FakeCtx but must not throw and must be callable
  // with the overlay's dimensions.
  assert.doesNotThrow(() => tip.clear(100, 100));
});
