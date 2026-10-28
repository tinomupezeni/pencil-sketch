// Pure polyline arc-length math. No DOM/canvas dependency, so this is
// directly unit-testable under plain Node.
//
// Stroke progress is driven by cumulative arc length rather than point
// index so that visual drawing speed stays consistent regardless of how
// densely a particular stroke's points happen to be sampled.

/**
 * @param {number[][]} points
 * @returns {number[]} cumulative arc length at each point; lens[0] === 0.
 *   Duplicate consecutive points (zero-length segments) simply contribute 0.
 */
export function cumulativeLengths(points) {
  const lens = [0];
  for (let i = 1; i < points.length; i++) {
    const [x0, y0] = points[i - 1];
    const [x1, y1] = points[i];
    const d = Math.hypot(x1 - x0, y1 - y0);
    lens.push(lens[i - 1] + d);
  }
  return lens;
}

export function totalLength(lens) {
  return lens.length === 0 ? 0 : lens[lens.length - 1];
}

/**
 * Interpolates the point on the polyline at the given arc length `target`.
 * Clamps to the endpoints for out-of-range targets. Safe for single-point
 * strokes and zero-length (all-duplicate-point) strokes.
 */
export function pointAtLength(points, lens, target) {
  if (points.length === 1) return points[0];
  const total = lens[lens.length - 1];
  if (target <= 0 || total <= 0) return points[0];
  if (target >= total) return points[points.length - 1];

  let i = 1;
  while (i < lens.length && lens[i] < target) i++;
  const segStart = lens[i - 1];
  const segEnd = lens[i];
  const segLen = segEnd - segStart;
  if (segLen <= 0) return points[i]; // zero-length segment: no interpolation possible
  const t = (target - segStart) / segLen;
  const [x0, y0] = points[i - 1];
  const [x1, y1] = points[i];
  return [x0 + (x1 - x0) * t, y0 + (y1 - y0) * t];
}

/**
 * The exact path to stroke in order to advance a stroke's rendering from
 * `fromLen` (already drawn, exclusive) to `toLen` (inclusive) -- the
 * previously-rendered prefix is never included, so re-painting this
 * segment never re-darkens ink that's already on the page.
 *
 * @returns {number[][]} a polyline (>=0 points; <2 means "nothing to draw")
 */
export function segmentBetween(points, lens, fromLen, toLen) {
  const total = lens.length === 0 ? 0 : lens[lens.length - 1];
  const clampedTo = Math.min(toLen, total);
  if (clampedTo <= fromLen) return [];

  const path = [pointAtLength(points, lens, fromLen)];
  for (let i = 0; i < points.length; i++) {
    if (lens[i] > fromLen && lens[i] < clampedTo) {
      path.push(points[i]);
    }
  }
  path.push(pointAtLength(points, lens, clampedTo));
  return path;
}

export function lerp2(a, b, t) {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
}

/** Point at parameter t (0..1) along the quadratic Bezier p0 -> control -> p1. */
export function quadraticBezierPoint(p0, control, p1, t) {
  const mt = 1 - t;
  const x = mt * mt * p0[0] + 2 * mt * t * control[0] + t * t * p1[0];
  const y = mt * mt * p0[1] + 2 * mt * t * control[1] + t * t * p1[1];
  return [x, y];
}
