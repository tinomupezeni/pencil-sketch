import test from "node:test";
import assert from "node:assert/strict";
import { cumulativeLengths, totalLength, pointAtLength, segmentBetween } from "../js/geometry.js";

test("cumulativeLengths is zero at the start and monotonic non-decreasing", () => {
  const points = [
    [0, 0],
    [3, 4], // length 5
    [3, 4], // duplicate point: zero-length segment
    [6, 8], // length 5
  ];
  const lens = cumulativeLengths(points);
  assert.deepEqual(lens, [0, 5, 5, 10]);
  assert.equal(totalLength(lens), 10);
});

test("pointAtLength interpolates linearly within a segment", () => {
  const points = [
    [0, 0],
    [10, 0],
  ];
  const lens = cumulativeLengths(points);
  assert.deepEqual(pointAtLength(points, lens, 0), [0, 0]);
  assert.deepEqual(pointAtLength(points, lens, 5), [5, 0]);
  assert.deepEqual(pointAtLength(points, lens, 10), [10, 0]);
});

test("pointAtLength clamps out-of-range targets to the endpoints", () => {
  const points = [
    [0, 0],
    [10, 0],
  ];
  const lens = cumulativeLengths(points);
  assert.deepEqual(pointAtLength(points, lens, -5), [0, 0]);
  assert.deepEqual(pointAtLength(points, lens, 1000), [10, 0]);
});

test("pointAtLength handles an all-duplicate (zero-length) stroke without dividing by zero", () => {
  const points = [
    [5, 5],
    [5, 5],
  ];
  const lens = cumulativeLengths(points);
  assert.deepEqual(pointAtLength(points, lens, 0), [5, 5]);
  assert.deepEqual(pointAtLength(points, lens, 3), [5, 5]);
});

test("segmentBetween never re-includes already-drawn length", () => {
  const points = [
    [0, 0],
    [10, 0],
    [20, 0],
  ];
  const lens = cumulativeLengths(points);
  const first = segmentBetween(points, lens, 0, 10);
  const second = segmentBetween(points, lens, 10, 20);
  assert.deepEqual(first, [
    [0, 0],
    [10, 0],
  ]);
  assert.deepEqual(second, [
    [10, 0],
    [20, 0],
  ]);
  // No point appears in both calls' output except the shared boundary.
  const overlap = first.filter((p) => second.some((q) => q[0] === p[0] && q[1] === p[1]));
  assert.equal(overlap.length, 1);
});

test("segmentBetween returns nothing when there is no new length to cover", () => {
  const points = [
    [0, 0],
    [10, 0],
  ];
  const lens = cumulativeLengths(points);
  assert.deepEqual(segmentBetween(points, lens, 5, 5), []);
  assert.deepEqual(segmentBetween(points, lens, 8, 5), []);
});

test("segmentBetween skips duplicate consecutive points cleanly", () => {
  const points = [
    [0, 0],
    [5, 0],
    [5, 0], // duplicate
    [10, 0],
  ];
  const lens = cumulativeLengths(points);
  const whole = segmentBetween(points, lens, 0, totalLength(lens));
  // every point coordinate still reachable, no NaN/duplicate-induced break
  assert.ok(whole.every(([x, y]) => Number.isFinite(x) && Number.isFinite(y)));
  assert.deepEqual(whole[whole.length - 1], [10, 0]);
});
