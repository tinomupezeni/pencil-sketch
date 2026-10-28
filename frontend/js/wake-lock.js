// Thin wrapper around the Screen Wake Lock API. `navigatorRef` is
// injectable so this is testable without a real browser; it defaults to
// the real `navigator` when available. Never throws: unsupported browsers
// and a rejected/denied request both resolve to {ok: false, reason}, never
// an exception the caller has to catch.
export class WakeLockController {
  constructor({ navigatorRef } = {}) {
    this._navigator = navigatorRef ?? (typeof navigator !== "undefined" ? navigator : null);
    this._sentinel = null;
    this._wanted = false; // whether the caller currently wants a lock held
  }

  get isSupported() {
    return !!(this._navigator && this._navigator.wakeLock);
  }

  get isActive() {
    return !!this._sentinel;
  }

  /** @returns {Promise<{ok: boolean, reason?: string}>} */
  async request() {
    this._wanted = true;
    if (!this.isSupported) {
      return { ok: false, reason: "unsupported" };
    }
    try {
      const sentinel = await this._navigator.wakeLock.request("screen");
      this._sentinel = sentinel;
      // The OS/browser can release it on its own (e.g. a different app
      // took focus) -- reacquireIfWanted() is how the caller resumes it,
      // typically on the next visibilitychange.
      sentinel.addEventListener?.("release", () => {
        if (this._sentinel === sentinel) this._sentinel = null;
      });
      return { ok: true };
    } catch (err) {
      this._sentinel = null;
      return { ok: false, reason: err && err.name ? err.name : "error" };
    }
  }

  async release() {
    this._wanted = false;
    const sentinel = this._sentinel;
    this._sentinel = null;
    if (sentinel) {
      try {
        await sentinel.release();
      } catch {
        // already released or unreleasable -- nothing more to do
      }
    }
  }

  /** Call on visibilitychange: re-requests only if a lock is still wanted
   * and isn't already held (e.g. the tab was hidden, which auto-releases
   * the lock, then became visible again while playback is still active). */
  async reacquireIfWanted() {
    if (this._wanted && !this._sentinel) {
      return this.request();
    }
    return { ok: this.isActive };
  }
}
