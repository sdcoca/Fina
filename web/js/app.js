// web/js/app.js
//
// WP-13: wires the shell together. Picks a FileList from the <input>, runs it through
// import.js's sniff-before-run filtering, hands the recognized subset to runBuild() (WP-12),
// and renders the result -- rejections, warnings, the summary figures, and the chart, embedded
// via a sandboxed <iframe srcdoc="..."> (PWA-5.2a). Contains no financial logic of its own:
// every figure `result.summary` carries is already a rounded display string from
// `web/py/bridge.py` and is only ever concatenated here, never re-parsed/rounded as a JS float
// (CLAUDE.md rule 9's intent, extended to this layer).

import { sniffAndPartition } from "./import.js";
import { runBuild } from "./pyodide-bridge.js";

const fileInput = document.getElementById("file-input");
const busyIndicator = document.getElementById("busy-indicator");
const rejectedFilesEl = document.getElementById("rejected-files");
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

window.addEventListener("message", (event) => {
  if (event.source !== chartFrame.contentWindow) {
    return;
  }
  const data = event.data;
  if (data && typeof data.finaChartHeight === "number" && data.finaChartHeight > 0) {
    chartFrame.style.height = `${data.finaChartHeight}px`;
  }
});

function clearChart() {
  chartFrame.removeAttribute("srcdoc");
  chartFrame.style.height = "";
  chartSection.hidden = true;
}

function showChart(chartHtml) {
  chartFrame.srcdoc = _augmentChartHtmlWithHeightReporting(chartHtml);
  chartSection.hidden = false;
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
  hideError();
  clearChart();
  statusSection.hidden = true;

  const { recognized, rejected } = await sniffAndPartition(fileList);
  renderRejected(rejected);

  if (recognized.length === 0) {
    // Nothing recognized in this pick -- do not call runBuild() at all (an empty active-file
    // set is not a meaningful pipeline input), and leave the chart/status sections cleared
    // rather than showing anything stale.
    return;
  }

  setBusy(true);
  try {
    const result = await runBuild(recognized);
    if (!result.ok) {
      // R-11.4 mirrored client-side: a recognized-shape file whose data still failed inside
      // the real pipeline must never leave a partial or stale chart/summary on screen.
      showError(
        result.error && result.error.message ? result.error.message : "Unknown pipeline error."
      );
      return;
    }
    renderWarnings(result.warnings);
    renderSummary(result.summary);
    statusSection.hidden = false;
    showChart(result.chart_html);
  } finally {
    setBusy(false);
  }
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

// Marks this module as loaded/parsed for tests, the same convention
// web/js/pyodide-bridge.js already established.
window.__finaAppJsLoaded = true;
