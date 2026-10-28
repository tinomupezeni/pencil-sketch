// Deterministic pseudo-randomness. Every bit of controlled human variation
// in this app (speed/pressure jitter, travel bow, evaluation-pause rolls)
// is derived from (seed, id) via a hash, not a shared mutable stream --
// so a given stroke/action's jitter never depends on what order other
// strokes happened to be processed in, only on the seed and its own id.
// Same input + same seed therefore always reproduce the same output.

function mulberry32(a) {
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashSeed(seed, id) {
  let h = (seed ^ 0x9e3779b9) >>> 0;
  h = Math.imul(h ^ id, 2654435761) >>> 0;
  h ^= h >>> 15;
  return h >>> 0;
}

/** A float in [0, 1), purely a function of (seed, id). */
export function deterministicUnit(seed, id) {
  return mulberry32(hashSeed(seed, id))();
}
