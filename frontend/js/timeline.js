// Builds a cumulative timeline from a drawing plan's actions, scaled to the
// user's chosen total playback duration. Pure (no DOM), unit-testable.

/**
 * Scaling policy: each action's natural (backend-estimated) duration is
 * first multiplied by an optional, category-specific `multiplierFor`
 * weight (e.g. careful contours drawn a bit slower than their raw
 * arc-length estimate, quick hatch flicks a bit faster -- see motion.js
 * and player.js), and the *weighted* total is then scaled by one constant
 * factor so the sum of scaled durations equals exactly `targetDurationMs`.
 *
 * This two-step policy is what separates intrinsic timing from requested
 * presentation duration: relative pacing between categories (and between
 * individual actions' natural estimates) is preserved proportionally at
 * any requested total, from 2 minutes to 4 hours, rather than being
 * washed out by a single uniform multiplier. Progression is driven by
 * this scaled time, never by point or action count. With no
 * `multiplierFor` supplied, every action weighs 1 and this reduces to
 * plain proportional scaling.
 *
 * @param {Array<{duration:number}>} actions
 * @param {number} targetDurationMs
 * @param {{multiplierFor?: (action:object) => number}} [options]
 * @returns {{entries: Array<{action:object, startMs:number, endMs:number}>, totalMs:number}}
 */
export function buildTimeline(actions, targetDurationMs, options = {}) {
  const multiplierFor = options.multiplierFor || (() => 1);
  const weighted = actions.map((a) => a.duration * multiplierFor(a));
  const naturalTotalSec = weighted.reduce((sum, w) => sum + w, 0);
  const scale = naturalTotalSec > 0 ? targetDurationMs / (naturalTotalSec * 1000) : 0;

  let cursor = 0;
  const entries = actions.map((action, i) => {
    const durationMs = naturalTotalSec > 0 ? weighted[i] * 1000 * scale : 0;
    const startMs = cursor;
    cursor += durationMs;
    return { action, startMs, endMs: cursor };
  });

  return { entries, totalMs: cursor };
}

/**
 * Index of the last timeline entry whose startMs <= t (binary search).
 * Returns -1 for an empty timeline. Zero-duration actions (startMs ===
 * endMs, e.g. "lift") are valid entries and are matched like any other.
 */
export function findActionIndex(entries, t) {
  if (entries.length === 0) return -1;
  let lo = 0;
  let hi = entries.length - 1;
  let ans = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (entries[mid].startMs <= t) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}
