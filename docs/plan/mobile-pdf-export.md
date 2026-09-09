# Mobile / client-side PDF export for the two-page report

Research and design only (no application code changed by this document). Context: the app is
moving to run entirely inside an end user's browser tab via Pyodide — no server, no Python
subprocess, and critically **no Playwright / no headless Chromium**. That sibling work stream
owns the Pyodide migration itself; this document is scoped narrowly to one question it creates:
once `render/section1_chart.py`'s HTML/SVG/CSS/JS document is sitting in a real end-user tab
instead of a Playwright-controlled one, how does the project's stated two-page PDF deliverable
(R-10.1: "rendered as HTML/SVG and exported to PDF via a headless browser") get produced without
that headless browser?

Today's export path (`tests/test_render_browser.py`'s T-705, and `browser_support.py`) uses
Playwright's `page.pdf()` — a Chromium DevTools Protocol call with no equivalent exposed to a web
page's own JavaScript. A page cannot drive its own Chromium instance from inside a browser tab;
whatever replaces it must be something an ordinary tab can do to itself.

## 1. Baseline: the browser's own print-to-PDF (`window.print()` + `@media print`)

### 1.1 What it is

Every mainstream browser (Chrome/Edge, Firefox, Safari, and their Android/iOS counterparts) ships
a built-in "Print" pipeline, invokable from a page's own JS via `window.print()`, that renders the
current document through a `@media print` stylesheet and hands the result to the OS print dialog.
Every one of those dialogs offers a "Save as PDF" (or "Microsoft Print to PDF" / iOS's
Share → "Save to Files" from Print Preview) destination alongside physical printers — this is how
most people already turn a web page into a PDF today, with zero JavaScript library involved.

### 1.2 Would it work cleanly against this chart's existing markup?

Mostly yes, with specific CSS additions — not a redesign:

1. **Vector SVG survives print.** Current Chromium/Firefox/Safari print pipelines render inline
   `<svg>` (this chart's `viewBox`-scaled SVG, already responsive) as real vector content in the
   resulting PDF, not a rasterized bitmap — text stays sharp and the file stays small. This is a
   reasonable inference from how these engines implement printing (their print output goes
   through the same layout/paint pipeline as screen rendering, just re-targeted at a page box)
   but is exactly the kind of claim CLAUDE.md rule 19 says not to reason about blindly — it must
   be confirmed with a real rendered PDF at implementation time, not assumed from this doc.
2. **Background colours do not print by default.** Browsers historically suppress
   author-specified background colours/images on the printed page to save ink, unless told
   otherwise. This chart's `--surface`, `--page`, `--gain-fill`/`--loss-fill` band colours, and
   the tooltip's translucent background all carry *meaning*, not decoration (the gain/loss band
   colour is the whole point of R-10.2) — printing them washed out to white would be a real
   information loss, not a cosmetic one. The fix is the standard
   `print-color-adjust: exact;` (plus the still-needed `-webkit-print-color-adjust: exact;` for
   older WebKit) declaration — well-supported (Chrome, Firefox, Safari including iOS; only
   pre-2013 WebKit and Internet Explorer lack it, per
   [MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/print-color-adjust) /
   [caniuse](https://caniuse.com/css-color-adjust)), and must be added — this chart has no reason
   to already have it, since nothing has ever printed it before.
3. **Page-break control** (needed once Section 2 exists alongside this chart, to get an actual
   *two*-page PDF rather than one long scrolling page cut arbitrarily by paper height): the
   legacy `page-break-before` / `page-break-after` / `page-break-inside` properties (values
   `auto` / `always` / `avoid`) and their modern successors `break-before` / `break-after` /
   `break-inside` (CSS Fragmentation spec; adds `page`, `avoid-page`, etc.) are both usable —
   current engines alias the legacy names to the same behaviour, so declaring both is cheap
   insurance rather than a real choice between them. Two concrete uses this chart needs:
   - `break-inside: avoid;` (+ legacy) on `.card`, so the chart card is never split mid-way
     across a page boundary if it lands near one.
   - `break-after: page;` (+ legacy) on whatever wraps Section 1's content, once a Section 2
     container exists, so it reliably starts a fresh page rather than flowing onto page 1's
     leftover space. This chart alone is only ever "page 1 of 1" today (T-705 already asserts
     exactly one PDF page for it in isolation) — the two-page split is a property of the
     *combined* report document Section 2 will produce, not of this module by itself, but the
     print-CSS pattern (§4 below) should be established on this module now so Section 2's future
     container only has to opt into the same convention.
   - `@page { size: A4; margin: 12mm; }` sets a strong *hint* for paper size and margins, honoured
     by Chromium and Firefox print rendering — but the OS dialog still lets the user override
     paper size (e.g. choosing Letter in a Letter-default region) before saving. This is a real,
     unavoidable limitation of the platform, not a bug to fix: the moment a PDF is produced via
     the OS print dialog, the OS/browser — not this page's CSS — has the final say over the
     literal paper geometry.
4. **Forcing a light, ink-appropriate palette regardless of the viewer's dark-mode setting.** A
   PDF meant to be read, filed, or handed to someone else should not default to this chart's dark
   palette just because the reviewing session happened to have `prefers-color-scheme: dark` set —
   `@media print` should override the CSS variables back to the light token set unconditionally
   (see §4.2), independent of whatever theme the on-screen chart is using at export time.
5. **Mobile-first still applies, but the box changes.** R-10.2a's ~390px verification is about
   the *screen* viewport; print media is sized in physical units (`@page`), not CSS pixels tied
   to a device viewport — the SVG's own `viewBox` + `width:100%` already scales it to whatever box
   contains it, so no new responsive work is implied here, only a print-specific stylesheet layer
   on top of the existing one.

### 1.3 The real limitation

`window.print()` **cannot** hand the user a finished PDF file directly — it can only open the
native print dialog; choosing "Save as PDF" and a save location afterwards is the user's own,
manual, 2-3-tap action (destination picker → confirm → file-save sheet). This is not a
implementation gap to close; it is a hard platform boundary; no web page, in any browser, is
permitted to silently write a file to disk without that dialog — the exact same security boundary
that keeps a malicious page from writing arbitrary files. A "Download PDF" button that skips the
OS dialog entirely requires the page to *build the PDF bytes itself* (§2).

One useful continuity note: Playwright's `page.pdf()` (today's T-705) is, under the hood, a
DevTools-Protocol call into the very same Chromium print-to-PDF pipeline a "Save as PDF" click
would invoke — so the CSS/markup work in §4 is exercising the real code path a real user's
Chrome/Edge print action will hit, not a fiction. It is *not* the same pipeline Firefox or Safari
use for their own print-to-PDF, which is why any print-CSS change still needs a real look on more
than one engine before being trusted, per CLAUDE.md rule 19's "render and look, don't reason
blindly" instruction.

## 2. Alternative: a client-side JS PDF-generation library

**Confidence note, read before this section**: this session has no live npm-registry or
Bundlephobia access; the version-specific size numbers below come from a web search run during
this research pass (cited inline) rather than direct measurement, and general library behaviour
comes from this model's own training knowledge (current to Jan 2026) rather than a live check of
current docs. Treat exact byte counts as indicative, not load-bearing — re-verify against the
actual pinned version before treating any number here as a real budget.

### 2.1 Realistic candidates

1. **jsPDF** ([npm](https://www.npmjs.com/package/jspdf)) — the most established client-side PDF
   generator; draws PDF content via its own primitive API (`text()`, `line()`, `rect()`,
   `addImage()`) or, via its bundled `html` plugin (itself depending on `html2canvas`),
   rasterizes a DOM subtree into a bitmap and embeds that. Reported size: on the order of
   **~95 KB minzipped** to **~150 KB minified** depending on which numbers/version one reads (a
   2026 web search turned up both figures, [Bundlephobia's jspdf
   page](https://bundlephobia.com/package/jspdf) being the authoritative live source — check it
   directly against the exact version pinned, rather than trusting either number here).
2. **svg2pdf.js** ([GitHub, yWorks](https://github.com/yWorks/svg2pdf.js/)) — a companion library
   to jsPDF specifically for converting an SVG *element* into real vector PDF drawing commands
   (`doc.svg(element, {...})`) instead of rasterizing it — the only realistic way to get this
   chart's actual SVG into a jsPDF-built document without losing vector crispness. This is a
   **second** dependency on top of jsPDF, not an alternative to it.
3. **pdf-lib** ([npm](https://www.npmjs.com/package/pdf-lib)) — a modern, dependency-free
   (browser-and-Node) PDF construction/editing library with its own drawing API (text with
   embedded fonts, lines, rectangles, PNG/JPEG images). It has **no built-in HTML/SVG import** at
   all — using it means either re-implementing this chart's line/band/label geometry a second
   time against pdf-lib's own coordinate API (a second renderer to keep in sync with
   `section1_chart.py` forever), or rasterizing the SVG to a PNG first (e.g. via an offscreen
   `<canvas>` + `drawImage` on the SVG) and embedding that raster — same crispness loss as
   jsPDF's `html2canvas` route. This session could not retrieve a reliable minzipped figure for
   pdf-lib specifically (Bundlephobia's page for it did not return a number through this search);
   treat its size as "same order of magnitude as jsPDF, confirm directly" rather than a known
   quantity.

### 2.2 What either option actually costs in engineering, not just bytes

Neither library talks to a headless browser or any external process — both run entirely inside
the tab's own JS, which is exactly compatible with the Pyodide-in-browser constraint. But getting
*this specific chart* into either one is not "swap a function call":

- The jsPDF + svg2pdf.js route is the closer fit to what already exists (the chart is already an
  SVG) — two dependencies, but the chart's own SVG-building code in `section1_chart.py` would not
  need to change, only how the resulting document is consumed at export time.
- The pdf-lib route means writing and maintaining a second geometry renderer, which directly cuts
  against this project's own R-10.4 discipline (one place figures get arranged, never
  recomputed) — every future chart change (a new series, a resized tooltip, a new label rule)
  would need updating twice, in two different drawing APIs, with no automated check that they
  stay visually consistent. This is the weaker of the two library options for this codebase
  specifically, independent of bundle size.

### 2.3 The real trade-off: one-tap UX vs. dependency weight

The payoff of either route is genuine: `doc.save("informe.pdf")` (jsPDF) or a `Blob` download
built from pdf-lib's output bytes produces an actual **file the user's browser downloads
directly** — no OS print dialog, no "choose a destination" step, a real one-tap "Download PDF"
button. That is a materially better experience than §1's manual print-dialog dance.

Against that: this app already carries a real, one-time load-time cost from Pyodide itself (the
sibling stream's own concern, typically several MB before any Python import happens) every time a
user opens the tab fresh. Adding jsPDF+svg2pdf.js (roughly a couple hundred KB combined, by the
figures above) is small in absolute terms next to Pyodide's own payload, but it is a second,
independent thing that must be fetched, version-pinned, and kept working across browser updates,
for a feature (exporting a PDF) that a zero-dependency platform primitive already does today,
just less conveniently. It is a real cost, just a secondary one — not decisive on its own, but not
free either.

One more mobile-specific risk worth flagging rather than assuming away: browser-driven downloads
of dynamically-built `Blob`s (the file-save step either library needs) have historically had
rough edges specifically on iOS Safari (behaviour has varied across iOS versions for
blob-URL downloads triggered from JS vs. a real anchor click, and inside a home-screen/PWA
context vs. an ordinary Safari tab). Given rule 19's mobile-first mandate, this would need a real
on-device check before being trusted, exactly like §1.2's SVG-print claim.

## 3. Recommendation for this iteration

**Build §1 (print-to-PDF via `window.print()` + a print stylesheet) now. Do not add a JS PDF
library yet.**

Reasoning:

1. **Who is using this, today.** The stated user of this build is the project owner, reviewing
   their own finances once a month — not a product being handed to strangers who will bounce at
   friction. Two extra taps (Print → Save as PDF) once a month is a trivial cost for that user;
   it would not be a trivial cost for a signup funnel, which this is not.
2. **Zero added dependency weight**, on an app that already has a real load-time cost problem to
   manage (Pyodide). §1 needs new CSS only — no new JS library, nothing to version-pin, nothing
   that can go stale relative to a future browser change the way a third-party PDF-drawing
   library eventually will.
3. **It reuses, rather than discards, work already done.** T-705's existing Playwright-based PDF
   smoke test already exercises the same Chromium print-to-PDF pipeline `window.print()` +
   "Save as PDF" would hit for a real Chrome/Edge user (§1.3) — the print-CSS work in §4 is
   directly checkable by the project's own existing dev/CI environment (which still has Python and
   Playwright; only the *deployed, end-user* runtime loses those, per this task's own framing) —
   without inventing a new verification story for this specific feature yet.
4. **It is the smaller, reversible step.** Nothing about building §1 first forecloses §2 later —
   the two are not mutually exclusive architectures, just two different buttons that could both
   exist on the same page. Building the cheap one first and only reaching for the expensive one
   if the cheap one turns out to actually hurt in practice is the same "don't build what isn't
   needed yet" judgment this project has already made once, explicitly, in Q-E's resolution
   (deferring a completeness-caveat section rather than building it speculatively).

This is logged as a genuine forward-looking decision for the project owner in
**Q-N** (`docs/plan/open-questions.md`) rather than settled outright here, because *when* the
manual print-dialog friction is worth spending a real dependency on is a subjective threshold only
the person doing the monthly review can set — this document's own recommendation is only "not
yet," not "never."

## 4. Consequences for the existing chart markup/CSS (`render/section1_chart.py`)

### 4.1 What already needs no change

The interactive elements (`.hit-area`, the click-opened `.tooltip`, `.tooltip-close`, the
crosshair/dots) default to hidden (`opacity: 0`, no `.visible` class) until a **click** event
fires (`showFor()` in the page's own script, §"render_section1_chart" template). A static print
render never dispatches a click, so nothing forces a stray tooltip open in the printed output —
today's markup is already print-*safe* in that narrow sense, with no defect to fix there. The
static elements (the two series lines, the gain/loss band, the always-visible end-of-line labels
for the final month, the headline gap line, the legend) are exactly what a printed page needs and
already render as plain SVG/HTML with no JS dependency to display correctly.

### 4.2 What must be added, concretely

1. A `@media print` block that:
   - Re-declares the light-theme CSS variables on `:root` unconditionally (§1.2 point 4), so a
     dark-mode viewer still gets a light, ink-appropriate printed page.
   - Sets `print-color-adjust: exact; -webkit-print-color-adjust: exact;` on `.card` (or `body`),
     so the gain/loss band fill and surface colours — real information, not decoration — survive
     printing (§1.2 point 2).
   - Defensively hides the interactive-only affordances that have no meaning on a static page:
     `.hit-area`, `.tooltip`, `.tooltip-close`, `.crosshair`, `.dot` all get `display: none`. This
     is belt-and-suspenders over §4.1's "already safe by default" observation — cheap, and it
     removes any residual ambiguity about what a reviewer looking only at the CSS (not a render)
     might worry about.
   - Adds `break-inside: avoid;` (+ `page-break-inside: avoid;`) on `.card`, and a
     `break-after: page;` (+ `page-break-after: always;`) hook on whatever will wrap this
     module's output once it sits inside a combined two-section report document (§1.2 point 3).
   - Declares `@page { size: A4; margin: 12mm; }` as a hint (with the caveat in §1.2 point 3 that
     the OS dialog can still override it).
2. Nothing in `render_section1_chart`'s Python needs to change to add the above — it is a
   `@media print { ... }` block appended to the existing `<style>` in `_DOCUMENT_TEMPLATE`, using
   only CSS variables and classes that already exist. R-10.4 (no financial recomputation in this
   module) is untouched by any of this — print CSS changes *how* existing figures are painted,
   never *what* figure is computed.

### 4.3 The real gap: the tooltip's data has no printed equivalent — recommendation

The interactive tooltip is the *only* place `real_net_worth`, `savings_only`, `savings_flow`, and
`gap` are ever shown for any month other than the last one — the SVG's own end-of-line labels
(R-10.2) are deliberately limited to the final point (§ "render_section1_chart", `end_labels_svg`)
to avoid crowding the plot at 390px. Hiding the tooltip for print (§4.2) without replacing what it
carried would silently turn the two-page report — this project's own stated final deliverable,
and a project built specifically to make every figure traceable within a minute (CLAUDE.md
rule 10) — into one that shows a *shape* for eleven months and *numbers* for only one. That is a
real information loss the task's framing asks to be resolved with a concrete recommendation, not
left as a caveat:

**Recommendation: add a print-only data table, not per-point on-chart labels.** Concretely:

- Render (in Python, from the same `ChartRow` sequence already passed to
  `render_section1_chart` — no new figure computed, R-10.4 preserved) one row per month with the
  same four values the tooltip already shows, as an ordinary HTML `<table>` placed directly after
  the chart card in the document's markup.
- Give that table `display: none;` in the normal (screen) stylesheet — it never appears in the
  on-screen, mobile, click-to-reveal experience R-10.2 already specifies and Q-G/Q-J's work
  polished — and `display: table;` only inside the new `@media print` block, so it exists purely
  as the print/PDF rendering of data the screen version already exposes interactively.
- Reject the alternative the task raises of labelling every point directly on the chart instead:
  the chart's own existing skip-every-other-month x-axis rule (`_x_label_svg`) and the
  single-endpoint end-label rule both exist *because* labelling every point at a narrow width
  gets visually crowded (Q-J's own defect history is a direct warning about exactly this kind of
  cramped-text failure mode) — a printed page is wider than a 390px phone, but still finite, and
  a table scales to twelve rows (one ledger-month at a time, per the project's monthly cadence)
  far more predictably than twelve on-chart labels would. A table is also plain, selectable,
  copyable text in the resulting PDF — closer to CLAUDE.md rule 10's "verifiable against a source
  document in under a minute" than a value inferred by eye from a label's vertical position on a
  printed chart.

This print-only table needs no new data plumbing: `ChartRow` already carries every value it would
show (`real_net_worth_display`, `savings_only_display`, `savings_flow_display`, `gap_display`,
`month_label`, `as_of`, `is_partial`) — the same fields `_tooltip_rows`/`_tooltip_html` already
format for screen. A shared helper producing the table's rows from `_tooltip_rows`'s existing
output (rather than a third independent formatting pass) is the natural implementation shape,
though writing it is out of scope for this research-only document.

## 5. What this document leaves open

1. Section 2 of the report (whatever surface follows this chart) does not exist yet in this
   repository — §1.2's `break-after: page` hook is specified here as a convention for Section 1's
   own container to carry, but the actual two-page split can only be verified once Section 2's
   container exists and adopts the matching `break-before: page` (or is simply the very next
   block in document flow with Section 1 already forcing a break after itself).
2. Whether/when to move from §1 (print-to-PDF) to §2 (a JS library one-tap download) is logged as
   **Q-N** in `docs/plan/open-questions.md` rather than decided here — it depends on the project
   owner's own tolerance for the print-dialog's manual steps in practice, which this document has
   no way to observe in advance.
3. This document does not address how `tests/test_render_browser.py`'s Playwright-based
   verification itself should evolve long-term once the *deployed* app no longer bundles
   Playwright — only that the *development/CI* environment producing and testing this module can
   keep doing so unchanged for now (§1.3), since that environment is unaffected by what ships to
   end users. If the sibling Pyodide work stream reaches a point where dev/CI verification itself
   needs to change, that is that stream's own scope, not this document's.
