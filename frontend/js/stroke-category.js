// Presentation-layer categorization only -- derived purely from fields the
// Phase 2 planner already annotates (kind, stage, arc_length). No new
// backend data and no image-analysis change; this does not alter which
// strokes exist or their geometry, only how playback treats them.
export const Category = Object.freeze({
  CONSTRUCTION: "construction",
  CONTOUR: "contour",
  HATCH: "hatch",
  CROSSHATCH: "crosshatch",
  FINE_DETAIL: "fine_detail",
});

const FINE_DETAIL_MAX_ARC_LENGTH = 10; // px: very short marks, regardless of stage
const DEEP_SHADING_STAGE = 5; // matches backend planner.STAGE_DEEP_SHADING

/**
 * There is no explicit "is this a cross-hatch pass" flag in the Phase 2
 * contract, so deep-shading-stage hatch strokes (where sketch.py's
 * perpendicular cross-hatch pass is concentrated) are treated as the
 * crosshatch category; this is a documented approximation, not a claim
 * that every such stroke is literally a cross-hatch pass.
 *
 * Phase 5's backend planner flags synthesized gesture/proportion marks
 * with `is_construction`; those are checked first regardless of `kind`
 * (they're generated as kind "line", but should read as quick, light
 * gestural marks, not firm structural contours).
 */
export function classifyStroke(stroke) {
  if (stroke.is_construction) return Category.CONSTRUCTION;
  if (stroke.kind === "line") return Category.CONTOUR;
  if (stroke.arc_length <= FINE_DETAIL_MAX_ARC_LENGTH) return Category.FINE_DETAIL;
  if (stroke.stage === DEEP_SHADING_STAGE) return Category.CROSSHATCH;
  return Category.HATCH;
}
