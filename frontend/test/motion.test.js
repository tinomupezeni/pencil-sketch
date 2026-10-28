import test from "node:test";
import assert from "node:assert/strict";
import { ease, durationMultiplierFor } from "../js/motion.js";

const CATEGORIES = ["travel", "construction", "contour", "hatch", "crosshatch", "fine_detail"];

test("every known easing anchors at 0 and 1 (no robotic jump, full range covered)", () => {
  for (const c of CATEGORIES) {
    assert.ok(Math.abs(ease(c, 0) - 0) < 1e-9, `${c} ease(0) should be 0`);
    assert.ok(Math.abs(ease(c, 1) - 1) < 1e-9, `${c} ease(1) should be 1`);
  }
});

test("easing is monotonic (progress never reverses)", () => {
  for (const c of CATEGORIES) {
    let prev = -Infinity;
    for (let u = 0; u <= 1.0001; u += 0.05) {
      const v = ease(c, u);
      assert.ok(v >= prev - 1e-9, `${c} eased progress must not go backwards`);
      prev = v;
    }
  }
});

test("constant-velocity (linear) progress is NOT what most profiles produce off-center", () => {
  // A genuinely robotic/constant-velocity profile has ease(u) === u
  // everywhere. Symmetric ease-in-out curves still cross exactly through
  // the midpoint by construction, so check a quarter-point instead, where
  // acceleration/deceleration shape is unambiguous.
  assert.notEqual(ease("contour", 0.25), 0.25);
  assert.notEqual(ease("hatch", 0.5), 0.5);
});

test("ease() clamps out-of-range input instead of extrapolating", () => {
  for (const c of CATEGORIES) {
    assert.equal(ease(c, -1), ease(c, 0));
    assert.equal(ease(c, 2), ease(c, 1));
  }
});

test("unknown category falls back to linear rather than throwing", () => {
  assert.equal(ease("nonexistent-category", 0.5), 0.5);
});

test("duration multipliers differentiate careful contours from quick flicks", () => {
  assert.ok(durationMultiplierFor("contour") > durationMultiplierFor("hatch"));
  assert.ok(durationMultiplierFor("fine_detail") > durationMultiplierFor("crosshatch"));
});

test("construction gestures are the fastest category (a loose warm-up mark)", () => {
  for (const c of ["contour", "hatch", "crosshatch", "fine_detail"]) {
    assert.ok(durationMultiplierFor("construction") < durationMultiplierFor(c));
  }
});

test("unknown category gets a neutral (1x) duration multiplier", () => {
  assert.equal(durationMultiplierFor("nonexistent-category"), 1);
});
