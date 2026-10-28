// Playback state machine, kept independent of pencil-contact state (see
// PenState below) -- "paused" vs "playing" is a UI/timeline concept; "pen
// up" vs "pen down" is a property of whichever action is currently
// executing. Pure, no DOM dependency.

export const States = Object.freeze({
  IDLE: "idle",
  LOADING: "loading",
  READY: "ready",
  PLAYING: "playing",
  PAUSED: "paused",
  COMPLETED: "completed",
  ERROR: "error",
});

export const PenState = Object.freeze({
  UP: "up",
  DOWN: "down",
});

// START (re)starts playback from a clean canvas/timeline -- it's the same
// operation whether it's the first play after an upload or a Replay-button
// click, and it's valid from any state that already has a loaded plan.
const TRANSITIONS = {
  [States.IDLE]: { LOAD_START: States.LOADING },
  [States.LOADING]: {
    LOAD_SUCCESS: States.READY,
    LOAD_ERROR: States.ERROR,
    LOAD_START: States.LOADING, // re-uploading while a previous upload is still in flight
  },
  [States.READY]: { START: States.PLAYING, LOAD_START: States.LOADING },
  [States.PLAYING]: {
    START: States.PLAYING,
    PAUSE: States.PAUSED,
    COMPLETE: States.COMPLETED,
    LOAD_START: States.LOADING,
    ERROR: States.ERROR,
  },
  [States.PAUSED]: {
    START: States.PLAYING,
    RESUME: States.PLAYING,
    LOAD_START: States.LOADING,
  },
  [States.COMPLETED]: { START: States.PLAYING, LOAD_START: States.LOADING },
  [States.ERROR]: { LOAD_START: States.LOADING },
};

export function canTransition(state, event) {
  const row = TRANSITIONS[state];
  return !!row && Object.prototype.hasOwnProperty.call(row, event);
}

export function nextState(state, event) {
  if (!canTransition(state, event)) {
    throw new Error(`invalid playback transition: ${event} from ${state}`);
  }
  return TRANSITIONS[state][event];
}
