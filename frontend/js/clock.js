// A monotonic virtual playhead: how far into the (scaled) timeline
// playback has progressed, in ms. Pure function, unit-testable.
//
// `pausedAccumMs` is the playhead value frozen at the moment of the most
// recent pause/start; `playStartPerf` is the `now()` timestamp at the
// moment playback last started/resumed. While playing, the playhead is
// pausedAccumMs + elapsed real time since that moment -- so a pause/resume
// cycle never loses or duplicates elapsed time (no drift), and a
// backgrounded/throttled tab simply means fewer calls, not a wrong value:
// whenever this *is* next called, it reports the correct elapsed time,
// regardless of how long it's been since the last call.
import { States } from "./playback-state.js";

export function computePlayheadMs({ state, pausedAccumMs, playStartPerf, nowPerf }) {
  if (state !== States.PLAYING) return pausedAccumMs;
  return pausedAccumMs + (nowPerf - playStartPerf);
}
