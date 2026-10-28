import test from "node:test";
import assert from "node:assert/strict";
import { Category } from "../js/stroke-category.js";
import { pressureAt, widthScaleFor, alphaScaleFor, NEUTRAL_PRESSURE } from "../js/pressure.js";

test("pressure is evaluated along the stroke, not a single constant", () => {
  // Construction is the deliberate exception: a flat, low, unvarying
  // pressure for its whole lifetime is exactly the "stays faint, never
  // erased" design (see backend planner.py's synthesize_construction_strokes).
  for (const c of Object.values(Category).filter((cat) => cat !== Category.CONSTRUCTION)) {
    const samples = [0, 0.25, 0.5, 0.75, 1].map((t) => pressureAt(c, t));
    const allSame = samples.every((v) => v === samples[0]);
    assert.ok(!allSame, `${c} pressure profile should vary along the stroke`);
  }
});

test("construction pressure is deliberately flat and low (stays faint, never erased)", () => {
  const samples = [0, 0.25, 0.5, 0.75, 1].map((t) => pressureAt(Category.CONSTRUCTION, t));
  assert.ok(samples.every((v) => v === samples[0]), "construction pressure must be flat");
  assert.ok(samples[0] < 0.2, "construction marks must be low-contrast");
});

test("pressure is always within [0, 1] even with jitter pushing past the natural range", () => {
  for (const c of Object.values(Category)) {
    assert.ok(pressureAt(c, 0, 10) <= 1);
    assert.ok(pressureAt(c, 0, -10) >= 0);
  }
});

test("contour and crosshatch profiles have soft entry/exit (lower pressure at the very ends)", () => {
  for (const c of [Category.CONTOUR, Category.CROSSHATCH]) {
    const start = pressureAt(c, 0);
    const middle = pressureAt(c, 0.5);
    const end = pressureAt(c, 1);
    assert.ok(middle > start, `${c} middle should exceed the soft entry`);
    assert.ok(middle > end, `${c} middle should exceed the soft exit`);
  }
});

test("hatch profile reads as a tapered flick: near-zero at both true ends", () => {
  assert.ok(pressureAt(Category.HATCH, 0) < 0.1);
  assert.ok(pressureAt(Category.HATCH, 1) < 0.1);
  assert.ok(pressureAt(Category.HATCH, 0.5) > pressureAt(Category.HATCH, 0));
});

test("pressure drives both width and opacity scale, monotonically", () => {
  assert.ok(widthScaleFor(1) > widthScaleFor(0));
  assert.ok(alphaScaleFor(1) > alphaScaleFor(0));
});

test("neutral pressure leaves opacity scale at exactly 1x", () => {
  assert.ok(Math.abs(alphaScaleFor(NEUTRAL_PRESSURE) - 1) < 1e-9);
});
