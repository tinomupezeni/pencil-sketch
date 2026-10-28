// A minimal in-memory fake of just the IndexedDB surface session-store.js
// uses (open/upgrade/transaction/objectStore put-get-delete), so
// session-store.js's glue code can be tested without a real browser.
export function createFakeIDBFactory({ failOpen = false } = {}) {
  const databases = new Map();

  function makeRequest() {
    return { onsuccess: null, onerror: null, result: undefined, error: null };
  }

  return {
    open(name) {
      const req = makeRequest();
      queueMicrotask(() => {
        if (failOpen) {
          req.error = new Error("blocked");
          req.onerror?.();
          return;
        }
        let entry = databases.get(name);
        const isNew = !entry;
        if (!entry) {
          entry = { stores: new Map() };
          databases.set(name, entry);
        }
        const db = {
          objectStoreNames: { contains: (n) => entry.stores.has(n) },
          createObjectStore(n) {
            entry.stores.set(n, new Map());
            return {};
          },
          transaction(storeName) {
            const map = entry.stores.get(storeName);
            // One object serves as both "the transaction" (whose
            // .oncomplete the caller assigns) and the closure the
            // scheduled microtask reads from -- must be the same
            // reference, not a copy (Object.assign would silently break
            // this: the caller's later `tx.oncomplete = ...` would land on
            // a different object than the one the microtask reads).
            const tx = {
              oncomplete: null,
              onerror: null,
              onabort: null,
              error: null,
              objectStore() {
                return {
                  put(value, key) {
                    const r = makeRequest();
                    queueMicrotask(() => {
                      map.set(key, value);
                      r.result = key;
                      r.onsuccess?.();
                    });
                    return r;
                  },
                  get(key) {
                    const r = makeRequest();
                    queueMicrotask(() => {
                      r.result = map.get(key);
                      r.onsuccess?.();
                    });
                    return r;
                  },
                  delete(key) {
                    const r = makeRequest();
                    queueMicrotask(() => {
                      map.delete(key);
                      r.onsuccess?.();
                    });
                    return r;
                  },
                };
              },
            };
            queueMicrotask(() => queueMicrotask(() => tx.oncomplete?.()));
            return tx;
          },
        };
        req.result = db;
        if (isNew) req.onupgradeneeded?.();
        req.onsuccess?.();
      });
      return req;
    },
  };
}
