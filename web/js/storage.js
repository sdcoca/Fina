// web/js/storage.js
//
// WP-15 (Q-M, "persistent library"): cross-session, on-device persistence for raw imported
// files, the last successful run's cache, and small settings -- via the browser's own
// `indexedDB` API (NOT Pyodide's Emscripten-backed `IDBFS`). See this WP's final report for
// the full reasoning; in short: the plan's own §2.2 says the three-store schema is "a name and
// a set of fields, not a commitment to exact IndexedDB API mechanics", and this file picks the
// implementation that keeps IndexedDB persistence entirely in JS-land, independent of whether
// Pyodide has booted yet -- a file picked before Pyodide finishes loading is still durably
// saved the instant it is recognized (app.js only needs a `sniffFile()` call to complete, which
// itself only needs Pyodide -- storage writes themselves never do).
//
// Three object stores, one database, matching `docs/plan/mobile-pwa-shell.md` §2.2 verbatim:
//
//   - `rawFiles`  -- one record per distinct imported file, keyed by its own SHA-256 content
//                    hash (`id`), computed here via the Web Crypto API
//                    (`crypto.subtle.digest("SHA-256", ...)`) -- never a JS reimplementation of
//                    a different hash, and the same identity fact
//                    `fina.io_utils.sha256_of_file` computes server-side (conceptually) for the
//                    same file's bytes. Re-adding a file whose content hash already exists is a
//                    pure no-op (§2.3's "not a duplicate row" rule) -- this module never
//                    silently mutates an existing record's `active` flag or bytes as a side
//                    effect of a re-pick.
//   - `runCache`  -- at most one record (fixed key `"current"`), the last *successful* run's
//                    manifest/chart/summary. This module never decides success or failure --
//                    `setRunCache`/`clearRunCache` are only ever called by `app.js` after it has
//                    already confirmed `bridge.run()`'s own `ok` field, per WP-13's established
//                    pattern of `app.js` owning orchestration (§2.4.2).
//   - `settings`  -- flat key/value rows, nothing financial.
//
// This module parses/stores `manifest_json`/`chart_html`/summary figures as opaque
// strings/blobs only -- it never parses a money figure into a JS Number (CLAUDE.md rule 9's
// intent, extended to this layer, restated from app.js's own header comment).

const DB_NAME = "fina";
const DB_VERSION = 1;

const STORE_RAW_FILES = "rawFiles";
const STORE_RUN_CACHE = "runCache";
const STORE_SETTINGS = "settings";

// runCache has exactly one live record at a time -- a fixed key, not a growing history.
const RUN_CACHE_KEY = "current";

let _dbPromise = null;

function _openDb() {
  if (_dbPromise !== null) {
    return _dbPromise;
  }
  _dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_RAW_FILES)) {
        const rawFiles = db.createObjectStore(STORE_RAW_FILES, { keyPath: "id" });
        rawFiles.createIndex("active", "active", { unique: false });
      }
      if (!db.objectStoreNames.contains(STORE_RUN_CACHE)) {
        db.createObjectStore(STORE_RUN_CACHE, { keyPath: "key" });
      }
      if (!db.objectStoreNames.contains(STORE_SETTINGS)) {
        db.createObjectStore(STORE_SETTINGS, { keyPath: "key" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return _dbPromise;
}

function _promisifyRequest(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function _txDone(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("IndexedDB transaction aborted"));
  });
}

/**
 * SHA-256 of `bytes` (a `Uint8Array`/`ArrayBuffer`), as lowercase hex -- the same identity
 * scheme `fina.io_utils.sha256_of_file` uses conceptually, computed browser-side via the Web
 * Crypto API rather than a hand-rolled JS hash.
 *
 * @param {Uint8Array | ArrayBuffer} bytes
 * @returns {Promise<string>}
 */
export async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// ---------------------------------------------------------------------------
// rawFiles
// ---------------------------------------------------------------------------

/**
 * Upserts one raw file record, keyed by the SHA-256 hash of `bytes`.
 *
 * If a record with the same content hash already exists, this is a **pure no-op** -- nothing
 * is changed, including `active` (re-picking a file the user has since marked inactive must
 * never silently reactivate it as a side effect of the pick landing on the same bytes; the
 * checklist toggle is the only path that changes `active`). This matches §2.3's "re-selecting
 * the same export twice is a no-op, not a second row."
 *
 * @param {{filename: string, bytes: Uint8Array, recognizedAs: string | null, active?: boolean,
 *   importedAt?: string}} file
 * @returns {Promise<string>} the record's `id` (content hash), whether newly inserted or not.
 */
export async function addRawFile({ filename, bytes, recognizedAs, active = true, importedAt }) {
  const id = await sha256Hex(bytes);
  const db = await _openDb();
  const tx = db.transaction([STORE_RAW_FILES], "readwrite");
  const store = tx.objectStore(STORE_RAW_FILES);
  const existing = await _promisifyRequest(store.get(id));
  if (existing === undefined) {
    store.put({
      id,
      filename,
      bytes: new Blob([bytes]),
      recognizedAs: recognizedAs ?? null,
      active,
      importedAt: importedAt ?? new Date().toISOString(),
      size: bytes.byteLength ?? bytes.length,
    });
  }
  await _txDone(tx);
  return id;
}

/**
 * Every stored raw file's metadata (never its `bytes` -- callers that need bytes use
 * `getActiveFiles()`), for rendering the active/inactive checklist. Order is not significant;
 * callers sort for display as they see fit.
 *
 * @returns {Promise<Array<{id: string, filename: string, recognizedAs: string | null,
 *   active: boolean, importedAt: string, size: number}>>}
 */
export async function listRawFiles() {
  const db = await _openDb();
  const tx = db.transaction([STORE_RAW_FILES], "readonly");
  const store = tx.objectStore(STORE_RAW_FILES);
  const all = await _promisifyRequest(store.getAll());
  await _txDone(tx);
  return all.map(({ id, filename, recognizedAs, active, importedAt, size }) => ({
    id,
    filename,
    recognizedAs,
    active,
    importedAt,
    size,
  }));
}

/**
 * Sets one stored file's `active` flag without deleting or otherwise touching the record
 * (§2.3's refinement: exclude from a run without losing the file).
 *
 * @param {string} id
 * @param {boolean} active
 * @returns {Promise<void>}
 */
export async function setActive(id, active) {
  const db = await _openDb();
  const tx = db.transaction([STORE_RAW_FILES], "readwrite");
  const store = tx.objectStore(STORE_RAW_FILES);
  const existing = await _promisifyRequest(store.get(id));
  if (existing === undefined) {
    await _txDone(tx);
    throw new Error(`storage.js: setActive() called for unknown rawFiles id ${id}`);
  }
  existing.active = active;
  store.put(existing);
  await _txDone(tx);
}

/**
 * The active subset of `rawFiles`, with real bytes -- this is a run's actual input set under
 * the "persistent library" model (§2.3): every stored file marked `active`, not only whatever
 * was picked in the current browser session.
 *
 * @returns {Promise<Array<{id: string, filename: string, recognizedAs: string | null,
 *   bytes: Uint8Array}>>}
 */
export async function getActiveFiles() {
  const db = await _openDb();
  const tx = db.transaction([STORE_RAW_FILES], "readonly");
  const store = tx.objectStore(STORE_RAW_FILES);
  const all = await _promisifyRequest(store.getAll());
  await _txDone(tx);
  const active = all.filter((record) => record.active);
  const out = [];
  for (const record of active) {
    const buf = await record.bytes.arrayBuffer();
    out.push({
      id: record.id,
      filename: record.filename,
      recognizedAs: record.recognizedAs,
      bytes: new Uint8Array(buf),
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// runCache -- rebuildable cache only (§2.2.2/§2.4.2). Never decides success/failure itself;
// the caller (app.js) only calls setRunCache() after it has already confirmed bridge.run()
// succeeded, and must simply not call setRunCache()/clearRunCache() at all on a failed run so
// the previous record -- if any -- survives untouched.
// ---------------------------------------------------------------------------

/**
 * The current cached run record, or `null` if none exists yet.
 *
 * @returns {Promise<object | null>}
 */
export async function getRunCache() {
  const db = await _openDb();
  const tx = db.transaction([STORE_RUN_CACHE], "readonly");
  const store = tx.objectStore(STORE_RUN_CACHE);
  const record = await _promisifyRequest(store.get(RUN_CACHE_KEY));
  await _txDone(tx);
  return record ?? null;
}

/**
 * Replaces the single `runCache` record wholesale. `record` should NOT include `key` --
 * this function sets it to the fixed `RUN_CACHE_KEY` itself, so callers never need to know the
 * key's exact value.
 *
 * Callers must only call this after confirming their own run succeeded -- this module has no
 * way to check that itself, matching WP-13's established pattern of `app.js` owning
 * orchestration, not `storage.js`/`pyodide-bridge.js`.
 *
 * @param {object} record e.g. `{ activeIds: string[], warnings, summary, manifestJson,
 *   chartHtml, computedAt }`.
 * @returns {Promise<void>}
 */
export async function setRunCache(record) {
  const db = await _openDb();
  const tx = db.transaction([STORE_RUN_CACHE], "readwrite");
  tx.objectStore(STORE_RUN_CACHE).put({ ...record, key: RUN_CACHE_KEY });
  await _txDone(tx);
}

/**
 * Deletes the current `runCache` record, if any. Like `setRunCache`, only ever called by a
 * caller that has already made its own success/failure determination.
 *
 * @returns {Promise<void>}
 */
export async function clearRunCache() {
  const db = await _openDb();
  const tx = db.transaction([STORE_RUN_CACHE], "readwrite");
  tx.objectStore(STORE_RUN_CACHE).delete(RUN_CACHE_KEY);
  await _txDone(tx);
}

// ---------------------------------------------------------------------------
// settings -- flat key/value rows only, nothing financial.
// ---------------------------------------------------------------------------

/**
 * @param {string} key
 * @returns {Promise<*>} the stored value, or `undefined` if `key` has never been set.
 */
export async function getSetting(key) {
  const db = await _openDb();
  const tx = db.transaction([STORE_SETTINGS], "readonly");
  const store = tx.objectStore(STORE_SETTINGS);
  const record = await _promisifyRequest(store.get(key));
  await _txDone(tx);
  return record === undefined ? undefined : record.value;
}

/**
 * @param {string} key
 * @param {*} value
 * @returns {Promise<void>}
 */
export async function setSetting(key, value) {
  const db = await _openDb();
  const tx = db.transaction([STORE_SETTINGS], "readwrite");
  tx.objectStore(STORE_SETTINGS).put({ key, value });
  await _txDone(tx);
}

// Set once this module has finished loading and parsing -- same load-time sanity-check
// convention `pyodide-bridge.js`/`app.js` already established.
if (typeof window !== "undefined") {
  window.__finaStorageJsLoaded = true;
}
