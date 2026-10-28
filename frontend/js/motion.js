// Motion profiles: reshape a linear time fraction (u, how far through an
// action's *scaled* duration we are) into a perceptual progress fraction,
// and scale intrinsic (natural) durations differently per category before
// the global presentation-duration scale is applied (see timeline.js).
// This is what separates "robotic constant-velocity plotting" from marks
// that accelerate/decelerate like a hand actually makes them.

function clamp01(u) {
  return Math.min(1, Math.max(0, u));
}

function easeInOutCubic(u) {
  return u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
}
function easeOutCubic(u) {
  return 1 - Math.pow(1 - u, 3);
}
function easeOutQuad(u) {
  return 1 - (1 - u) * (1 - u);
}
function easeInOutQuad(u) {
  return u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
}
function linear(u) {
  return u;
}

// Keyed by the same category strings stroke-category.js produces, plus
// "travel" for pen-up moves (which have no stroke/category of their own).
const EASINGS = {
  travel: easeInOutQuad, // efficient, purposeful reposition: smooth but not lingering
  construction: easeOutQuad, // quick, confident gestural sketch mark
  contour: easeInOutCubic, // slow, careful, decelerates into precision at the end
  hatch: easeOutCubic, // quick flick: fast start, short decisive taper-off
  crosshatch: easeOutQuad, // quick, slightly less tapered than a single hatch flick
  fine_detail: easeInOutCubic, // careful, a little hesitant
};

export function ease(category, u) {
  const fn = EASINGS[category] || linear;
  return fn(clamp01(u));
}

// Intrinsic duration multipliers, applied to a stroke's backend-estimated
// natural duration *before* the global scale-to-requested-duration factor
// (see timeline.js's `multiplierFor` option) -- so relative pacing between
// categories (contours deliberate and slow, hatch/crosshatch quick flicks)
// holds proportionally at any requested total duration, from 2 minutes to
// 4 hours, rather than being washed out by uniform global scaling.
const CATEGORY_DURATION_MULTIPLIER = {
  travel: 1.0,
  construction: 0.6, // loose, fast warm-up gesture
  contour: 1.15,
  hatch: 0.9,
  crosshatch: 0.85,
  fine_detail: 1.25,
};

export function durationMultiplierFor(category) {
  return CATEGORY_DURATION_MULTIPLIER[category] ?? 1.0;
}
