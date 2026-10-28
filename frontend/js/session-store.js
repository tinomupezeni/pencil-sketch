// IndexedDB-backed storage for one recoverable drawing session (Phase 8,
// section 8). Deliberately NOT localStorage -- a DrawingPlan can be tens
// of megabytes, well past what localStorage can hold synchronously.
// Bounded by construction: exactly one record (key "current"), so saving
// a new session always overwrites the previous one rather than
// accumulating. `idbFactory` is injectable for testing (defaults to the
// real `indexedDB`); every operation resolves to {ok, reason?} or a value
// rather than throwing -- storage errors (quota, private-browsing
// restrictions, unsupported) degrade to "no recovery available", never a
// crash.
import { isRecoverable } from "./session-policy.js";

const DB_NAME = "pencil-sketch-session";
const DB_VERSION = 1;
const STORE_NAME = "sessions";
const RECORD_KEY = "current";

function openDb(idbFactory) {
  return new Promise((resolve, reject) => {
    let request;
    try {
      request = idbFactory.open(DB_NAME, DB_VERSION);
    } catch (err) {
      reject(err);
      return;
    }
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("IndexedDB open failed"));
  });
}

export function createSessionStore({ idbFactory } = {}) {
  const factory = idbFactory ?? (typeof indexedDB !== "undefined" ? indexedDB : null);

  async function run(mode, useStore) {
    if (!factory) return { ok: false, reason: "unsupported" };
    let db;
    try {
      db = await openDb(factory);
    } catch (err) {
      return { ok: false, reason: (err && err.name) || "open-failed" };
    }
    return new Promise((resolve) => {
      let tx;
      try {
        tx = db.transaction(STORE_NAME, mode);
      } catch (err) {
        resolve({ ok: false, reason: (err && err.name) || "transaction-failed" });
        return;
      }
      const store = tx.objectStore(STORE_NAME);
      let value;
      try {
        value = useStore(store);
      } catch (err) {
        resolve({ ok: false, reason: (err && err.name) || "error" });
        return;
      }
      tx.oncomplete = () => resolve({ ok: true, value });
      tx.onerror = () => resolve({ ok: false, reason: (tx.error && tx.error.name) || "error" });
      tx.onabort = () => resolve({ ok: false, reason: "aborted" });
    });
  }

  return {
    /** Saves (overwriting) the single current-session record. */
    async save(record) {
      return run("readwrite", (store) => {
        store.put(record, RECORD_KEY);
      });
    },

    /** Returns the saved record (or null) if it's well-formed and still
     * within the bounded retention window; never throws. */
    async loadIfRecoverable(nowMs) {
      let fetched = null;
      const outcome = await run("readonly", (store) => {
        const req = store.get(RECORD_KEY);
        req.onsuccess = () => {
          fetched = req.result ?? null;
        };
      });
      if (!outcome.ok) return null;
      return isRecoverable(fetched, nowMs) ? fetched : null;
    },

    /** Explicit, user-driven discard (e.g. "start a new drawing" / "dismiss"). */
    async discard() {
      return run("readwrite", (store) => {
        store.delete(RECORD_KEY);
      });
    },
  };
}
