import test from "node:test";
import assert from "node:assert/strict";
import { containDimensions } from "../js/fit.js";

test("contain sizing fits a portrait artwork within the workspace", () => {
  const fitted = containDimensions(600, 900, 1200, 700);
  assert.equal(fitted.width, 466.6666666666667);
  assert.equal(fitted.height, 700);
  assert.ok(Math.abs(fitted.width / fitted.height - 600 / 900) < 1e-12);
});

test("contain sizing allows proportional upscaling beyond a small intrinsic display size", () => {
  const fitted = containDimensions(236, 420, 926, 774);
  assert.ok(fitted.width > 236);
  assert.ok(fitted.height > 420);
  assert.ok(Math.abs(fitted.width / fitted.height - 236 / 420) < 1e-12);
});

test("contain sizing fits a landscape artwork within the workspace", () => {
  const fitted = containDimensions(1600, 900, 1000, 700);
  assert.equal(fitted.width, 1000);
  assert.equal(fitted.height, 562.5);
  assert.ok(Math.abs(fitted.width / fitted.height - 1600 / 900) < 1e-12);
});

test("invalid dimensions produce an empty fit", () => {
  assert.deepEqual(containDimensions(0, 900, 1000, 700), { width: 0, height: 0, scale: 0 });
});
