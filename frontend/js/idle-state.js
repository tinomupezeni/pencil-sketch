// Pure auto-hide-controls state: when should the secondary playback
// controls be visible? No DOM here -- main.js wires mouse/touch/keyboard
// events to recordActivity()/setFocusWithinControls() and polls
// shouldShowControls() on a timer.
export function createIdleState({ idleTimeoutMs = 2500 } = {}) {
  return { idleTimeoutMs, lastActivityAt: 0, focusWithinControls: false };
}

export function recordActivity(state, nowMs) {
  state.lastActivityAt = nowMs;
}

export function setFocusWithinControls(state, value) {
  state.focusWithinControls = value;
}

/** Controls never hide while keyboard focus is inside them, and stay
 * visible for `idleTimeoutMs` after the last recorded activity. */
export function shouldShowControls(state, nowMs) {
  if (state.focusWithinControls) return true;
  return nowMs - state.lastActivityAt < state.idleTimeoutMs;
}
