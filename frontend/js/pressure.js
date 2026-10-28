// Pressure is evaluated along a stroke (t in [0,1], arc-length-normalized),
// not assigned a single constant value, and drives visible width/opacity --
// this is what makes graphite read as hand-applied instead of a uniform
// plotted line. Profiles are keyed by the same category strings
// stroke-category.js produces.
import { Category } from "./stroke-category.js";

function clamp01(x) {
  return Math.min(1, Math.max(0, x));
}

function smoothstep(edge0, edge1, x) {
  const t = clamp01((x - edge0) / (edge1 - edge0));
  return t * t * (3 - 2 * t);
}

const PROFILES = {
  // Very light, flat pressure throughout: a construction/gesture mark
  // stays faint for its entire lifetime rather than being erased -- see
  // backend planner.py's synthesize_construction_strokes docstring.
  [Category.CONSTRUCTION]: () => 0.12,
  // Soft entry/exit, firm consistent middle pressure: a deliberate,
  // carefully-controlled structural line.
  [Category.CONTOUR]: (t) => {
    const inRamp = smoothstep(0, 0.12, t);
    const outRamp = 1 - smoothstep(0.88, 1, t);
    return 0.35 + 0.5 * inRamp * outRamp;
  },
  // Tapered flick: rises quickly, eases away toward both ends.
  [Category.HATCH]: (t) => Math.pow(Math.sin(Math.PI * Math.pow(clamp01(t), 0.7)), 0.8),
  // Heavier, flatter: deliberate reinforcing marks building up a shadow.
  [Category.CROSSHATCH]: (t) => {
    const inRamp = smoothstep(0, 0.08, t);
    const outRamp = 1 - smoothstep(0.92, 1, t);
    return 0.55 + 0.4 * inRamp * outRamp;
  },
  // Light, fairly flat: a careful, low-pressure touch.
  [Category.FINE_DETAIL]: (t) => {
    const inRamp = smoothstep(0, 0.2, t);
    const outRamp = 1 - smoothstep(0.8, 1, t);
    return 0.2 + 0.2 * inRamp * outRamp;
  },
};

/** @returns {number} pressure in [0, 1] at normalized position `t`, plus bounded jitter. */
export function pressureAt(category, t, jitter = 0) {
  const fn = PROFILES[category] || PROFILES[Category.HATCH];
  return clamp01(fn(clamp01(t)) + jitter);
}

const MIN_WIDTH_SCALE = 0.55;
const MAX_WIDTH_SCALE = 1.35;
const MIN_ALPHA_SCALE = 0.7;
const MAX_ALPHA_SCALE = 1.2;
// alphaScaleFor(NEUTRAL_PRESSURE) === 1, i.e. no net opacity change versus
// the shade-driven base alpha when pressure is exactly "neutral".
export const NEUTRAL_PRESSURE = (1 - MIN_ALPHA_SCALE) / (MAX_ALPHA_SCALE - MIN_ALPHA_SCALE);

export function widthScaleFor(pressure) {
  return MIN_WIDTH_SCALE + clamp01(pressure) * (MAX_WIDTH_SCALE - MIN_WIDTH_SCALE);
}

export function alphaScaleFor(pressure) {
  return MIN_ALPHA_SCALE + clamp01(pressure) * (MAX_ALPHA_SCALE - MIN_ALPHA_SCALE);
}
