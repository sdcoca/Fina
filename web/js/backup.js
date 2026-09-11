// web/js/backup.js
//
// WP-17 -- backup/export (storage-eviction mitigation, required not optional; see this WP's own
// plan entry and docs/plan/mobile-pwa-shell.md sections 1.4b/2.4.3). Any browser can evict
// IndexedDB under storage pressure (iOS Safari's 7-day cap for a non-installed origin is the
// sharpest concrete case, but Android/Chrome is not exempt either) -- and under WP-15's Q-M
// "persistent library" model, that is unrecoverable data loss with no server copy anywhere
// (CLAUDE.md rule 18: this app never has a server copy to fall back to, by design). This module
// is the mitigation: a single "export everything" archive the user saves wherever they choose
// on their own device, and a restore path that feeds it back through exactly the same
// sniff-before-run check a fresh file pick already goes through.
//
// "Export backup" reads every `rawFiles` record (bytes included, active AND inactive alike --
// `storage.getAllFilesWithBytes()`) and hands them to `bridge.py`'s `export_backup()`, which
// builds the zip archive via Python's stdlib `zipfile` (WP-17's own explicit instruction: no
// second, JS-side zip implementation anywhere in this file). The resulting bytes are saved
// through the browser's ordinary download mechanism, with the Web Share API tried first as a
// progressive enhancement where the platform actually supports sharing a file.
//
// "Restore from backup" reads a picked archive's bytes, unzips it via `bridge.py`'s
// `import_backup()`, then re-sniffs every returned entry through the exact same `sniffFile()`
// path `web/js/import.js` uses for a fresh pick -- the archive's own stored `recognizedAs` is
// never trusted blindly (belt and suspenders, not either/or: `bridge.py`'s manifest keeps it as
// a courtesy record, but this module treats it as advisory only). Only entries that still sniff
// as recognized are upserted into `storage.js`, via the same content-hash-deduped
// `addRawFile()` a fresh pick uses -- so restoring a backup that overlaps with already-present
// files is a safe no-op for those, never a duplicate row.

import { exportBackup, importBackup, sniffFile } from "./pyodide-bridge.js";
import * as storage from "./storage.js";

/**
 * Filename extension used for exported backups. The archive is a completely ordinary zip file
 * (built via Python's stdlib `zipfile`, DEFLATE-compressed) -- there is no proprietary format to
 * hide behind a made-up extension, so the plain, honest `.zip` is used; a user can inspect or
 * extract it with any ordinary zip tool if they ever need to, independent of Fina itself.
 */
export const BACKUP_EXTENSION = ".zip";

function _backupFilename() {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  return `fina-backup-${stamp}${BACKUP_EXTENSION}`;
}

function _downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoked on a delay rather than immediately: `click()` returning does not guarantee the
  // browser has already started reading the blob URL, so revoking synchronously here has been
  // known to race the download start in some engines.
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

/**
 * Downloads `blob` as `filename` through an anchor built directly in `win`'s own document (a
 * window opened earlier, before any async work -- see this function's callers), rather than an
 * anchor in the current page's document.
 *
 * Why this exists (a real bug found on a real device, not a hypothetical): `exportBackupToFile`
 * below must `await` real async work (reading rawFiles from IndexedDB, then Pyodide building the
 * zip via bridge.py) before any bytes exist to download. On a real mobile browser, a plain
 * `anchor.click()` issued *after* that await -- even a fast one, appended to the current page's
 * own `<body>` -- was observed to be silently ignored: no download, no error, no exception
 * thrown, nothing for the user to act on beyond "I tap Export backup and nothing happens." The
 * browser's own download/popup safeguards require the triggering action to still be tied to a
 * "fresh" user gesture, and that gesture had already expired by the time the async work finished.
 *
 * The fix: `win` is opened *synchronously*, as the very first statement in `exportBackupToFile`
 * -- an async function's body runs synchronously up to its first `await`, so that `window.open`
 * call is still part of the original click's own call stack and gesture, exactly like any other
 * `window.open()` a click handler might call directly. A window opened this way keeps its own
 * activation independent of the opener's, so building and clicking the anchor *inside that
 * window's own document* (not the opener's) once the bytes are finally ready still works
 * reliably -- confirmed directly against a real download event, not assumed; an earlier attempt
 * at this same fix that instead serialized the anchor via `document.write()` into the popup did
 * NOT reliably trigger a download and was replaced by this direct-DOM-API approach.
 */
function _downloadInWindow(win, blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = win.document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  win.document.body.appendChild(anchor);
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

/**
 * Reads every stored raw file (active and inactive alike) from `storage.js`, builds a backup
 * archive via the Pyodide bridge's `exportBackup()` (Python stdlib `zipfile`), and saves it
 * through the browser's ordinary download mechanism (`URL.createObjectURL` + a programmatic
 * `<a download>` click) -- the baseline "OS share/save sheet" affordance this WP's spec asks
 * for, and the one guaranteed to work across every target browser.
 *
 * Also tries the Web Share API (`navigator.share` with a real `File`) first, as a progressive
 * enhancement, wherever the platform actually supports sharing a file -- a true OS share-sheet
 * feel where it exists. Any failure there (unsupported, declined, cancelled) falls back to the
 * anchor-download above rather than leaving the user without a saved backup.
 *
 * @returns {Promise<number>} how many stored files were included in the archive (0 means there
 *   was nothing stored yet -- the archive is still produced and downloaded, just empty).
 */
export async function exportBackupToFile() {
  // Opened synchronously, as the very first statement -- see _downloadInWindow's own docstring
  // for exactly why. `window.open` can itself return null (a popup blocker, or a browser/context
  // that simply doesn't support it) -- handled below by falling back to an in-page anchor
  // download, which is what this function did unconditionally before this fix.
  const backupWindow =
    typeof window !== "undefined" && typeof window.open === "function"
      ? window.open("", "_blank")
      : null;

  const records = await storage.getAllFilesWithBytes();
  const zipBytes = await exportBackup(
    records.map((r) => ({
      filename: r.filename,
      bytes: r.bytes,
      recognizedAs: r.recognizedAs,
      active: r.active,
    }))
  );
  const blob = new Blob([zipBytes], { type: "application/zip" });
  const filename = _backupFilename();

  // The Web Share API is tried first as a progressive enhancement -- but it strictly requires
  // "transient" user activation (spec, not this app's own choice), which the awaits above have
  // near-certainly already consumed by this point on a real device. Kept anyway for the rare
  // case a fast Pyodide/zip step leaves it still valid; any failure (including the now-expected
  // "must be handling a user gesture" rejection) falls straight through to the window opened
  // above, never leaving the user with a silently-do-nothing button.
  if (typeof navigator !== "undefined" && typeof navigator.canShare === "function") {
    try {
      const file = new File([blob], filename, { type: "application/zip" });
      if (navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title: filename });
        if (backupWindow && !backupWindow.closed) {
          backupWindow.close();
        }
        return records.length;
      }
    } catch {
      // Share was cancelled, declined, or (the expected/common case here) failed because the
      // user gesture had already expired -- fall through to the window-based download below.
    }
  }

  if (backupWindow && !backupWindow.closed) {
    _downloadInWindow(backupWindow, blob, filename);
  } else {
    // window.open was blocked or unsupported -- the plain in-page anchor trigger is what this
    // function always did before this fix; still correct when the gesture genuinely does carry
    // through (desktop browsers are generally far more lenient here than mobile ones), and the
    // best remaining option when there is no open window to write into.
    _downloadBlob(blob, filename);
  }
  return records.length;
}

/**
 * Reads `file` (a picked backup archive), unzips it via the Pyodide bridge's `importBackup()`,
 * then re-sniffs every returned entry through the same `sniffFile()` path a fresh pick uses --
 * WP-17's explicit instruction: never trust the archive's own stored `recognizedAs` blindly.
 * Only entries that still sniff as recognized are upserted into `storage.js` (via `addRawFile`,
 * so an overlapping restore is a safe no-op for those, never a duplicate); each restored
 * record's `active` flag is taken from the archive (Q-M's persistent-library model makes
 * active/inactive state meaningfully part of what a backup should preserve, not only the bytes).
 * An entry that no longer sniffs as any known shape (a foreign or corrupted archive, or a file
 * whose shape no adapter recognizes any more) is reported back as rejected, never silently
 * dropped without being surfaced to the caller.
 *
 * @param {File} file
 * @returns {Promise<{
 *   restored: Array<{filename: string, adapter: string}>,
 *   rejected: Array<{filename: string, reason: string}>
 * }>}
 */
export async function restoreFromFile(file) {
  const zipBytes = new Uint8Array(await file.arrayBuffer());

  let entries;
  try {
    entries = await importBackup(zipBytes);
  } catch (err) {
    throw new Error(
      `"${file.name}" doesn't look like a Fina backup archive: ` +
        (err && err.message ? err.message : String(err))
    );
  }

  const restored = [];
  const rejected = [];
  for (const entry of entries) {
    const bytes = entry.bytes instanceof Uint8Array ? entry.bytes : new Uint8Array(entry.bytes);
    const probeFile = new File([bytes], entry.filename);
    let adapter = null;
    try {
      adapter = await sniffFile(probeFile);
    } catch {
      adapter = null;
    }
    if (!adapter) {
      rejected.push({
        filename: entry.filename,
        reason:
          "This file's shape is no longer recognized (the backup may be corrupted, or from " +
          "an export type Fina no longer supports) and was not restored.",
      });
      continue;
    }
    await storage.addRawFile({
      filename: entry.filename,
      bytes,
      recognizedAs: adapter,
      active: entry.active,
    });
    restored.push({ filename: entry.filename, adapter });
  }

  return { restored, rejected };
}

// Set once this module has finished loading and parsing -- same load-time sanity-check
// convention storage.js/pyodide-bridge.js/app.js already established.
if (typeof window !== "undefined") {
  window.__finaBackupJsLoaded = true;
}
