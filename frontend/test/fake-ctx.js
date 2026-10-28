// Minimal duck-typed stand-in for CanvasRenderingContext2D, recording what
// was drawn instead of rendering pixels. Covers exactly the subset of the
// Canvas API GraphiteRenderer/PencilTip use.
export class FakeCtx {
  constructor() {
    this.strokeCalls = []; // each entry: the polyline path points for one stroke() call
    this.strokeStyles = []; // each entry: {lineCap, lineWidth, strokeStyle} snapshot at that stroke() call
    this.arcCalls = []; // each entry: {x, y, radius, fillStyle} for a filled-circle "tip stamp"
    this.fillRectCalls = [];
    this._currentPath = [];
    this._pendingArc = null;
  }
  beginPath() {
    this._currentPath = [];
  }
  moveTo(x, y) {
    this._currentPath.push([x, y]);
  }
  lineTo(x, y) {
    this._currentPath.push([x, y]);
  }
  closePath() {}
  stroke() {
    this.strokeCalls.push(this._currentPath.slice());
    this.strokeStyles.push({
      lineCap: this._lineCap,
      lineWidth: this._lineWidth,
      strokeStyle: this._strokeStyle,
    });
  }
  arc(x, y, radius) {
    this._pendingArc = { x, y, radius };
  }
  fill() {
    if (this._pendingArc) {
      this.arcCalls.push({ ...this._pendingArc, fillStyle: this._fillStyle });
      this._pendingArc = null;
    }
  }
  fillRect(x, y, w, h) {
    this.fillRectCalls.push([x, y, w, h]);
  }
  clearRect() {}
  save() {}
  restore() {}
  translate() {}
  rotate() {}
  createRadialGradient() {
    return { addColorStop() {} };
  }
}
for (const prop of ["strokeStyle", "lineWidth", "lineCap", "lineJoin", "fillStyle"]) {
  Object.defineProperty(FakeCtx.prototype, prop, {
    get() {
      return this[`_${prop}`];
    },
    set(v) {
      this[`_${prop}`] = v;
    },
  });
}
