// Controlled human variation: subtle, bounded jitter in speed/pressure and
// pen-travel behavior, plus the evaluation-pause probability roll -- all
// derived from one seed via rng.js's (seed, id) hash, so it's fully
// reproducible and never touches actual stroke geometry (no positional
// jitter of the drawn points themselves).
import { deterministicUnit } from "./rng.js";

// Disjoint integer namespaces so different uses of the same numeric id
// (e.g. stroke id 7's speed jitter vs its pressure jitter) never collide.
const NS_SPEED = 1;
const NS_PRESSURE = 2;
const NS_BOW_SIGN = 3;
const NS_BOW_MAGNITUDE = 4;
const NS_EVAL_PAUSE = 5;

function unit(seed, namespace, id) {
  return deterministicUnit(seed, namespace * 1_000_000_007 + id);
}

/** +/-8% duration multiplier: human, not erratic. */
export function speedJitter(seed, strokeId) {
  return 1 + (unit(seed, NS_SPEED, strokeId) * 2 - 1) * 0.08;
}

/** +/-0.05 additive pressure jitter, applied on top of a category's base profile. */
export function pressureJitter(seed, strokeId) {
  return (unit(seed, NS_PRESSURE, strokeId) * 2 - 1) * 0.05;
}

export function travelBowSign(seed, moveActionIndex) {
  return unit(seed, NS_BOW_SIGN, moveActionIndex) < 0.5 ? -1 : 1;
}

/** A float in [0, 1) scaling how much of the allowed bow a given move actually uses. */
export function travelBowMagnitudeFraction(seed, moveActionIndex) {
  return unit(seed, NS_BOW_MAGNITUDE, moveActionIndex);
}

/** A float in [0, 1); callers compare against a configured probability threshold. */
export function evalPauseRoll(seed, actionIndex) {
  return unit(seed, NS_EVAL_PAUSE, actionIndex);
}
