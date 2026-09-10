// web/js/app.js
//
// WP-13: wires the shell together. Picks a FileList from the <input>, runs it through
// import.js's sniff-before-run filtering, hands the recognized subset to runBuild() (WP-12),
// and renders the result -- rejections, warnings, the summary figures, and the chart, embedded
// via a sandboxed <iframe srcdoc="..."> (PWA-5.2a). Contains no financial logic of its own:
// every figure `result.summary` carries is already a rounded display string from
// `web/py/bridge.py` and is only ever concatenated here, never re-parsed/rounded as a JS float
// (CLAUDE.md rule 9's intent, extended to this layer).
//
// WP-15 (Q-M, "persistent library"): every file the app has ever recognized and imported is
// kept in `storage.js`'s `rawFiles` store across sessions, deduplicated by content hash. A run's
// input is now "every stored file marked active" (`storage.getActiveFiles()`), not only what was
// just picked in this browser session -- picking new files adds to that persistent set rather
// than replacing it. `app.js` alone still decides success/failure (WP-13's established pattern):
// `storage.js` never inspects a `bridge.run()` result itself, it only ever gets told, after the
// fact, "here is a record to cache" (`setRunCache`) or nothing at all on failure -- a failed run
// leaves whatever `runCache` record already existed completely untouched (§2.4.2).
//
// WP-17: wires in `backup.js`'s export/restore actions (storage-eviction mitigation). A restore
// that actually adds files follows the exact same write-then-refresh-then-run sequencing
// `handleFiles` below already uses for a fresh pick -- see `handleRestore`.

import { exportBackupToFile, restoreFromFile } from "./backup.js";
import { sniffAndPartition } from "./import.js";
import { runBuild } from "./pyodide-bridge.js";
import * as storage from "./storage.js";

const fileInput = document.getElementById("file-input");
const busyIndicator = document.getElementById("busy-indicator");
const rejectedFilesEl = document.getElementById("rejected-files");
const storedFilesEmptyEl = document.getElementById("stored-files-empty");
const storedFilesListEl = document.getElementById("stored-files-list");
const exportBackupButton = document.getElementById("export-backup-button");
const restoreBackupInput = document.getElementById("restore-backup-input");
const backupStatusEl = document.getElementById("backup-status");
const errorSection = document.getElementById("error-section");
const errorMessageEl = document.getElementById("error-message");
const statusSection = document.getElementById("status-section");
const warningsEl = document.getElementById("warnings");
const summaryEl = document.getElementById("summary");
const chartSection = document.getElementById("chart-section");
const chartFrame = document.getElementById("chart-frame");

// ---------------------------------------------------------------------------
// PWA-5.3: iframe sizing. The chart's SVG is a fixed 960x420 viewBox (2.286:1); shell.css
// gives the iframe that aspect ratio as an immediate first-paint fallback. That alone is not
// trusted to be enough headroom for the tooltip (absolutely positioned, can grow taller than
// the base chart, per the chart module's own R-10.2/R-10.6) -- instead the chart document
// itself is augmented, client-side, with a small script that measures its own real rendered
// height (base chart, then again whenever the tooltip opens/closes/resizes) and posts it to
// this page, which then sets the iframe's height explicitly. This never touches
// `render/section1_chart.py`'s own template; the reporting script is appended to the HTML
// string only after `bridge.run()` has already returned it.
// ---------------------------------------------------------------------------

const _HEIGHT_REPORTER_SCRIPT = `
<script>
(function () {
  var lastSent = -1;
  function reportHeight() {
    var h = Math.ceil(document.documentElement.getBoundingClientRect().height);
    if (h > 0 && h !== lastSent) {
      lastSent = h;
      parent.postMessage({ finaChartHeight: h }, "*");
    }
  }
  var scheduled = false;
  function scheduleReport() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(function () {
      scheduled = false;
      reportHeight();
    });
  }
  window.addEventListener("load", scheduleReport);
  window.addEventListener("resize", scheduleReport);
  if (document.body) {
    new MutationObserver(scheduleReport).observe(document.body, {
      attributes: true,
      subtree: true,
      childList: true,
    });
  }
  scheduleReport();
})();
</script>`;

function _augmentChartHtmlWithHeightReporting(chartHtml) {
  if (chartHtml.includes("</body>")) {
    return chartHtml.replace("</body>", `${_HEIGHT_REPORTER_SCRIPT}</body>`);
  }
  // Defensive fallback: the chart document has always had a </body> to date, but if that
  // ever changes, still report a height rather than silently never resizing the iframe.
  return chartHtml + _HEIGHT_REPORTER_SCRIPT;
}

// Guards the fallback reveal below against firing for a chart that has since been cleared or
// replaced by a newer one (a stale timeout must never reveal/resize the *current* iframe just
// because it happens to fire after a later showChart()/clearChart() call).
let _chartRevealToken = 0;

window.addEventListener("message", (event) => {
  if (event.source !== chartFrame.contentWindow) {
    return;
  }
  const data = event.data;
  if (data && typeof data.finaChartHeight === "number" && data.finaChartHeight > 0) {
    chartFrame.style.height = `${data.finaChartHeight}px`;
    // Only reveal the iframe once its real height is known -- until then the fallback
    // aspect-ratio box (shell.css's `.chart-frame`) is correctly proportioned for the SVG
    // alone but too short for the full document (title/subtitle/legend), so showing it
    // earlier would flash clipped content. See shell.css's own comment on `.chart-frame`.
    chartFrame.classList.add("chart-frame--sized");
  }
});

function clearChart() {
  _chartRevealToken += 1;
  chartFrame.removeAttribute("srcdoc");
  chartFrame.style.height = "";
  chartFrame.classList.remove("chart-frame--sized");
  chartSection.hidden = true;
}

// Bounds the worst case if the height-reporting message is ever delayed past this or never
// arrives at all (a dropped/delayed `postMessage` under real-device memory/CPU pressure is not
// provable never to happen) -- reveals the iframe at whatever height it currently has (the
// correct one if the message did arrive in time, the aspect-ratio fallback otherwise) rather
// than leaving it permanently invisible. A brief, bounded flash of the fallback ratio on a rare
// slow device is a strictly better failure mode than a chart that silently never appears.
const _REVEAL_FALLBACK_MS = 1500;

function showChart(chartHtml) {
  chartFrame.classList.remove("chart-frame--sized");
  chartFrame.srcdoc = _augmentChartHtmlWithHeightReporting(chartHtml);
  chartSection.hidden = false;
  const token = ++_chartRevealToken;
  setTimeout(() => {
    if (_chartRevealToken === token) {
      chartFrame.classList.add("chart-frame--sized");
    }
  }, _REVEAL_FALLBACK_MS);
}

// ---------------------------------------------------------------------------
// Rendering helpers. All use textContent/createElement, never innerHTML with user-controlled
// or file-derived strings (file names, warning text), even though this is a fully local,
// single-user app -- there is no reason to open an XSS-shaped hole just because the network
// isn't the attack surface here.
// ---------------------------------------------------------------------------

function renderRejected(rejected) {
  rejectedFilesEl.replaceChildren();
  if (rejected.length === 0) {
    rejectedFilesEl.hidden = true;
    return;
  }
  rejectedFilesEl.hidden = false;
  const heading = document.createElement("p");
  heading.className = "rejected-heading";
  heading.textContent =
    rejected.length === 1
      ? "1 file was not imported:"
      : `${rejected.length} files were not imported:`;
  rejectedFilesEl.appendChild(heading);
  const list = document.createElement("ul");
  for (const { file, reason } of rejected) {
    const item = document.createElement("li");
    const name = document.createElement("strong");
    name.textContent = file.name;
    item.appendChild(name);
    item.appendChild(document.createTextNode(`: ${reason}`));
    list.appendChild(item);
  }
  rejectedFilesEl.appendChild(list);
}

function renderWarnings(warnings) {
  warningsEl.replaceChildren();
  if (!warnings || warnings.length === 0) {
    warningsEl.hidden = true;
    return;
  }
  warningsEl.hidden = false;
  const heading = document.createElement("p");
  heading.className = "warnings-heading";
  heading.textContent = "Warnings";
  warningsEl.appendChild(heading);
  const list = document.createElement("ul");
  for (const message of warnings) {
    const item = document.createElement("li");
    item.textContent = message;
    list.appendChild(item);
  }
  warningsEl.appendChild(list);
}

// `summary`'s figures (real_net_worth/savings_only/gap) are already-rounded display strings
// from bridge.py (Decimal on the Python side); they are only ever concatenated below, never
// parsed back into a JS Number/float.
function renderSummary(summary) {
  summaryEl.replaceChildren();
  if (!summary) {
    summaryEl.hidden = true;
    return;
  }
  summaryEl.hidden = false;
  const asOfLabel = summary.is_partial ? "As of (partial month)" : "As of";
  const rows = [
    [asOfLabel, summary.as_of],
    ["Completeness", summary.completeness],
    ["Real net worth", `${summary.real_net_worth} EUR`],
    ["Savings only", `${summary.savings_only} EUR`],
    ["Gap", `${summary.gap} EUR`],
  ];
  const dl = document.createElement("dl");
  dl.className = "summary-list";
  for (const [label, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    dl.appendChild(dt);
    dl.appendChild(dd);
  }
  summaryEl.appendChild(dl);
}

// ---------------------------------------------------------------------------
// WP-15: the stored-files (rawFiles) active/inactive checklist.
// ---------------------------------------------------------------------------

function _formatSize(bytes) {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Renders the full stored-files checklist from `files` (as returned by
 * `storage.listRawFiles()`), sorted by filename for a stable, predictable display order --
 * `getAll()`'s own order is by content-hash key, which has no meaningful reading order.
 * Each row's checkbox is the *only* control that ever changes `rawFiles.active`
 * (`storage.setActive`, via `onToggleActive`).
 */
function renderStoredFiles(files) {
  storedFilesListEl.replaceChildren();
  if (files.length === 0) {
    storedFilesEmptyEl.hidden = false;
    storedFilesListEl.hidden = true;
    return;
  }
  storedFilesEmptyEl.hidden = true;
  storedFilesListEl.hidden = false;

  const sorted = [...files].sort((a, b) => a.filename.localeCompare(b.filename));
  for (const file of sorted) {
    const li = document.createElement("li");
    li.dataset.fileId = file.id;
    if (!file.active) {
      li.classList.add("stored-file-inactive");
    }

    const label = document.createElement("label");
    label.className = "stored-file-row";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "stored-file-checkbox";
    checkbox.checked = file.active;
    checkbox.setAttribute("aria-label", `Include ${file.filename} in the next run`);
    checkbox.addEventListener("change", () => {
      onToggleActive(file.id, checkbox.checked).catch((err) => {
        showError(err && err.message ? err.message : String(err));
      });
    });

    const info = document.createElement("div");
    info.className = "stored-file-info";
    const name = document.createElement("span");
    name.className = "stored-file-name";
    name.textContent = file.filename;
    const meta = document.createElement("span");
    meta.className = "stored-file-meta";
    meta.textContent = `${file.recognizedAs ?? "unknown"} · ${_formatSize(file.size)}`;
    info.appendChild(name);
    info.appendChild(meta);

    label.appendChild(checkbox);
    label.appendChild(info);
    li.appendChild(label);
    storedFilesListEl.appendChild(li);
  }
}

async function refreshStoredFilesChecklist() {
  const files = await storage.listRawFiles();
  renderStoredFiles(files);
  return files;
}

function _sortedIds(list) {
  return [...list].sort();
}

function _idsEqual(a, b) {
  const sa = _sortedIds(a);
  const sb = _sortedIds(b);
  return sa.length === sb.length && sa.every((v, i) => v === sb[i]);
}

/**
 * On startup, if a cached run (`storage.getRunCache()`) exists whose `activeIds` exactly match
 * the currently-active stored files, renders it directly -- no Pyodide boot needed at all. This
 * is the persistence payoff: reopening the app shows the prior result immediately, entirely
 * from `storage.js`, independent of whether/when Pyodide finishes loading.
 *
 * If the cache is missing or stale (the active set has changed since it was computed -- e.g.
 * after a backup restore), this deliberately does *not* auto-trigger a fresh Pyodide-backed run
 * on load (that would pay the boot cost on every single open, defeating the point of caching);
 * the shell instead stays in its empty state until the user picks a file or toggles a checkbox,
 * either of which always recomputes over the current active set.
 */
async function displayCachedResultIfFresh(files) {
  const cache = await storage.getRunCache();
  if (!cache) {
    return;
  }
  const activeIds = files.filter((f) => f.active).map((f) => f.id);
  if (activeIds.length === 0 || !_idsEqual(activeIds, cache.activeIds || [])) {
    return;
  }
  renderWarnings(cache.warnings);
  renderSummary(cache.summary);
  statusSection.hidden = false;
  showChart(cache.chartHtml);
}

/**
 * Runs the pipeline over `storage.getActiveFiles()` -- the full persistent active set, not only
 * whatever was just picked -- and renders the result. On success, caches it (`setRunCache`); on
 * failure, `storage.js`'s `runCache` is left completely untouched (§2.4.2) -- this function
 * simply never calls `setRunCache`/`clearRunCache` on that path.
 */
async function runOverActiveSet() {
  hideError();
  clearChart();
  statusSection.hidden = true;

  try {
    const activeRecords = await storage.getActiveFiles();
    if (activeRecords.length === 0) {
      // No active files at all (everything toggled off, or nothing stored yet) -- nothing
      // meaningful to run; leave the empty state showing rather than calling runBuild([]).
      return;
    }

    setBusy(true);
    try {
      const files = activeRecords.map((r) => new File([r.bytes], r.filename));
      const result = await runBuild(files);
      if (!result.ok) {
        // R-11.4 mirrored client-side, and WP-15/PWA-2.4.2 on top of it: a failing run must
        // never leave a partial or stale chart/summary on screen (handled above by the
        // unconditional clearChart()/statusSection.hidden=true before this call) -- and must
        // never touch the persisted runCache record. No storage.setRunCache/clearRunCache call
        // happens anywhere on this branch, by construction: whatever runCache already held, if
        // anything, survives exactly as it was.
        showError(
          result.error && result.error.message ? result.error.message : "Unknown pipeline error."
        );
        return;
      }
      renderWarnings(result.warnings);
      renderSummary(result.summary);
      statusSection.hidden = false;
      showChart(result.chart_html);

      await storage.setRunCache({
        activeIds: activeRecords.map((r) => r.id),
        warnings: result.warnings,
        summary: result.summary,
        manifestJson: result.manifest_json,
        chartHtml: result.chart_html,
        computedAt: new Date().toISOString(),
      });
    } finally {
      setBusy(false);
    }
  } finally {
    // A monotonically-increasing counter, bumped exactly once per completed
    // runOverActiveSet() call (success, failure, or the "nothing active" early return alike),
    // *after* every DOM update above has already happened -- tests use this to wait for "the
    // run this specific action triggered has fully settled" instead of racing the generic
    // chart/error-visibility check against a chart that was already showing from a previous,
    // unrelated run.
    window.__finaRunSeq = (window.__finaRunSeq || 0) + 1;
  }
}

/** A stored file's checkbox changed -- the sole path that ever changes `rawFiles.active`. */
async function onToggleActive(id, active) {
  await storage.setActive(id, active);
  await refreshStoredFilesChecklist();
  // WP-15 task spec: toggling re-runs the pipeline over the new active set immediately (chosen
  // over "signal only, wait for an explicit re-run action") -- the checklist is the only control
  // for `active`, so its own change event is already the user's explicit "recompute with this
  // set" action; a second confirmation step would just be friction for what the checkbox click
  // already unambiguously means.
  await runOverActiveSet();
}

function showError(message) {
  errorMessageEl.textContent = message;
  errorSection.hidden = false;
}

function hideError() {
  errorSection.hidden = true;
  errorMessageEl.textContent = "";
}

function setBusy(isBusy) {
  busyIndicator.hidden = !isBusy;
  fileInput.disabled = isBusy;
}

// ---------------------------------------------------------------------------
// Main flow.
// ---------------------------------------------------------------------------

async function handleFiles(fileList) {
  // Serializes every pick after startup's own checklist load / cache-display attempt
  // (`_initPromise`, defined below) -- without this, a file picked immediately on page load
  // could race `init()`'s own `storage.listRawFiles()` read/render and have its result
  // clobbered by init()'s own (by-then-stale) render call finishing second. `_initPromise`
  // itself never rejects (its own `.catch` already handled any startup error), so this await
  // never throws on init()'s behalf.
  await _initPromise;
  hideError();

  const { recognized, rejected } = await sniffAndPartition(fileList);
  renderRejected(rejected);

  // WP-15/PWA-2.4: each newly-recognized file is written to `rawFiles` as soon as it is picked
  // and recognized -- BEFORE the pipeline runs -- so a crash mid-computation never costs the
  // user having to re-find and re-pick the file; only the (cheap, re-runnable) computation is
  // ever lost. Re-picking an already-stored file (same content hash) is a no-op inside
  // `addRawFile` itself, never a duplicate row.
  for (const { file, adapter } of recognized) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    await storage.addRawFile({ filename: file.name, bytes, recognizedAs: adapter, active: true });
  }

  if (recognized.length > 0) {
    await refreshStoredFilesChecklist();
  }

  if (recognized.length === 0) {
    // Nothing new was recognized in this pick -- the persistent active set (if any) is
    // unchanged, so there is nothing new to compute; leave whatever was already displayed
    // (from a prior run or the startup cache) exactly as it was.
    return;
  }

  // WP-15 (Q-M, "persistent library"): the run's input is every stored *active* file, not only
  // what was just picked -- picking adds to the persistent set rather than replacing it.
  await runOverActiveSet();
}

fileInput.addEventListener("change", (event) => {
  const files = event.target.files;
  if (!files || files.length === 0) {
    return;
  }
  handleFiles(files).catch((err) => {
    setBusy(false);
    showError(err && err.message ? err.message : String(err));
  });
});

// ---------------------------------------------------------------------------
// WP-17: backup export/restore wiring. `backup-status` is a small, separate status line (not
// `error-section`/`rejected-files`, which are both specifically about the import/run flow) --
// export and restore are their own action with their own outcome to report, independent of
// whatever the shell's main import/run state currently shows.
// ---------------------------------------------------------------------------

function showBackupStatus(message, isError) {
  backupStatusEl.textContent = message;
  backupStatusEl.hidden = false;
  backupStatusEl.classList.toggle("backup-status--error", Boolean(isError));
}

function hideBackupStatus() {
  backupStatusEl.hidden = true;
  backupStatusEl.textContent = "";
  backupStatusEl.classList.remove("backup-status--error");
}

exportBackupButton.addEventListener("click", () => {
  hideBackupStatus();
  exportBackupButton.disabled = true;
  exportBackupToFile()
    .then((count) => {
      if (count === 0) {
        showBackupStatus("Nothing to back up yet -- no files stored.", false);
      } else {
        showBackupStatus(
          count === 1 ? "Backup saved (1 file)." : `Backup saved (${count} files).`,
          false
        );
      }
    })
    .catch((err) => {
      showBackupStatus(err && err.message ? err.message : String(err), true);
    })
    .finally(() => {
      exportBackupButton.disabled = false;
      // Mirrors __finaRunSeq's convention (see runOverActiveSet): bumped once per completed
      // export attempt, success or failure alike, after the status line has already been
      // updated -- tests wait on this instead of racing a fixed timeout against a real download.
      window.__finaBackupSeq = (window.__finaBackupSeq || 0) + 1;
    });
});

/**
 * Restores `file` (a picked backup archive) via `backup.js`, then -- mirroring `handleFiles`'s
 * own write-before-run sequencing exactly -- refreshes the stored-files checklist once any
 * entries were actually restored, and re-runs the pipeline over the (now possibly larger)
 * active set so the shell's displayed result never goes stale relative to what was just
 * restored.
 */
async function handleRestore(file) {
  await _initPromise;
  hideBackupStatus();
  hideError();

  const { restored, rejected } = await restoreFromFile(file);

  if (restored.length > 0) {
    await refreshStoredFilesChecklist();
  }

  const parts = [];
  if (restored.length > 0) {
    parts.push(restored.length === 1 ? "Restored 1 file." : `Restored ${restored.length} files.`);
  }
  if (rejected.length > 0) {
    parts.push(
      `${rejected.length} file(s) from the backup were not restored (unrecognized shape): ` +
        rejected.map((r) => r.filename).join(", ")
    );
  }
  if (parts.length === 0) {
    parts.push("The backup archive contained no files.");
  }
  showBackupStatus(parts.join(" "), rejected.length > 0 && restored.length === 0);

  if (restored.length > 0) {
    await runOverActiveSet();
  }
}

restoreBackupInput.addEventListener("change", (event) => {
  const file = event.target.files && event.target.files[0];
  // Cleared immediately (not just after a successful restore) so picking the exact same backup
  // file path again later still fires a "change" event -- the input's own value, not this
  // module's state, is what would otherwise suppress a second identical pick.
  restoreBackupInput.value = "";
  if (!file) {
    return;
  }
  handleRestore(file)
    .catch((err) => {
      showBackupStatus(err && err.message ? err.message : String(err), true);
    })
    .finally(() => {
      window.__finaBackupSeq = (window.__finaBackupSeq || 0) + 1;
    });
});

// ---------------------------------------------------------------------------
// Startup: load the stored-files checklist and, if a fresh cache exists, display it -- both
// entirely via storage.js, independent of whether/when Pyodide finishes booting (WP-15's whole
// point: a file picked, and a prior result, are usable before Pyodide is ready at all).
// ---------------------------------------------------------------------------

async function init() {
  const files = await refreshStoredFilesChecklist();
  await displayCachedResultIfFresh(files);
}

const _initPromise = init()
  .catch((err) => {
    showError(err && err.message ? err.message : String(err));
  })
  .finally(() => {
    // Marks startup (checklist load + cache display attempt) as settled, for tests to wait on
    // deterministically -- independent of `__finaAppJsLoaded` below, which only means "this
    // module's top-level code finished running", not "storage.js has been read yet".
    window.__finaChecklistReady = true;
  });

// Marks this module as loaded/parsed for tests, the same convention
// web/js/pyodide-bridge.js already established.
window.__finaAppJsLoaded = true;
