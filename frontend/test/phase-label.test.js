import test from "node:test";
import assert from "node:assert/strict";
import { humanizeStageSlug, phaseLabelForStage, formatProgressLine } from "../js/phase-label.js";

test("known stage slugs get a friendly label", () => {
  assert.equal(humanizeStageSlug("deep_shading_and_cross_hatching"), "Deepening shadows");
  assert.equal(humanizeStageSlug("construction_and_broad_gesture"), "Sketching the composition");
});

test("unknown slug falls back to a generic label rather than throwing", () => {
  assert.equal(humanizeStageSlug("something_new"), "Drawing");
  assert.equal(humanizeStageSlug(undefined), "Drawing");
});

test("phaseLabelForStage reads the real stageNames map from plan metadata", () => {
  const stageNames = { "1": "construction_and_broad_gesture", "5": "deep_shading_and_cross_hatching" };
  assert.equal(phaseLabelForStage(stageNames, 5), "Deepening shadows");
  assert.equal(phaseLabelForStage(stageNames, 1), "Sketching the composition");
});

test("phaseLabelForStage handles missing metadata gracefully", () => {
  assert.equal(phaseLabelForStage(null, 5), "Drawing");
  assert.equal(phaseLabelForStage({}, 5), "Drawing");
  assert.equal(phaseLabelForStage({ "5": "deep_shading_and_cross_hatching" }, null), "Drawing");
});

test("formatProgressLine composes the glanceable line", () => {
  assert.equal(formatProgressLine("Deepening shadows", 42), "Deepening shadows · 42%");
});
