// Occasional "evaluation" pauses -- an artist stepping back to assess
// progress -- layered on top of the backend's own per-stage-transition
// pause (see backend/planner.py's STAGE_TRANSITION_PAUSE_SECONDS). Inserted
// before: entering the structural-contour stage (a "difficult" moment),
// before the final stage present in the plan (final refinement), and,
// occasionally (not every time), when work moves to a new spatial region
// within the same stage. Deliberately infrequent: this must not pause
// before every small stroke.
import { evalPauseRoll } from "./variation.js";

const STRUCTURAL_CONTOUR_STAGE = 2; // matches backend planner.STAGE_STRUCTURAL_CONTOURS

export const DEFAULT_OPTIONS = Object.freeze({
  seed: 1,
  regionSwitchProbability: 0.35,
  regionSwitchPauseSeconds: 0.5,
  preStructuralPauseSeconds: 0.9,
  finalStagePauseSeconds: 0.7,
});

/**
 * @param {Array} actions plan.actions (plain objects: {type, duration, stroke_id?, x?, y?})
 * @param {Array} strokes plan.strokes (each has id, stage, region_id)
 * @returns {Array} a new actions array with extra {type:"pause", duration} entries spliced in
 */
export function injectEvaluationPauses(actions, strokes, options = {}) {
  const opts = { ...DEFAULT_OPTIONS, ...options };
  const strokesById = new Map(strokes.map((s) => [s.id, s]));
  const stagesPresent = [...new Set(strokes.map((s) => s.stage))].sort((a, b) => a - b);
  const finalStage = stagesPresent[stagesPresent.length - 1];

  const result = [];
  let prevStroke = null;
  for (let i = 0; i < actions.length; i++) {
    const action = actions[i];
    if (action.type === "draw") {
      const stroke = strokesById.get(action.stroke_id);
      if (stroke && prevStroke && stroke.id !== prevStroke.id) {
        if (prevStroke.stage !== stroke.stage) {
          if (stroke.stage === STRUCTURAL_CONTOUR_STAGE) {
            result.push({ type: "pause", duration: opts.preStructuralPauseSeconds });
          } else if (stroke.stage === finalStage && finalStage !== prevStroke.stage) {
            result.push({ type: "pause", duration: opts.finalStagePauseSeconds });
          }
        } else if (prevStroke.region_id !== stroke.region_id) {
          if (evalPauseRoll(opts.seed, i) < opts.regionSwitchProbability) {
            result.push({ type: "pause", duration: opts.regionSwitchPauseSeconds });
          }
        }
      }
      if (stroke) prevStroke = stroke;
    }
    result.push(action);
  }
  return result;
}
