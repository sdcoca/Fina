# Mobile PWA shell — design and research

**Status**: proposal only. Per CLAUDE.md rule 6, nothing here is implemented — this document
is the plan to be confirmed before any `manifest.json`, service worker, or shell markup is
written. No file under `src/fina/`, `tests/`, or any other `docs/plan/*.md` is touched by this
document.

**Scope**: this designs the shell around the existing Python core — the parts a user directly
touches (install, storage, file import, offline behaviour, and where the existing chart output
is displayed). It assumes, without re-deciding, that running that Python core client-side via
Pyodide is feasible (a separate work stream owns that question) and that the core's public
surface is at minimum: a function that runs the full pipeline (R-11.1) over a set of in-memory
file contents and returns the run manifest (R-11.5) plus the rendered chart HTML
(`render_section1_chart`'s output, per `src/fina/cli.py`), and — per §3.3 below — a lighter
"does this file's shape match a known adapter" check exposed separately from the full run.

**Governing constraint (CLAUDE.md rule 18, restated because it shapes every section below)**:
this app has no backend for user data, by design. Raw bank/broker exports and the ledger
computed from them exist **only** on the user's device — never uploaded, synced, or logged to
any server this project controls. Every design choice below is checked against this before
anything else; where a mainstream PWA pattern (background sync, push, a share-target endpoint)
would normally imply a server round-trip, the choice made here is the one that keeps the round
trip inside the device or drops the feature.

---

## 1. Web app manifest and installability

### 1.1 `manifest.json` contents

**PWA-1.1** Minimum required fields, with values proposed:

1. `name`: "Fina" (full name; short enough that a short_name is arguably redundant, but
   Android's home-screen label space is tight, so both are set).
2. `short_name`: "Fina".
3. `start_url`: `/` (or `/index.html` — whichever the shell's build serves as its root; must
   be a URL the service worker (§4) can serve fully offline after first load).
4. `scope`: `/` — the whole app is one origin, one scope; no sub-app boundaries needed yet.
5. `display`: `standalone`. This is the one field that most changes the felt experience: no
   browser address bar/tab chrome once installed, closer to a native app. `fullscreen` is
   rejected — it also hides the OS status bar (clock, battery, network), which users expect to
   see, and gains nothing for a finance app.
6. `orientation`: not set (leave portrait-primary as the implicit default via CSS/layout, not
   locked in the manifest) — a phone in landscape should still work, just not be optimized
   first, consistent with mobile-first meaning "phone-shaped", not "portrait-only".
7. `theme_color` / `background_color`: see 1.2 — this is the one field the existing palette
   answers directly.
8. `icons`: see 1.3.

### 1.2 Theme color: what the existing chart palette gives, and its limit

`src/fina/render/section1_chart.py` defines the palette as CSS tokens (light first, dark via
`@media (prefers-color-scheme: dark)`, both redefining the same token names — this is exactly
the token discipline artifact/Section-10 work is already required to follow, R-10.3):

| token | light | dark |
|---|---|---|
| `--page` (ground) | `#f6f5f1` | `#0d0d0d` |
| `--surface` (card) | `#fcfcfb` | `#1a1a19` |
| `--ink` (primary text) | `#14140f` | `#ffffff` |
| `--line-real` (accent — the "real net worth" series) | `#2a78d6` | `#3987e5` |

**PWA-1.2a** `manifest.json`'s own `theme_color` and `background_color` are each a **single**
static value — the manifest spec has no light/dark variant for them. Recommendation: set both
from the **light** palette (`background_color: #f6f5f1`, `theme_color: #f6f5f1`) as the
baseline the OS uses for the splash screen and the initial status-bar tint before any page CSS
runs. Reasoning: this is the value used for the shortest, most-visible moment (cold-start splash
before the app's own JS/CSS has painted anything), and a light value there reads correctly
against either the Android or the (future) iOS status bar icon colors; a dark value would need
the status bar icons flipped to match, which the manifest cannot express conditionally.

**PWA-1.2b** For the *running* app (after first paint, where CSS already governs), use two
`<meta name="theme-color">` tags with `media` attributes to track the palette's real dark-mode
switch — this is supported independently of the manifest field, in both Chrome and Safari:

```html
<meta name="theme-color" content="#f6f5f1" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0d0d0d" media="(prefers-color-scheme: dark)">
```

This is the one place the shell's own theming should be a direct, literal reuse of
`section1_chart.py`'s tokens (`--page` light/dark) rather than a value invented separately for
the shell — so the installed app's chrome and its own first-party content never visibly
disagree about which theme is active.

### 1.3 Icons — not yet drawn, requirements only

**PWA-1.3** Not decided here (this is an asset-creation task, not a product decision, so it is
not logged as an open question — just flagged as outstanding work before `manifest.json` can
actually ship):

1. Sizes needed: at minimum `192×192` and `512×512` "any purpose" PNGs (Android's installability
   check requires these two), plus a `512×512` `purpose: "maskable"` variant sized to Android's
   adaptive-icon safe zone (artwork kept inside the center ~80% — the outer ring is masked to
   whatever shape the launcher applies).
2. `apple-touch-icon` (`180×180` PNG, no transparency — iOS composites it onto an opaque
   background and does not honor alpha) for iOS home-screen add (§1.4); iOS still does not
   reliably consume `manifest.json`'s own `icons` array for this, so this tag is required
   in addition to, not instead of, the manifest icons.
3. Recommendation on look, once someone draws it: reuse `--line-real` (the blue already used for
   the "real net worth" line — the number this whole app exists to get right) on `--page`, e.g.
   a monogram or a simple upward line-mark, rather than inventing a separate brand color the
   chart palette doesn't already use.

### 1.4 Android install vs. iOS Safari add — what to design around now

**PWA-1.4a (Android/Chrome, what's being built)** With a valid manifest, an https origin (or
localhost), and a service worker registered with a `fetch` handler (§4), Chrome fires
`beforeinstallprompt` and offers a native install affordance (either its own mini-infobar or,
if the app calls `event.prompt()` itself, a custom "Install Fina" button at a moment the app
chooses). Recommendation: capture the event, suppress the default mini-infobar
(`event.preventDefault()`), and surface a deliberate "Install Fina" action from the shell's own
UI (e.g. a settings-screen row) rather than leaving install timing to the browser's own
heuristic — a finance app's first session is file-import and looking at one's own numbers, not
an install decision, and the "have I imported anything successfully yet" moment is a more
sensible place to *offer* installing than a heuristic tied to page-visit count.

**PWA-1.4b (iOS Safari — not being built now, but designed around)** iOS Safari has no
`beforeinstallprompt` and no automatic banner at all: the *only* path is the user manually
tapping the Share icon → "Add to Home Screen". This can be prompted toward (an in-app banner
with instructions and the Share-icon glyph) but never triggered programmatically — any copy
written for this later should say exactly that, not imply an install button exists on iOS.
Two consequences worth designing around now even though nothing iOS-specific ships yet:

1. **No push notifications, historically** — Safari added Web Push for home-screen-installed
   PWAs only from iOS 16.4 (March 2023), and only for apps actually added to the home screen
   (a PWA merely open in a Safari tab gets no push, no matter how recent the iOS version). Any
   future feature idea premised on "notify the user when X" (a reminder to import this month's
   statement, say) needs an Android-first design and an explicit "no notification support"
   fallback path for iOS below 16.4 or for a user who never added the icon to their home screen
   — not an assumption that push works everywhere once the manifest exists.
2. **Storage eviction risk** — Safari enforces a 7-day cap on script-writable storage
   (including IndexedDB, §2) for an origin the user has not interacted with in 7 days, when that
   origin is only ever opened as a normal Safari tab. A page added to the home screen and opened
   in standalone mode runs in a separate storage container that this cap does not apply to — but
   a user who imports data once, never adds the icon, and comes back three weeks later **can
   lose their locally-stored ledger** on iOS specifically (this does not happen on Android/Chrome
   today). This is the strongest concrete reason, of everything in this document, to design a
   **local backup/export affordance** (a "save a backup file" button producing a single file the
   user picks a location for via the OS's own share/save sheet — still never leaving the device
   to a server Fina controls) as part of the storage design, not as an afterthought. It is called
   out again in §2.4.

---

## 2. Client-side storage

### 2.1 Why IndexedDB, not `localStorage`

**PWA-2.1** `localStorage` is synchronous, string-only, and capped at a few MB in most engines
— unsuitable for raw XLSX/CSV bytes (each export is typically tens of KB to a few MB, well past
comfortable `localStorage` territory) and unsuitable for the computed ledger once more than a
few months of history accumulate. IndexedDB is the only client-storage primitive that handles
arbitrary binary (`Blob`) values, is asynchronous (doesn't block the UI thread — relevant once
Pyodide is also competing for the main thread), and carries a quota in the tens-to-hundreds of
MB range on Chrome/Android and (installed) Safari alike — comfortably above what this app's
actual file sizes need. **Recommendation: IndexedDB is the only store for anything beyond a
trivial UI preference flag** — not a mix of both, to avoid a "which storage has the real state"
split-brain (echoing CLAUDE.md rule 16's single-source-of-truth principle, applied to the
client tier: one engine, not two).

### 2.2 Schema (three object stores, one database)

**PWA-2.2** Proposed shape — a name and a set of fields, not a commitment to exact IndexedDB
API mechanics:

1. **`rawFiles`** — one record per imported source file.
   - `id`: the SHA-256 content hash (reuses R-2.15's existing hashing — the same fact the
     pipeline itself already computes for duplicate-detection, so the shell doesn't invent a
     second identity scheme for the same file).
   - `filename`, `importedAt` (device local timestamp, display-only, never fed into any
     financial calculation), `size`.
   - `bytes`: the file content as a `Blob` (not base64 text — base64 costs ~33% extra space and
     buys nothing here; IndexedDB stores `Blob`s natively).
   - `recognizedAs`: the adapter name it sniffed as (R-11.2's `_select_adapter` naming, e.g.
     `"broker_csv"` / `"bank_es_xlsx"`), or `null` if unrecognized (§3.3).
   - `active`: boolean — see §2.3's persistence-model options; only meaningful if the project
     owner picks an option where stored files can be included or excluded from a run without
     being deleted.
2. **`runCache`** — at most one current record (or one per "run configuration" if the persistence
   decision in §2.3 allows more than one). Holds the last successful run's manifest (R-11.5: the
   file hashes it covered, `owned_accounts`, warnings, tool version) and the rendered chart HTML
   string, keyed by the hash-set of `rawFiles` records it was computed from. **This is a cache of
   a view, never a second source of truth** — the moment the active file set changes, this
   record is stale and must be recomputed before being shown again, mirroring CLAUDE.md rule 16
   applied one layer up: the ledger is the one source of truth server-side (so to speak) of this
   client, and `runCache` is exactly the kind of derived view that document already says must
   never be allowed to drift from what a fresh computation would produce.
3. **`settings`** — a handful of small key/value rows: theme override (if the user can force
   light/dark independent of `prefers-color-scheme`), whether the install prompt has already
   been dismissed once, onboarding-seen flag. Deliberately not modeling this as anything more
   than flat preferences — nothing financial belongs here.

### 2.3 The real product decision: does the user re-upload every session, or does the app remember files?

**PWA-2.3** This is the one place this document would otherwise stop short of choosing, per
the task's own instruction to log it rather than decide it — but the identical decision is
already logged, found independently while researching the Pyodide bridging layer, as
**Q-M** in `docs/plan/open-questions.md` ("Cross-session persistence of ingested source files /
ledger state in the mobile PWA"). This section adopts Q-M's framing rather than opening a
second, differently-worded entry for the same product-values call — its three options map
directly onto this document's storage schema (§2.2):

- Q-M's option 1 (no persistence) ⇔ **ephemeral**: nothing in `rawFiles` survives between
  sessions; every open starts from zero files, and the user re-supplies whatever they want
  included, every time. The literal, narrowest reading of D4 (R-0.2: "every run treats the
  files present as the complete current truth") carried over unchanged to the client: "the
  files present" means "the files the user handed over just now," full stop.
- Q-M's option 2 (persist raw, recompute in full every session) ⇔ **persistent library**:
  `rawFiles` accumulates across sessions (deduplicated by content hash, so re-selecting the same
  export twice is a no-op, not a second row); a run's input becomes "every stored file marked
  `active`," and the user's ordinary action is adding *new* exports, not re-supplying old ones.
  D4's recompute-from-raw discipline still holds in full — only the *scope* of "the files
  present" has grown from "this session's picker selection" to "the device's accumulated
  history." This is Q-M's own recommended option.
- Q-M's option 3 (persist a derived ledger/manifest, feed only new files incrementally) is
  rejected by Q-M's own reasoning (a second, sync-dependent source of truth, against CLAUDE.md
  rule 16) except as a rebuildable cache — which is exactly what this document's §2.2
  `runCache` store already is, so no further option needs adding here.

**One refinement this document adds on top of Q-M, not a competing decision**: if Q-M resolves
toward persistence (its own option 2), a further, smaller question is whether every stored file
silently counts as `active` in every future run, or whether the user sees and confirms which
stored files are active before each run (a lightweight checklist, letting a file be excluded —
say, a duplicate, or one from a since-closed account — without deleting it outright).
Recommendation: build the checklist. It is cheap once persistence exists at all, and it is the
direct mitigation for the one new risk persistence introduces that ephemeral storage structurally
cannot have — a forgotten-but-still-active file silently shaping every run's numbers with no
per-run confirmation step. This refinement does not need its own open-questions entry: it is
only meaningful once Q-M is resolved toward persistence, and it is a UX-cost tradeoff (one extra
tap per session) this document is comfortable recommending outright rather than escalating.

### 2.4 Write timing and failure handling

**PWA-2.4** Regardless of which way §2.3 resolves:

1. A file is written to `rawFiles` as soon as it is picked and recognized (§3.3) — **before**
   the full pipeline runs — so a crash, a tab close, or a Pyodide error mid-computation never
   costs the user having to re-find the file in Downloads again; only the (cheap, re-runnable)
   computation is lost, never the (annoying-to-redo-on-a-phone) act of locating and picking the
   file.
2. `runCache` is only overwritten by a *successful* run. Mirroring R-11.4's "no partial report"
   at the client-storage layer: if the pipeline raises (`ValidationError` / `ReconciliationError`)
   over the current active file set, the previous `runCache` record — if any — stays exactly as
   it was, and the shell shows the error (§3.3/§4) rather than a half-updated or blank chart.
3. **Local backup/export**: per §1.4b's iOS storage-eviction risk (which, while iOS is not being
   built yet, is a reason to shape the schema now so it isn't a rearchitecture later) —
   recommend `rawFiles` support a bulk "export everything as a single archive" action the user
   can save via the OS share/save sheet. This is not a network operation and does not weaken rule
   18: it moves bytes from IndexedDB to a file the user places wherever they choose on their own
   device (Files app, a cloud-drive folder they control, etc.) — never to a server this project
   runs. Whether to build this now or defer it is bundled into Q-M's decision (a persistent
   library needs this more urgently than an ephemeral design does, since ephemeral already puts
   the "second copy" burden on the user's Downloads folder by construction).

---

## 3. File import UX

### 3.1 The primary path: the file picker

**PWA-3.1** `<input type="file" accept=".csv,.xlsx" multiple>`, triggered from an obvious
"Import" action (a tab or a prominent button, not buried in a menu). On both Android and iOS
this opens the OS's own file/document picker, which already surfaces Downloads, the Files app,
and any cloud-drive provider the user has configured — exactly the place a bank/broker export
actually lands after being downloaded or shared once. `multiple` lets the user pick a bank
export and a broker export in one picker interaction rather than two separate import trips.

### 3.2 Enhancement: OS share-sheet ("share to Fina") — recommend deferring, not deciding against

**PWA-3.2** A Web Share Target (`share_target` in `manifest.json`, a `POST`-based target
requiring a service-worker `fetch` handler to receive it) would let the user share a file
*from* their banking app's own export/share flow directly to Fina, skipping the Downloads-folder
detour in §3.1 entirely. Recommendation: **build §3.1 first; treat this as a v2 candidate**, not
because it is a bad idea but because it is Android/Chrome-only (no iOS Safari support exists for
`share_target`, historically or currently) and it adds a real piece of service-worker surface —
a `POST` handler that must itself route the shared bytes straight into IndexedDB and never let
them touch an actual network `fetch` onward, which is an easy invariant to state and worth
verifying carefully once (not a reason to avoid it, but a reason not to bundle its complexity
into the first shippable shell). This is stated as a recommendation, not logged as an open
question — it is a sequencing call this document is comfortable making, not a product-values
question only the owner can settle.

### 3.3 "We don't recognize this file" — designing for exactly two adapters

**PWA-3.3** Today there are exactly two working adapters (R-0.1: a Trade-Republic-shaped broker
CSV, a Spanish-bank-shaped XLSX), selected by file-shape sniffing, never by filename or
extension (R-11.2) — and an unrecognized shape already raises a structured `ParseError` naming
every adapter tried (`src/fina/pipeline.py`'s `_select_adapter`: *"a shape matching one of the
known adapters: broker_csv, bank_es_xlsx"*). The shell must not assume every picked file works;
it must design for this rejection path as a first-class, expected outcome, not an exceptional
crash.

**Two ways the shell could be structured, given R-11.4's existing "one bad file aborts the whole
run, no partial report" rule:**

1. **(a) Sniff before running — recommended.** The shell calls a lightweight "does this file's
   shape match a known adapter" check (the same sniffing logic `_select_adapter` already uses,
   exposed as its own entry point rather than only reachable by triggering the full pipeline) on
   each file **at pick time**, per file, before ever handing anything to the full pipeline. A
   file that doesn't match either shape is rejected immediately, individually, with copy such as:
   *"This file doesn't look like a bank or broker export Fina recognizes. Supported today: a
   Trade Republic CSV export, or a Spanish bank XLSX export."* — and is never written to
   `rawFiles` as anything but a rejected attempt (or not stored at all). Only files that already
   matched a known shape reach the pipeline, so R-11.4's all-or-nothing abort is preserved for
   what it actually means to guard — a *recognized* file whose *data* fails validation or
   reconciliation — rather than being triggered by an incidental unrelated file (a stray PDF from
   the same Downloads folder) sitting alongside good ones in one multi-file pick.
2. **(b) Let every file reach the pipeline.** Simpler to build, but means one unrelated file in
   a multi-file pick produces the same "nothing was computed" abort state as a genuine
   reconciliation failure — the two are very different situations for a user to be told about the
   same way.

**Recommendation: (a).** This requires the Pyodide bridge (the separate work stream) to expose
the adapters' sniffing step as its own callable, distinct from the full `R-11.1` pipeline entry
point — flagged here as an interface requirement on that other work stream, not decided by this
document, since it does not touch `src/fina/` itself (sniffing already exists there; only a new
thin export of it is being asked for).

**PWA-3.3a** The app's own empty/import state (what the user sees before ever importing
anything, not only the error path) should say which two formats are supported, in the same
place the "Import" action lives — setting the expectation before the user tries, rather than
only after a rejection.

---

## 4. Offline behavior

**PWA-4.1 What "offline-capable" means here.** This is not an offline-then-sync pattern — there
is no server for user data to sync *to* (rule 18). "Offline-capable" means: once the app shell
has been loaded once (including the Pyodide runtime and the `fina` wheel), it keeps working with
**zero** network access, indefinitely — imports, computation, and viewing the chart all happen
the same way with the device in airplane mode as with a live connection. There is no "queued
while offline, sent when back online" state anywhere in this design, because nothing the user
does here is ever meant to leave the device.

**PWA-4.2 What the service worker precaches (the shell only).**

1. The shell's own HTML/CSS/JS bundle.
2. The Pyodide runtime and the `fina` package's wheel/dependencies (`openpyxl`, per
   `pyproject.toml`) — likely the largest single payload the app ever downloads; precaching this
   on install (rather than fetching it lazily on first use) is what makes "works with the device
   in airplane mode the moment after install" true rather than "works offline once you've
   happened to use it online once already."
3. Icons and any self-hosted font files. **Recommendation: self-host the IBM Plex subset the
   chart's own typography names** (`section1_chart.py`'s font-family declarations, per that
   module's own Q-I resolution already on record: it names "IBM Plex Serif/Sans/Mono" but
   deliberately does *not* load them from `fonts.googleapis.com`, precisely so the renderer never
   depends on network access to look right). Extending that same already-established principle
   to the shell means the shell should not reintroduce, at its own layer, the exact network
   dependency the chart module went out of its way to avoid at its layer — either self-host the
   real font files (once available to do so) or accept the same system-font fallback stack the
   chart already falls back to, but never a live Google Fonts request from the shell.

**PWA-4.3 What must never be cached or requested at all: any of the user's actual financial
data.** Not "cached with a short TTL," not "cached but excluded from background sync" — **never
enters the service worker's `fetch` handler in the first place.** A bank statement's bytes and
the computed ledger travel `IndexedDB` ↔ page JS ↔ Pyodide entirely in-process; none of that is
a `fetch()` call, so there is no request for the service worker to intercept, cache, or leak, by
construction rather than by a caching-policy exclusion rule that could later be gotten wrong.
This is the one item in this whole document where "make sure the design can't do X" is stronger
than "design a policy that says don't do X."

**PWA-4.4 Update strategy.** A new service-worker version installs in the background per the
standard SW lifecycle; recommend an explicit user-facing "update available — reload to apply"
prompt rather than silent `skipWaiting()`/`clients.claim()` activation. A chart mid-tooltip-
interaction (open, dismissible, per R-10.2) silently swapping underneath the user because a new
version activated itself is exactly the kind of small mobile-UX defect this project's own history
(Q-J, three real visual defects found only by looking at a real device) suggests is worth
designing away rather than discovering later.

**PWA-4.5 Interface point with the Pyodide work stream, not decided here.** The shell's own
cache-versioning key should be tied to whatever build/version identifier the Pyodide/core work
stream produces, so a shell update and a core update always ship as one atomic unit — an old
shell talking to a newer core's data shapes (or vice versa) is a determinism/correctness risk
this document flags as an interface requirement between the two work streams without presuming
to specify that other stream's own versioning scheme.

---

## 5. Where the existing chart output fits

**PWA-5.1 What exists today, precisely.** `render_section1_chart` (`src/fina/render/
section1_chart.py`) returns a complete, self-contained document string — `<!doctype html>`
through a closing `</html>`, its own `<head>` with a `<style>` block that defines a page-scoped
`:root { --page: ...; --ink: ...; }` token set (§1.2's table) and an inline `<script>` handling
the click-to-reveal tooltip — and `src/fina/cli.py` writes that string verbatim to its own file,
`section1_chart.html`, with nothing else on the page. It is designed, by explicit prior decision
(Q-I in `docs/plan/open-questions.md`), to be fully self-contained with **no external assets** —
originally so a headless browser could screenshot/PDF it reliably (R-10.1) without depending on
network access, but that same property is exactly what makes it safe to embed elsewhere too.

**PWA-5.2 Three ways to slot it into the shell, and why two of them are actively unsafe, not
merely inelegant:**

1. **(a) An `<iframe>`** — the chart's full HTML string set via `srcdoc` (or a `Blob` URL),
   `sandbox="allow-scripts"` (needed for the tooltip's click handling; no `allow-same-origin`
   unless something specific is found to require it, since R-10.4 already establishes the module
   never reaches outside the `ChartRow` data it's handed — it shouldn't need to reach outside its
   own iframe either).
2. **(b) Inject the markup directly into the shell's own DOM** (`container.innerHTML = chartHtml`
   or equivalent). **Rejected, not merely deprioritized** — two concrete, verifiable problems,
   not a style preference:
   - The chart's `<style>` block's `:root { --page: #f6f5f1; --ink: #14140f; ... }` is written
     assuming it is the *page's own* root — inserted into the shell's existing document, those
     custom-property names collide with (and, depending on CSS source order, silently override
     or get overridden by) whatever the shell defines under the same names for its own theming.
     This is not hypothetical: the shell's own theme tokens (§1.2) are being modeled on this
     exact palette, so a naming collision is the likely case, not an edge case.
   - **`<script>` tags inserted via `innerHTML` do not execute** — this is standard DOM
     behavior, not an implementation detail that might vary. The tooltip's click-to-open/close
     interaction (R-10.2's own visual contract) would silently stop working the moment this
     module's markup is injected this way; recovering it would require manually re-creating and
     re-inserting the script node, at which point the isolation an iframe already gives up front
     is being rebuilt piecemeal, with none of its safety.
3. **(c) Serve the chart as the entire page, unchanged from today** — literally true right now
   (there is no shell yet), but is a description of the current Python-only artifact, not an
   answer to this document's actual assignment, which is to design a shell *around* it. Adopting
   (c) as the shell's own design would mean building no shell at all.

**Recommendation: (a).** It is the only option consistent with why this module was built as a
self-contained document in the first place, it costs nothing the module doesn't already provide
(no change to `render/section1_chart.py`'s contract — out of this document's scope regardless,
per the task's explicit "do not touch `src/fina/`"), and it is the shape that scales the way the
task frames future growth: "more report sections added as new documents later." Each future
report section, if it follows this module's own established pattern (a self-contained
`<!doctype html>` document per section, per R-10.4-style discipline), becomes one more iframe —
the shell hosts a tab or carousel of them, one per section, rather than needing a rewrite each
time a new section is added.

**PWA-5.3 One implementation detail flagged for whoever builds this, not resolved here:**
sizing. The chart's SVG is a fixed `960×420` viewBox (a `2.286:1` aspect ratio) scaled
responsively by its own CSS; an iframe's height does not auto-follow its content's rendered
size the way a normal block element would. A first pass can fix the iframe's height to that same
aspect ratio against the shell's own width (letting the chart's internal layout — which is
already verified mobile-first at 390px, R-10.2a — handle the rest); if the *tooltip* content
(which is absolutely positioned and can be taller than the base chart, per R-10.2's own
box-fitting rule, R-10.6) turns out to need more room than the fixed-height iframe gives it, the
more robust fix is the chart's own script `postMessage`-ing its real rendered height to the
parent shell once loaded. **This needs a real mobile-width render check once it's built (CLAUDE.md
rule 19) — not reasoned about only on paper here.**
