// Action-timeline playback engine: executes a DrawingPlan's lift/move/draw/
// pause actions against a monotonic virtual clock. Owns playback state,
// pencil-contact state, and per-frame scheduling; delegates actual pixel
// work to GraphiteRenderer/PencilTip and pure timeline/geometry math to
// timeline.js/clock.js. Framework-free; `now`/`scheduleFrame`/`cancelFrame`
// are injected so this class runs identically in a browser (default:
// performance.now / requestAnimationFrame) and under plain Node tests
// (fake clock + manual _tick calls, no DOM required).
import { quadraticBezierPoint } from "./geometry.js";
import { buildTimeline } from "./timeline.js";
import { States, PenState, nextState } from "./playback-state.js";
import { computePlayheadMs } from "./clock.js";
import { ease, durationMultiplierFor } from "./motion.js";
import { classifyStroke } from "./stroke-category.js";
import { speedJitter, travelBowSign, travelBowMagnitudeFraction } from "./variation.js";
import { injectEvaluationPauses } from "./eval-pauses.js";

const FRAME_BUDGET_MS = 8; // time-boxed catch-up work per animation frame
const MAX_TRAVEL_BOW_PX = 14; // caps how much a pen-up move curves -- "restrained and efficient"
const TRAVEL_BOW_RATIO = 0.18; // bow scales with move distance, up to the cap above

export class Player {
  constructor({
    graphite,
    tip,
    seed = 1,
    now = () => performance.now(),
    scheduleFrame = (cb) => requestAnimationFrame(cb),
    cancelFrame = (h) => cancelAnimationFrame(h),
  }) {
    this.graphite = graphite;
    this.tip = tip;
    this.seed = seed;
    this._now = now;
    this._scheduleFrame = scheduleFrame;
    this._cancelFrame = cancelFrame;

    this.state = States.IDLE;
    this.penState = PenState.UP;
    this.position = [0, 0];
    this.currentStage = null;
    this.plan = null;
    this.entries = [];
    this.totalMs = 0;

    this._rafHandle = null;
    this._actionCursor = -1; // index of the last FULLY completed action
    this._lastCurrentIdx = -1;
    this._moveStartPos = null;
    this._moveControlPoint = null;
    this._loggedDraws = new Set();
    this._drawOrdinal = 0;
    this._totalDrawActions = 0;

    this._pausedAccumMs = 0;
    this._playStartPerf = 0;

    // user-assignable callbacks; all optional
    this.onStateChange = null;
    this.onProgress = null; // ({elapsedMs, totalMs})
    this.onDrawStart = null; // ({index, total, stroke})
    this.onPhaseChange = null; // (stage) -- fires only when the stage actually changes
  }

  setLoading() {
    this._setState(nextState(this.state, "LOAD_START"));
  }

  setError(message) {
    this.lastError = message;
    this._setState(nextState(this.state, "LOAD_ERROR"));
  }

  /** Loads a validated DrawingPlan and scales it to `targetDurationMs`.
   * Safe to call directly (it enters the "loading" state itself if the
   * caller hasn't already called setLoading()). Deterministic given the
   * same plan/targetDurationMs/seed: evaluation-pause placement, per-stroke
   * speed jitter and travel bow are all derived from `this.seed`. */
  load(plan, targetDurationMs) {
    if (this.state !== States.LOADING) {
      this._setState(nextState(this.state, "LOAD_START"));
    }
    this.plan = plan;
    this.strokesById = new Map(plan.strokes.map((s) => [s.id, s]));

    const actionsWithPauses = injectEvaluationPauses(plan.actions, plan.strokes, {
      seed: this.seed,
    });
    const multiplierFor = (action) => {
      if (action.type !== "draw") return 1;
      const stroke = this.strokesById.get(action.stroke_id);
      if (!stroke) return 1;
      const category = classifyStroke(stroke);
      return durationMultiplierFor(category) * speedJitter(this.seed, stroke.id);
    };
    const { entries, totalMs } = buildTimeline(actionsWithPauses, targetDurationMs, {
      multiplierFor,
    });
    this.entries = entries;
    this.totalMs = totalMs;
    this._totalDrawActions = actionsWithPauses.filter((a) => a.type === "draw").length;
    this._resetPlayheadState();
    this.graphite.clear(plan.width, plan.height);
    this.tip.clear(plan.width, plan.height);
    this._setState(nextState(this.state, "LOAD_SUCCESS"));
  }

  /** (Re)starts playback from a clean canvas -- used for both the first
   * play after upload and the Replay button; always resets fully.
   *
   * @param {{resumeAtMs?: number}} [options] `resumeAtMs` seeds the virtual
   *   playhead at a prior elapsed time (session recovery, Phase 8): the
   *   canvas/renderer state still starts clean, but the very next tick's
   *   existing time-boxed catch-up loop fast-forwards through every action
   *   up to that point -- the same mechanism that already handles a long
   *   background-tab gap, reused rather than duplicated.
   */
  start({ resumeAtMs = 0 } = {}) {
    if (!this.plan) return;
    this._cancelScheduled();
    this._resetPlayheadState();
    this.graphite.clear(this.plan.width, this.plan.height);
    this.tip.clear(this.plan.width, this.plan.height);
    this._pausedAccumMs = Math.max(0, Math.min(resumeAtMs, this.totalMs));
    this._playStartPerf = this._now();
    this._setState(nextState(this.state, "START"));
    this._scheduleNext();
  }

  pause() {
    if (this.state !== States.PLAYING) return;
    this._pausedAccumMs = computePlayheadMs({
      state: this.state,
      pausedAccumMs: this._pausedAccumMs,
      playStartPerf: this._playStartPerf,
      nowPerf: this._now(),
    });
    this._cancelScheduled();
    this._setState(nextState(this.state, "PAUSE"));
  }

  resume() {
    if (this.state !== States.PAUSED) return;
    this._playStartPerf = this._now();
    this._setState(nextState(this.state, "RESUME"));
    this._scheduleNext();
  }

  _resetPlayheadState() {
    this._actionCursor = -1;
    this._lastCurrentIdx = -1;
    this._moveStartPos = null;
    this._moveControlPoint = null;
    this._loggedDraws = new Set();
    this._drawOrdinal = 0;
    this.penState = PenState.UP;
    this.position = [0, 0];
    this.currentStage = null;
  }

  _setCurrentStage(stage) {
    if (stage !== this.currentStage) {
      this.currentStage = stage;
      if (this.onPhaseChange) this.onPhaseChange(stage);
    }
  }

  _setState(next) {
    this.state = next;
    if (this.onStateChange) this.onStateChange(this.state);
  }

  _cancelScheduled() {
    if (this._rafHandle !== null) {
      this._cancelFrame(this._rafHandle);
      this._rafHandle = null;
    }
  }

  _scheduleNext() {
    // Cancel any frame already pending before arming a new one: a request
    // is a standalone registration, not a single mutable slot, so calling
    // this twice without an intervening real fire (as repeated manual
    // _tick() calls -- e.g. a seek/catch-up driven from outside the normal
    // rAF loop -- can do) would otherwise leak extra pending frames that
    // all fire later with the browser's own (tiny, real-elapsed)
    // timestamp and silently stomp on whatever progress was just set.
    this._cancelScheduled();
    this._rafHandle = this._scheduleFrame((t) => this._tick(t));
  }

  /** One playback step. Exposed (not private) so tests can drive it directly
   * with controlled timestamps instead of real rAF/performance.now. */
  _tick(nowPerf) {
    if (this.state !== States.PLAYING) return;

    const target = computePlayheadMs({
      state: this.state,
      pausedAccumMs: this._pausedAccumMs,
      playStartPerf: this._playStartPerf,
      nowPerf,
    });

    const frameDeadline = this._now() + FRAME_BUDGET_MS;
    // Bounded-but-adaptive catch-up: fully-elapsed actions are each
    // finished in one cheap O(1)-draw-call step (see
    // GraphiteRenderer.renderStrokeComplete), budgeted by wall-clock time
    // rather than a fixed action/point count -- so an arbitrarily long
    // background/throttle gap is cleared over as many frames as it takes,
    // never left permanently behind, and never blocks a single frame for
    // longer than the budget.
    while (this._actionCursor + 1 < this.entries.length) {
      const idx = this._actionCursor + 1;
      const entry = this.entries[idx];
      if (entry.endMs > target) break;
      this._completeInstant(idx, entry);
      this._actionCursor = idx;
      if (this._now() > frameDeadline) break;
    }

    if (this._actionCursor + 1 >= this.entries.length) {
      this._cancelScheduled();
      this.tip.clear(this.plan.width, this.plan.height);
      this._emitProgress(this.totalMs);
      this._setState(nextState(this.state, "COMPLETE"));
      return;
    }

    const currentIdx = this._actionCursor + 1;
    const entry = this.entries[currentIdx];
    if (entry.endMs <= target) {
      // Finished by the clock, but catch-up ran out of frame budget before
      // reaching it this frame -- leave it for next frame's catch-up loop
      // rather than rendering it as "partial".
    } else {
      if (currentIdx !== this._lastCurrentIdx) {
        this._moveStartPos = null;
        this._moveControlPoint = null;
        this._lastCurrentIdx = currentIdx;
      }
      const fraction = entry.endMs > entry.startMs ? (target - entry.startMs) / (entry.endMs - entry.startMs) : 1;
      this._renderPartial(currentIdx, entry, Math.min(1, Math.max(0, fraction)));
    }

    this._emitProgress(target);
    this._scheduleNext();
  }

  _completeInstant(idx, entry) {
    const { action } = entry;
    switch (action.type) {
      case "move":
        this.position = [action.x, action.y];
        break;
      case "draw": {
        const stroke = this.strokesById.get(action.stroke_id);
        this._logDrawStart(idx, stroke);
        this._setCurrentStage(stroke.stage);
        this.graphite.renderStrokeComplete(stroke);
        this.penState = PenState.DOWN;
        this.position = stroke.points[stroke.points.length - 1];
        break;
      }
      case "lift":
        this.penState = PenState.UP;
        break;
      case "pause":
        break;
      default:
        break;
    }
    this._updateTip(action.type === "pause");
  }

  _renderPartial(idx, entry, fraction) {
    const { action } = entry;
    switch (action.type) {
      case "move": {
        if (!this._moveStartPos) {
          this._moveStartPos = [...this.position];
          this._moveControlPoint = this._computeTravelBowControlPoint(
            this._moveStartPos,
            [action.x, action.y],
            idx
          );
        }
        const eased = ease("travel", fraction);
        this.position = quadraticBezierPoint(
          this._moveStartPos,
          this._moveControlPoint,
          [action.x, action.y],
          eased
        );
        break;
      }
      case "draw": {
        const stroke = this.strokesById.get(action.stroke_id);
        this._logDrawStart(idx, stroke);
        this._setCurrentStage(stroke.stage);
        const category = classifyStroke(stroke);
        const eased = ease(category, fraction);
        const total = this.graphite.totalLengthOf(stroke);
        const targetLen = total * eased;
        this.graphite.renderStrokeUpTo(stroke, targetLen);
        this.penState = PenState.DOWN;
        this.position = this.graphite.positionAtProgress(stroke, targetLen);
        break;
      }
      case "lift":
        this.penState = PenState.UP;
        break;
      case "pause":
        break;
      default:
        break;
    }
    this._updateTip(action.type === "pause");
  }

  /**
   * A small, bounded perpendicular bow for pen-up travel (section 1/2:
   * "allow subtle curved travel paths... but keep motion restrained and
   * efficient"). Deterministic per move-action index via `this.seed`, so
   * it never distorts actual stroke geometry (strokes are never touched --
   * this only applies to travel, which deposits no graphite) and is fully
   * reproducible.
   */
  _computeTravelBowControlPoint(p0, p1, actionIndex) {
    const dx = p1[0] - p0[0];
    const dy = p1[1] - p0[1];
    const dist = Math.hypot(dx, dy);
    const mid = [(p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2];
    if (dist < 1e-6) return mid;
    const nx = -dy / dist;
    const ny = dx / dist;
    const sign = travelBowSign(this.seed, actionIndex);
    const magnitude =
      Math.min(MAX_TRAVEL_BOW_PX, dist * TRAVEL_BOW_RATIO) *
      travelBowMagnitudeFraction(this.seed, actionIndex);
    return [mid[0] + nx * magnitude * sign, mid[1] + ny * magnitude * sign];
  }

  _logDrawStart(idx, stroke) {
    if (this._loggedDraws.has(idx)) return;
    this._loggedDraws.add(idx);
    if (this.onDrawStart) {
      this.onDrawStart({ index: this._drawOrdinal, total: this._totalDrawActions, stroke });
    }
    this._drawOrdinal += 1;
  }

  /**
   * @param {boolean} isPausing true while a "pause" action is executing --
   *   an artist evaluating progress has lifted the pencil even if the
   *   contractual pen-contact state (set only by lift/draw actions) still
   *   says "down" from the stroke just finished.
   */
  _updateTip(isPausing = false) {
    const [px, py] = this._lastTipPos || this.position;
    const [x, y] = this.position;
    const angle = Math.atan2(y - py, x - px) || this._lastAngle || 0;
    this._lastAngle = angle;
    this._lastTipPos = [x, y];
    this.tip.clear(this.plan.width, this.plan.height);
    if (this.state === States.PLAYING) {
      const visuallyDown = this.penState === PenState.DOWN && !isPausing;
      this.tip.draw(x, y, angle, visuallyDown);
    }
  }

  _emitProgress(elapsedMs) {
    if (this.onProgress) this.onProgress({ elapsedMs, totalMs: this.totalMs });
  }
}
