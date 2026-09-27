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
//
// WP-19/WP-22b (R-3.8): the "Accounts" section after the chart (rendered by `accounts.js`):
// statement accounts with their own files, accounts to confirm, and the names the user gave
// them. Every "Mine" / "Not mine" / rename rewrites the own-accounts confirmation file (a
// stored file like any statement, via `storage.js`) and re-runs -- the engine alone decides
// what that changes. The chart itself carries the three headline figures (WP-22a), so there is
// no separate summary card any more.

import { renderAccounts } from "./accounts.js";
import { exportBackupToFile, restoreFromFile } from "./backup.js";
import { sniffAndPartition } from "./import.js";
import {
  mergeDocuments,
  parseDocument,
  serializeDocument,
  todayIso,
  withAlias,
  withDecision,
} from "./own-accounts.js";
import { runBuild } from "./pyodide-bridge.js";
import * as storage from "./storage.js";

const fileInput = document.getElementById("file-input");
const importButton = document.getElementById("import-button");
const busyIndicator = document.getElementById("busy-indicator");
const rejectedFilesEl = document.getElementById("rejected-files");
const exportBackupButton = document.getElementById("export-backup-button");
const restoreBackupInput = document.getElementById("restore-backup-input");
const backupStatusEl = document.getElementById("backup-status");
const errorSection = document.getElementById("error-section");
const errorMessageEl = document.getElementById("error-message");
const statusSection = document.getElementById("status-section");
const warningsEl = document.getElementById("warnings");
const accountsSection = document.getElementById("accounts-section");
const chartSection = document.getElementById("chart-section");
const chartFrame = document.getElementById("chart-frame");

// ---------------------------------------------------------------------------
// PWA-5.3: iframe sizing. The chart document is roughly 390x520 at a phone width; shell.css
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

// ---------------------------------------------------------------------------
// WP-22b: the Accounts section. `_lastRun` holds what the latest successful run (or the
// startup cache) said about accounts; stored files and names come from `storage.js`.
// ---------------------------------------------------------------------------

let _lastRun = { accounts: [], candidates: [] };

/** The stored confirmation file(s) as one document: decisions and names. */
async function _currentDocument() {
  const files = await storage.getOwnAccountsFiles();
  return mergeDocuments(files.map((f) => parseDocument(f.bytes)));
}

/** Every account to show, its files inside it; files never linked to one go to "Other". */
async function refreshAccounts({ expandAttention = false } = {}) {
  const files = await storage.listRawFiles();
  const documentFiles = files.filter((f) => f.recognizedAs === storage.OWN_ACCOUNTS_ADAPTER);
  const statementFiles = files.filter((f) => f.recognizedAs !== storage.OWN_ACCOUNTS_ADAPTER);
  const { aliases } = documentFiles.length ? await _currentDocument() : { aliases: {} };

  const byKey = new Map();
  for (const account of _lastRun.accounts) {
    byKey.set(account.key, { ...account, files: [] });
  }
  const unlinkedFiles = [];
  for (const file of statementFiles) {
    const account = file.account;
    if (!account) {
      unlinkedFiles.push(file);
      continue;
    }
    if (!byKey.has(account.key)) {
      byKey.set(account.key, { ...account, balance: null, files: [] });
    }
    byKey.get(account.key).files.push(file);
  }
  renderAccounts(
    accountsSection,
    {
      statementAccounts: [...byKey.values()],
      candidates: _lastRun.candidates,
      unlinkedFiles,
      aliases,
    },
    {
      onToggleFile: onToggleActive,
      onDecide: decideAccount,
      onRename: renameAccount,
      onImport: () => fileInput.click(),
      onError: (err) => showError(err && err.message ? err.message : String(err)),
    },
    { expandAttention }
  );
  return files;
}

/** Rewrites the confirmation file with `change` applied, then recomputes. */
async function _updateDocument(change) {
  const doc = await _currentDocument();
  await storage.replaceOwnAccountsFile(serializeDocument(change(doc)));
  await refreshAccounts();
  await runOverActiveSet();
}

/** Records the user's decision on one account (R-3.8). */
async function decideAccount(candidate, owned) {
  await _updateDocument((doc) => {
    const previous = doc.accounts.find((d) => d.iban === candidate.iban);
    const holderName = candidate.holder_names[0] ?? (previous && previous.holder_name);
    return {
      ...doc,
      accounts: withDecision(doc.accounts, {
        iban: candidate.iban,
        holder_name: holderName,
        owned,
        decided_on: todayIso(),
      }),
    };
  });
}

/** Names one account (a blank name goes back to the default label). */
async function renameAccount(key, name) {
  await _updateDocument((doc) => ({ ...doc, aliases: withAlias(doc.aliases, key, name) }));
}

/**
 * After a backup restore: if it brought in a second confirmation file, merge both into one
 * (latest decision per account wins) so a run never reads two versions.
 */
async function consolidateOwnAccounts() {
  const files = await storage.getOwnAccountsFiles();
  if (files.length > 1) {
    await storage.replaceOwnAccountsFile(serializeDocument(await _currentDocument()));
  }
}

/** Remembers, per stored file, the account the run found it declares (see storage.js). */
async function _linkFilesToAccounts(accounts) {
  const byFilename = {};
  for (const account of accounts) {
    const { files, balance, ...identity } = account;
    for (const filename of files) {
      byFilename[filename] = identity;
    }
  }
  await storage.setFileAccounts(byFilename);
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
  statusSection.hidden = false;
  _lastRun = { accounts: cache.accounts || [], candidates: cache.candidates || [] };
  await refreshAccounts();
  showChart(cache.chartHtml);
}

/**
 * Runs the pipeline over `storage.getActiveFiles()` -- the full persistent active set, not only
 * whatever was just picked -- and renders the result. On success, caches it (`setRunCache`); on
 * failure, `storage.js`'s `runCache` is left completely untouched (§2.4.2) -- this function
 * simply never calls `setRunCache`/`clearRunCache` on that path.
 */
async function runOverActiveSet({ expandAttention = false } = {}) {
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
      // Everything the run must persist is written first (each file's account, the cache), and
      // only then shown: once the result is on screen, it is also safely stored -- a reload at
      // that moment can never find a half-written state.
      _lastRun = { accounts: result.accounts, candidates: result.candidates };
      await _linkFilesToAccounts(result.accounts);
      await storage.setRunCache({
        activeIds: activeRecords.map((r) => r.id),
        warnings: result.warnings,
        accounts: result.accounts,
        candidates: result.candidates,
        summary: result.summary,
        manifestJson: result.manifest_json,
        chartHtml: result.chart_html,
        computedAt: new Date().toISOString(),
      });

      renderWarnings(result.warnings);
      statusSection.hidden = false;
      await refreshAccounts({ expandAttention });
      showChart(result.chart_html);
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
  await refreshAccounts();
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
  importButton.disabled = isBusy;
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
    await refreshAccounts();
  }

  if (recognized.length === 0) {
    // Nothing new was recognized in this pick -- the persistent active set (if any) is
    // unchanged, so there is nothing new to compute; leave whatever was already displayed
    // (from a prior run or the startup cache) exactly as it was.
    return;
  }

  // WP-15 (Q-M, "persistent library"): the run's input is every stored *active* file, not only
  // what was just picked -- picking adds to the persistent set rather than replacing it.
  // WP-22b: a file was loaded, so the account groups that need attention open by themselves.
  await runOverActiveSet({ expandAttention: true });
}

// WP-22b: the Accounts header's "Import" button opens the (hidden) file picker. A button is
// interactive content, so tapping it never also opens/closes the section it sits in.
importButton.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", (event) => {
  const files = Array.from(event.target.files || []);
  // Cleared right away (the picker is hidden now, WP-22b): picking the same file again later
  // must still fire "change".
  fileInput.value = "";
  if (files.length === 0) {
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
    await consolidateOwnAccounts();
    await refreshAccounts();
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
    await runOverActiveSet({ expandAttention: true });
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
  const files = await refreshAccounts();
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
