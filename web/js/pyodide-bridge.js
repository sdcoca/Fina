// web/js/pyodide-bridge.js
//
// WP-12: thin JS wrapper that boots the vendored Pyodide runtime + wheels (WP-11) and exposes
// a small, `File`-shaped API backed by `web/py/bridge.py`, which runs completely unmodified
// inside that runtime -- this file contains no financial logic of its own, only plumbing.
//
// Loading sequence mirrors the pattern `tests/test_pyodide_vendor.py` already proved offline
// (WP-11): a global `loadPyodide()` (from a classic `<script src="./vendor/pyodide/pyodide.js">`
// tag loaded by the page *before* this module, since Pyodide's own loader is not itself an ES
// module) -> `pyodide.loadPackage()` for the vendored `micropip` wheel -> `micropip.install(
// [...], deps=False)` against the vendored `openpyxl`/`et_xmlfile`/`fina` wheel paths. Nothing
// here ever fetches from PyPI or a CDN; every path below is local to this same origin.
//
// Callers (WP-13's shell): `import { sniffFile, runBuild } from "./pyodide-bridge.js"`.

// Resolved relative to this module's own URL (like BRIDGE_PY_URL below), never root-relative --
// a root-relative "/vendor" is only correct when the app is served from the origin's root,
// which is false for a GitHub Pages *project* site (served under "/<repo-name>/"). A real
// deployment under such a subpath surfaced this exact bug: every absolute "/..." path in this
// file/index.html/manifest.json/sw-register.js 404'd, so picking a file did nothing (Pyodide
// never finished booting, silently, since nothing here surfaced that failure to the UI either).
const VENDOR_BASE = new URL("../vendor", import.meta.url).href;
const PYODIDE_INDEX_URL = `${VENDOR_BASE}/pyodide/`;
const WHEEL_PATHS = {
  micropip: `${VENDOR_BASE}/wheels/micropip-0.11.1-py3-none-any.whl`,
  etXmlfile: `${VENDOR_BASE}/wheels/et_xmlfile-2.0.0-py3-none-any.whl`,
  openpyxl: `${VENDOR_BASE}/wheels/openpyxl-3.1.5-py2.py3-none-any.whl`,
  fina: `${VENDOR_BASE}/wheels/fina-0.1.0-py3-none-any.whl`,
};

// Resolved relative to this module's own URL so the page can mount it at any path (e.g.
// "/js/pyodide-bridge.js") without this file needing to know its own absolute location.
const BRIDGE_PY_URL = new URL("../py/bridge.py", import.meta.url).href;

let _pyodidePromise = null;

async function _bootPyodide() {
  if (typeof loadPyodide !== "function") {
    throw new Error(
      "pyodide-bridge.js: the global loadPyodide() function is not defined -- the page must " +
        'load "./vendor/pyodide/pyodide.js" with a classic <script> tag before this module ' +
        "runs (Pyodide's own loader script is not an ES module and cannot be imported here)."
    );
  }
  const pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX_URL });

  // The vendored micropip wheel's own sha256 intentionally does not match Pyodide's
  // pyodide-lock.json (see web/vendor/build.py's docstring) -- load it as an ordinary local
  // wheel, not a lock-verified package.
  await pyodide.loadPackage(WHEEL_PATHS.micropip, { checkIntegrity: false });

  await pyodide.runPythonAsync(`
import micropip
await micropip.install([
    ${JSON.stringify(WHEEL_PATHS.etXmlfile)},
    ${JSON.stringify(WHEEL_PATHS.openpyxl)},
    ${JSON.stringify(WHEEL_PATHS.fina)},
], deps=False)
`);

  const bridgeSource = await (await fetch(BRIDGE_PY_URL)).text();
  pyodide.FS.writeFile("/bridge.py", bridgeSource);
  await pyodide.runPythonAsync(`
import sys
if "/" not in sys.path:
    sys.path.insert(0, "/")
import bridge
`);

  return pyodide;
}

async function _getPyodide() {
  if (_pyodidePromise === null) {
    _pyodidePromise = _bootPyodide();
  }
  return _pyodidePromise;
}

async function _fileToUint8Array(file) {
  return new Uint8Array(await file.arrayBuffer());
}

/**
 * Which adapter's shape `file`'s bytes match, or `null` if none does. Never throws for an
 * unrecognized file -- mirrors `fina.pipeline.sniff_adapter_name`'s own contract, so WP-13's
 * shell can reject one bad file in a multi-pick without aborting the others.
 *
 * @param {File} file
 * @returns {Promise<string|null>}
 */
export async function sniffFile(file) {
  const pyodide = await _getPyodide();
  const bytes = await _fileToUint8Array(file);
  const bridge = pyodide.pyimport("bridge");
  try {
    const result = bridge.sniff(file.name, bytes);
    return result === undefined ? null : result;
  } finally {
    bridge.destroy();
  }
}

/**
 * Runs the full pipeline over `files` -- `fina.cli._build`'s exact steps, reproduced by
 * `bridge.run()` -- and returns its structured result:
 * `{ ok, warnings, summary, manifest_json, chart_html, error }` (see `web/py/bridge.py`'s
 * `run()` docstring for the exact shape of each field).
 *
 * @param {File[]} files
 * @returns {Promise<object>}
 */
export async function runBuild(files) {
  const pyodide = await _getPyodide();
  const bridge = pyodide.pyimport("bridge");
  const activeFiles = [];
  for (const file of files) {
    activeFiles.push({ filename: file.name, data: await _fileToUint8Array(file) });
  }
  try {
    const pyResult = bridge.run(activeFiles);
    try {
      return pyResult.toJs({ dict_converter: Object.fromEntries });
    } finally {
      pyResult.destroy();
    }
  } finally {
    bridge.destroy();
  }
}

/**
 * WP-17: builds a single backup archive (a plain zip, via `bridge.py`'s stdlib-`zipfile`-backed
 * `export_backup` -- never a second, JS-side zip implementation) over `files`, and returns its
 * raw bytes.
 *
 * @param {Array<{filename: string, bytes: Uint8Array, recognizedAs: string | null,
 *   active: boolean}>} files
 * @returns {Promise<Uint8Array>} the zip archive's raw bytes.
 */
export async function exportBackup(files) {
  const pyodide = await _getPyodide();
  const bridge = pyodide.pyimport("bridge");
  try {
    const result = bridge.export_backup(files);
    try {
      return result.toJs();
    } finally {
      result.destroy();
    }
  } finally {
    bridge.destroy();
  }
}

/**
 * WP-17: reverses `exportBackup` -- unzips `zipBytes` (via `bridge.py`'s stdlib-`zipfile`-backed
 * `import_backup`) and returns the list of archived entries. Callers (`web/js/backup.js`) must
 * still re-sniff every entry's bytes via `sniffFile()` before trusting/storing it -- this
 * function alone does not re-validate anything, it only reverses the archive format.
 *
 * @param {Uint8Array} zipBytes
 * @returns {Promise<Array<{filename: string, bytes: Uint8Array, recognizedAs: string | null,
 *   active: boolean}>>}
 */
export async function importBackup(zipBytes) {
  const pyodide = await _getPyodide();
  const bridge = pyodide.pyimport("bridge");
  try {
    const result = bridge.import_backup(zipBytes);
    try {
      return result.toJs({ dict_converter: Object.fromEntries });
    } finally {
      result.destroy();
    }
  } finally {
    bridge.destroy();
  }
}

// Set once this module has finished loading and parsing -- used by
// tests/test_pyodide_bridge.py as a load-time sanity check that this file is syntactically
// valid and importable as an ES module inside a real browser, independent of whether any
// Pyodide runtime is ever actually booted in that same test.
if (typeof window !== "undefined") {
  window.__finaPyodideBridgeJsLoaded = true;
}
