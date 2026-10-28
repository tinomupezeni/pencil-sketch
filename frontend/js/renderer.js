// Graphite rendering: draws strokes onto a CanvasRenderingContext2D-shaped
// object. Deliberately takes `ctx` as a plain duck-typed object (only
// beginPath/moveTo/lineTo/stroke/fill/arc/fillRect and style properties are
// used) so this is testable with a lightweight fake context, without a
// real browser canvas or any added test-canvas dependency.
import { cumulativeLengths, segmentBetween, pointAtLength } from "./geometry.js";
import { classifyStroke } from "./stroke-category.js";
import { pressureAt, widthScaleFor, alphaScaleFor, NEUTRAL_PRESSURE } from "./pressure.js";
import { pressureJitter as seededPressureJitter } from "./variation.js";

// Bounds both how finely pressure/width are sampled along a stroke and the
// worst-case number of stroke() calls needed to render one in a single
// "catch-up" shot (see renderStrokeComplete): a ~140px stroke needs at
// most ~9 calls, not one per point or per animation frame.
const MAX_PAINT_CHUNK_LEN_PX = 16;

// Parses the backend's compact "#rrggbb" stroke color (Phase 9, section 3)
// into an "r, g, b" string for rgba() interpolation. Never more than this
// one hex string per stroke -- not a per-point RGB array -- is sent over
// the wire or stored per stroke.
function hexToRgbTriplet(hex) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `${r}, ${g}, ${b}`;
}

// Graphite, not ink: soft gray-black by default, or a muted source color
// when the stroke carries one (Faithful Pencil's colored-pencil accents --
// section 3). Either way, pressure modulates opacity on top of the exact
// same shade-driven base alpha formula, so colored strokes build up via
// the same translucent, layered animation as neutral graphite; only the
// base hue differs. (pressure=NEUTRAL_PRESSURE leaves the base alpha
// unchanged, for callers that don't care about pressure.)
export function strokeColor(stroke, pressure = NEUTRAL_PRESSURE) {
  const baseAlpha =
    stroke.kind === "line" ? 0.3 + stroke.shade * 0.3 : 0.08 + stroke.shade * 0.32;
  const alpha = Math.min(0.95, Math.max(0.03, baseAlpha * alphaScaleFor(pressure)));
  const rgb = stroke.color ? hexToRgbTriplet(stroke.color) : stroke.kind === "line" ? "55, 54, 58" : "60, 58, 56";
  return `rgba(${rgb}, ${alpha.toFixed(3)})`;
}

export class GraphiteRenderer {
  constructor(ctx, { seed = 1 } = {}) {
    this.ctx = ctx;
    this.seed = seed;
    this._cursors = new Map(); // strokeId -> cursor (see _cursorFor)
  }

  /** Forgets all per-stroke progress -- a fresh replay starts every stroke over. */
  reset() {
    this._cursors.clear();
  }

  clear(width, height, paperColor = "#fdfcf9") {
    this.reset();
    this.ctx.fillStyle = paperColor;
    this.ctx.fillRect(0, 0, width, height);
  }

  _cursorFor(stroke) {
    let cursor = this._cursors.get(stroke.id);
    if (!cursor) {
      const lens = cumulativeLengths(stroke.points);
      cursor = {
        lens,
        total: lens[lens.length - 1],
        lastLen: 0,
        category: classifyStroke(stroke),
        pressureJitter: seededPressureJitter(this.seed, stroke.id),
      };
      this._cursors.set(stroke.id, cursor);
    }
    return cursor;
  }

  /**
   * Advances a stroke's rendering to `targetLen` (arc length). Only the
   * incremental range since this stroke's last call is painted -- previously
   * rendered ink is never re-stroked, so already-completed portions of a
   * stroke never darken just because more frames have elapsed. Width and
   * opacity are resampled from the stroke's pressure profile at each
   * sub-chunk, so a long increment (as happens in the catch-up fast path)
   * still reads as a tapered, hand-applied mark rather than one flat bar.
   * Safe to call with Number.POSITIVE_INFINITY to finish a stroke in one shot.
   */
  renderStrokeUpTo(stroke, targetLen) {
    const cursor = this._cursorFor(stroke);
    const clamped = Math.min(targetLen, cursor.total);
    if (clamped <= cursor.lastLen) return;
    this._paintRange(stroke, cursor, cursor.lastLen, clamped);
    cursor.lastLen = clamped;
  }

  /** Bounded, few-draw-call fast path used to catch a stroke fully up to date. */
  renderStrokeComplete(stroke) {
    this.renderStrokeUpTo(stroke, Number.POSITIVE_INFINITY);
  }

  isStrokeComplete(stroke) {
    const cursor = this._cursors.get(stroke.id);
    return !!cursor && cursor.lastLen >= cursor.total;
  }

  /** Total arc length of a stroke's polyline, in its own point units. */
  totalLengthOf(stroke) {
    return this._cursorFor(stroke).total;
  }

  /** Where the pencil tip sits for a given arc-length progress into a stroke. */
  positionAtProgress(stroke, targetLen) {
    const cursor = this._cursorFor(stroke);
    return pointAtLength(stroke.points, cursor.lens, targetLen);
  }

  _paintRange(stroke, cursor, fromLen, toLen) {
    if (toLen <= fromLen) return;
    const total = cursor.total || 1;
    const span = toLen - fromLen;
    const chunkCount = Math.max(1, Math.ceil(span / MAX_PAINT_CHUNK_LEN_PX));
    const chunkLen = span / chunkCount;

    let chunkStart = fromLen;
    for (let i = 0; i < chunkCount; i++) {
      const chunkEnd = i === chunkCount - 1 ? toLen : chunkStart + chunkLen;
      const path = segmentBetween(stroke.points, cursor.lens, chunkStart, chunkEnd);
      if (path.length >= 2) {
        const midT = (chunkStart + chunkEnd) / 2 / total;
        const pressure = pressureAt(cursor.category, midT, cursor.pressureJitter);
        const isTrueStart = chunkStart <= 1e-6;
        const isTrueEnd = chunkEnd >= total - 1e-6;
        this._paint(stroke, path, pressure, isTrueStart, isTrueEnd);
      }
      chunkStart = chunkEnd;
    }
  }

  _paint(stroke, path, pressure, isTrueStart, isTrueEnd) {
    const ctx = this.ctx;
    const color = strokeColor(stroke, pressure);
    const width = Math.max(0.3, stroke.width * widthScaleFor(pressure));

    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    // "butt", always: two separate stroke() calls (one per chunk/frame)
    // that share an exact boundary point must never each paint a round cap
    // there -- two overlapping translucent half-disks at the same pixel is
    // exactly the "dark dot at the join" artifact. A soft, tapered look at
    // the stroke's *true* extremities is instead added explicitly below,
    // painted exactly once per stroke, never at an interior seam.
    ctx.lineCap = "butt";
    ctx.lineJoin = "round";
    ctx.moveTo(path[0][0], path[0][1]);
    for (let i = 1; i < path.length; i++) ctx.lineTo(path[i][0], path[i][1]);
    ctx.stroke();

    if (isTrueStart) this._stampTip(path[0], width, color);
    if (isTrueEnd) this._stampTip(path[path.length - 1], width, color);
  }

  _stampTip(point, width, color) {
    const ctx = this.ctx;
    ctx.beginPath();
    ctx.fillStyle = color;
    ctx.arc(point[0], point[1], width / 2, 0, Math.PI * 2);
    ctx.fill();
  }
}
