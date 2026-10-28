import test from "node:test";
import assert from "node:assert/strict";
import { Category, classifyStroke } from "../js/stroke-category.js";

test("line strokes are always contours", () => {
  assert.equal(classifyStroke({ kind: "line", arc_length: 500, stage: 2 }), Category.CONTOUR);
});

test("construction-flagged strokes are construction regardless of kind", () => {
  assert.equal(
    classifyStroke({ kind: "line", arc_length: 500, stage: 1, is_construction: true }),
    Category.CONSTRUCTION
  );
});

test("very short hatch marks are fine detail regardless of stage", () => {
  assert.equal(classifyStroke({ kind: "hatch", arc_length: 4, stage: 3 }), Category.FINE_DETAIL);
  assert.equal(classifyStroke({ kind: "hatch", arc_length: 4, stage: 5 }), Category.FINE_DETAIL);
});

test("deep-shading-stage hatch marks (not tiny) are crosshatch", () => {
  assert.equal(classifyStroke({ kind: "hatch", arc_length: 30, stage: 5 }), Category.CROSSHATCH);
});

test("other hatch marks are plain hatch", () => {
  assert.equal(classifyStroke({ kind: "hatch", arc_length: 30, stage: 3 }), Category.HATCH);
  assert.equal(classifyStroke({ kind: "hatch", arc_length: 30, stage: 4 }), Category.HATCH);
});
