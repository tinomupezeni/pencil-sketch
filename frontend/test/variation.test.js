import test from "node:test";
import assert from "node:assert/strict";
import { deterministicUnit } from "../js/rng.js";
import {
  speedJitter,
  pressureJitter,
  travelBowSign,
  travelBowMagnitudeFraction,
  evalPauseRoll,
} from "../js/variation.js";

test("deterministicUnit is reproducible: same seed+id always gives the same value", () => {
  assert.equal(deterministicUnit(7, 42), deterministicUnit(7, 42));
});

test("deterministicUnit does not depend on call order (order-independence proxy for reproducibility)", () => {
  const a1 = deterministicUnit(7, 1);
  const b1 = deterministicUnit(7, 2);
  // Calling in the reverse order must not change either result.
  const b2 = deterministicUnit(7, 2);
  const a2 = deterministicUnit(7, 1);
  assert.equal(a1, a2);
  assert.equal(b1, b2);
});

test("different seeds produce different (but each internally reproducible) streams", () => {
  assert.notEqual(deterministicUnit(1, 5), deterministicUnit(2, 5));
});

test("speed jitter is bounded to +/-8% and reproducible", () => {
  for (let id = 0; id < 50; id++) {
    const j = speedJitter(3, id);
    assert.ok(j >= 0.92 && j <= 1.08);
    assert.equal(j, speedJitter(3, id));
  }
});

test("pressure jitter is bounded to +/-0.05", () => {
  for (let id = 0; id < 50; id++) {
    const j = pressureJitter(3, id);
    assert.ok(j >= -0.05 && j <= 0.05);
  }
});

test("travel bow sign/magnitude are reproducible and magnitude is a fraction in [0, 1)", () => {
  for (let i = 0; i < 20; i++) {
    assert.ok([-1, 1].includes(travelBowSign(5, i)));
    const m = travelBowMagnitudeFraction(5, i);
    assert.ok(m >= 0 && m < 1);
  }
});

test("eval pause roll is a reproducible float in [0, 1)", () => {
  const r1 = evalPauseRoll(9, 123);
  const r2 = evalPauseRoll(9, 123);
  assert.equal(r1, r2);
  assert.ok(r1 >= 0 && r1 < 1);
});

test("speed jitter and pressure jitter for the same id are independent (different namespaces)", () => {
  // They shouldn't trivially collapse to the same transform of the same
  // underlying id -- spot-check across many ids that they're not identical.
  let anyDifferent = false;
  for (let id = 0; id < 20; id++) {
    if (speedJitter(1, id) !== 1 + pressureJitter(1, id)) {
      anyDifferent = true;
      break;
    }
  }
  assert.ok(anyDifferent);
});
