// Translates the plan's *actual* artistic stage (from backend planner.py's
// STAGE_NAMES, carried in plan.metadata.stageNames) into a short,
// glanceable phrase. The phase itself is always real drawing-plan data;
// only the wording is a presentation choice, same as styling "kind"/"shade"
// -- never a fictional status message invented independently of the plan.
const FRIENDLY_STAGE_LABELS = {
  construction_and_broad_gesture: "Sketching the composition",
  major_structural_contours: "Drawing structure",
  light_tonal_blocking: "Blocking in tone",
  regional_development: "Developing the drawing",
  deep_shading_and_cross_hatching: "Deepening shadows",
  final_accents_and_refinement: "Adding final details",
};

export function humanizeStageSlug(slug) {
  return FRIENDLY_STAGE_LABELS[slug] || "Drawing";
}

/** @param {object|null} stageNames plan.metadata.stageNames ({"1": "...", ...}) */
export function phaseLabelForStage(stageNames, stageNumber) {
  if (!stageNames || stageNumber == null) return "Drawing";
  const slug = stageNames[String(stageNumber)];
  return humanizeStageSlug(slug);
}

export function formatProgressLine(phaseText, pct) {
  return `${phaseText} · ${pct}%`;
}
