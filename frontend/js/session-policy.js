// Pure decision logic for session recovery (Phase 8, section 8). Kept
// separate from the actual IndexedDB glue (session-store.js) so the
// "is this saved session still worth offering to resume" policy is
// testable without a browser/IndexedDB.
export const MAX_SESSION_AGE_MS = 24 * 60 * 60 * 1000; // bounded retention: 24 hours

/**
 * @param {unknown} saved whatever was read back from storage (or null/undefined)
 * @param {number} nowMs
 * @returns {boolean} whether this record is well-formed and fresh enough to offer
 */
export function isRecoverable(saved, nowMs, maxAgeMs = MAX_SESSION_AGE_MS) {
  if (!saved || typeof saved !== "object") return false;
  if (typeof saved.savedAt !== "number" || !Number.isFinite(saved.savedAt)) return false;
  if (nowMs - saved.savedAt > maxAgeMs) return false;
  if (nowMs < saved.savedAt) return false; // clock skew / corrupted record
  if (typeof saved.elapsedMs !== "number" || !Number.isFinite(saved.elapsedMs) || saved.elapsedMs < 0) return false;
  if (typeof saved.targetDurationMs !== "number" || !(saved.targetDurationMs > 0)) return false;
  if (!saved.plan || !Array.isArray(saved.plan.strokes) || !Array.isArray(saved.plan.actions)) return false;
  return true;
}

/** How long ago (for a human-readable "resume from X ago" message). */
export function ageDescription(saved, nowMs) {
  const ageMs = Math.max(0, nowMs - saved.savedAt);
  const minutes = Math.round(ageMs / 60000);
  if (minutes < 1) return "moments ago";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  return `${hours} hour${hours === 1 ? "" : "s"} ago`;
}

export function buildSessionRecord({ plan, elapsedMs, targetDurationMs, nowMs }) {
  return { plan, elapsedMs, targetDurationMs, savedAt: nowMs };
}
