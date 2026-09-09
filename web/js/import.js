// web/js/import.js
//
// WP-13, PWA-3.3 ("sniff before running"): the sniff-before-run filtering logic. Given a
// FileList/File[] from the picker, this calls `sniffFile()` (WP-12's bridge) on each file
// *individually*, at pick time, before anything reaches the full pipeline. A file whose shape
// matches no known adapter is rejected on its own -- it never gets anywhere near `runBuild()`
// and it never causes a recognized sibling file in the same multi-pick to be skipped. This is
// the module that makes R-11.4's "one bad file aborts the whole run" rule apply only to what it
// is actually meant to guard (a *recognized* file whose *data* fails inside the pipeline), not
// to an unrelated file that merely happened to be picked alongside good ones.
//
// This module owns no DOM: it is pure partitioning logic over File objects, so it can be
// exercised without a document (and so `app.js` alone decides how rejections are rendered).

/**
 * The rejection message shown for a file that matched no known adapter shape, per WP-13's
 * spec (PWA-3.3) verbatim -- deliberately not generated from adapter internals, so the copy
 * never silently drifts if a new adapter name changes.
 */
export const UNRECOGNIZED_FILE_MESSAGE =
  "This file doesn't look like a bank or broker export Fina recognizes. Supported today: " +
  "a Trade Republic CSV export, or a Spanish bank XLSX export.";

/**
 * @typedef {{file: File, reason: string}} RejectedFile
 * @typedef {{recognized: File[], rejected: RejectedFile[]}} Partition
 */

/**
 * Sniffs every file in `fileList` individually (via `sniffFile`, WP-12) and partitions them
 * into files that matched a known adapter shape ("recognized", handed to `runBuild()` by the
 * caller) and files that did not ("rejected", never handed to `runBuild()` at all).
 *
 * `sniffFile()` never throws for an unrecognized file (that is its whole contract -- it
 * returns `null`), but this still guards each call defensively: a file this function cannot
 * even sniff (e.g. a read error) is treated as rejected, never as a reason to abort sniffing
 * the rest of the pick.
 *
 * @param {FileList | File[]} fileList
 * @param {(file: File) => Promise<string | null>} sniffFile injected for testability; defaults
 *   to the real `sniffFile` from `pyodide-bridge.js` when omitted.
 * @returns {Promise<Partition>}
 */
export async function sniffAndPartition(fileList, sniffFile) {
  if (sniffFile === undefined) {
    ({ sniffFile } = await import("./pyodide-bridge.js"));
  }

  const files = Array.from(fileList);
  /** @type {File[]} */
  const recognized = [];
  /** @type {RejectedFile[]} */
  const rejected = [];

  for (const file of files) {
    let adapter = null;
    try {
      adapter = await sniffFile(file);
    } catch {
      adapter = null;
    }
    if (adapter) {
      recognized.push(file);
    } else {
      rejected.push({ file, reason: UNRECOGNIZED_FILE_MESSAGE });
    }
  }

  return { recognized, rejected };
}
