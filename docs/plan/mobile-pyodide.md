# Mobile PWA via Pyodide — feasibility and bridging plan

Research and design only. Nothing under `src/fina/` changes as a result of this document;
it exists to let the project owner decide, with the real constraints in front of them,
whether "run the existing, already-verified Python package client-side via Pyodide" is
viable before any implementation work package is written for it.

## 0. How this was researched, and how to read the confidence markers

Per CLAUDE.md rule 11 (measured vs. estimated, with the magnitude of uncertainty and what
document would resolve it), every claim below is tagged:

- **[DOCS]** — found in Pyodide's own documentation or GitHub issues via web search this
  session. Direct fetches of `pyodide.org` pages were blocked by this environment's egress
  proxy, so these are search-engine snippets *of* those pages, not the full page text
  re-read first-hand. Treated as reliable for facts (a module is/isn't vendored, a
  mechanism exists) but not for exact version numbers, which the search tool sometimes
  garbled (see 0.1).
- **[KNOWLEDGE]** — this model's own training knowledge (cutoff January 2026), used where
  search returned nothing more specific. Flagged separately so the reader knows it wasn't
  cross-checked against a live source this session.
- **[ESTIMATE]** — arithmetic or reasoning built on the above, not a fact anyone published.
- **[OPEN]** — genuinely unresolved by research; resolvable only by running the real thing
  on a real device. Not escalated to `open-questions.md` unless it is also a *decision* the
  owner must make, per this task's own instructions — most of these are just "someone should
  time it," which is an implementation-phase task, not a decision.

### 0.1 A caution about the version string returned by search

Search results this session consistently returned a Pyodide "stable" version string of the
form `314.0.x`, alongside separate hits describing `0.29.x` releases dated January–May 2026.
**[KNOWLEDGE]**: Pyodide's version historically tracked its own release counter (`0.19`,
`0.20`, ... `0.28`), each pinned to one CPython minor version. A `314.x` string is
consistent with a switch to a scheme keyed to the embedded CPython version (3.14 → `314`),
which would explain why both forms appear for what is nominally "the current stable release."
This is plausible but **not independently confirmed** this session (direct page fetch was
blocked); nothing in this plan depends on the exact number, only on the *behaviour* described
in 1.1–1.2, which multiple independent search hits agree on. If the owner wants the exact
pinned version before committing engineering time, that is a five-minute check once
`pyodide.org` is reachable (it is not blocked for a normal browser, only for this session's
fetch tool) — not a research gap that changes the recommendation.

---

## 1. Can Pyodide run `openpyxl` and `decimal` as this project uses them?

### 1.1 `decimal` — yes, and no extra step is needed **[DOCS]**

`decimal` in CPython has two implementations: the C-accelerated `_decimal` and the pure-Python
fallback `_pydecimal`, with identical documented behaviour. Search hits against Pyodide's own
"Python compatibility" and changelog pages agree on two points that matter here:

1. Pyodide's C-accelerated `_decimal` is part of the standard build — it is not one of the
   modules that needs an explicit `loadPackage`/`micropip.install` call before `import decimal`
   works.
2. Recent Pyodide releases went further and **stopped unvendoring most of the stdlib by
   default** (one hit: "Pyodide no longer unvendors stdlibs, and `sqlite3` and `lzma` are now
   bundled into Pyodide by default"), while the now-redundant pure-Python `_pydecimal` fallback
   was *removed* from the distribution as dead weight — redundant because the C version is
   already present.

Net effect: every module `fina` imports from `decimal` (`Decimal`, rounding modes, `getcontext`,
`InvalidOperation`, etc. — see `src/fina/money.py`) is available immediately after
`loadPyodide()`, with the same C-accelerated arithmetic CPython uses natively, not a slower
pure-Python emulation. This is the strongest, most confidently-sourced finding in this
document: **no risk, no workaround needed.**

### 1.2 `openpyxl` — not prebuilt, but installable, and offline-safe **[DOCS]**

`openpyxl` does **not** appear to be one of the packages Pyodide cross-compiles and ships in
its curated distribution (the list at `packages-in-pyodide.html` exists specifically for
packages with C/Rust extensions that need Emscripten compilation — numpy, pandas, etc.). A
2022 feature request for it (`pyodide/pyodide#2959`) was still open/unresolved in the search
results, and later hits (`pyodide/pyodide#4533`, "Plotly and Openpyxl problem") suggest people
hit friction expecting it to be there "for free."

That is not the same as "unsupported," though. `openpyxl` is a **pure-Python package** — its
own dependency, `et_xmlfile`, is pure Python too, and its only optional dependency (`lxml`,
for slightly faster parsing) is never imported by `fina`'s adapter, so it is irrelevant here.
Pyodide's `micropip` installs **any** pure-Python wheel, curated or not, as long as it can
fetch it — `openpyxl` publishes a universal `py2.py3-none-any` wheel on PyPI, which is exactly
the case `micropip` supports:

```python
import micropip
await micropip.install("openpyxl")
import openpyxl  # works unmodified from here
```

**The one real risk this raises is not "does it work," it's "does it work offline."** The
snippet above fetches the wheel from PyPI (via jsdelivr's PyPI proxy) over the network at
*first use*. For an app whose whole point is a monthly, possibly-offline review session, a
runtime dependency on PyPI being reachable is a bad idea even if PyPI is reliable in practice.
`micropip` also documented support for local wheels via `emfs:` (Pyodide's in-memory
filesystem) and `file://` URIs — this is the mechanism to use instead:

1. At build time, download `openpyxl`'s (and `et_xmlfile`'s) `.whl` files once and commit them
   as static PWA assets, next to `fina`'s own wheel (see §4.2).
2. At app start, before calling any adapter: `await micropip.install(["emfs:/assets/et_xmlfile-....whl", "emfs:/assets/openpyxl-....whl"], deps=False)`.
3. Nothing ever reaches out to PyPI at runtime; the PWA's service worker caches the wheels the
   same way it caches everything else (§2.3), so this works fully offline after first install.

**Recommendation: bundle the wheel, don't fetch it live.** This is a small addition to the app
shell's startup sequence (a few lines of JS/Python glue), not a change to `src/fina` — the
`import openpyxl` line inside `bank_xlsx.py` is untouched.

### 1.3 Contingency if `openpyxl`-in-Pyodide turns out to be unreliable in practice

Nothing found this session says `openpyxl` fails in Pyodide, but nothing found says a fully
verified end-to-end run exists for *this exact* pattern either (a genuine **[OPEN]** — the
only way to close it is a real spike: install the two wheels above in an actual `loadPyodide()`
page and run `bank_xlsx.parse()` against the project's real fixture file). If that spike turns
up a real defect (not just "PyPI was unreachable," which §1.2's local-wheel approach already
prevents), the options, worst-to-best:

1. **Take the parsing logic itself pure-Python and Pyodide-native**, e.g. a minimal `.xlsx`
   reader (`.xlsx` is a zip of XML; `zipfile` and `xml.etree.ElementTree` are both plain
   stdlib, both trivially Pyodide-compatible). Rejected as a first move: this means
   re-implementing and re-verifying real parsing logic in a second... no, it's still Python,
   so not literally the second-language problem the project is trying to avoid — but it *is*
   new, unverified code standing in for a mature, battle-tested library, for no proven reason.
   Only worth doing if the spike finds an actual `openpyxl` defect this specific replacement
   would sidestep.
2. **Move `.xlsx` parsing to JS** (e.g. via a JS library like SheetJS) and change
   `bank_xlsx.py` to accept pre-parsed rows instead of a file path. Rejected as the default
   plan for the same reason the project chose Pyodide over a JS rewrite in the first place:
   it puts spreadsheet-structure logic (which cell is which, header-block layout, R-7.x rules)
   into a second language, exactly the class of duplication this whole architecture exists to
   avoid — and `bank_xlsx.py`'s own header-block search (`_find_label_value`, see
   `src/fina/adapters/bank_xlsx.py:73`) is precisely that kind of logic. Keep as a documented
   fallback only.
3. **Bundle the wheel (§1.2), unchanged** — the default plan. Nothing here is a fallback to
   it; it already is the recommendation.

---

## 2. Load-time and bundle-size expectations, cold, on a mid-range Android phone

### 2.1 The numbers found **[DOCS]** / **[ESTIMATE]**

Pyodide's own project roadmap states a baseline: *"At present a first load of Pyodide requires
a 6.4 MB download, and the environment initialization takes 4 to 5 seconds."* This figure
recurs verbatim across several versions of that page, which suggests it is a stated design
target/rough baseline rather than a per-release-remeasured benchmark — and it says nothing
about the hardware or connection it assumes; roadmap pages of this kind are typically written
against a developer's desktop machine on broadband, not a mid-range Android phone on mobile
data. Treat "6.4 MB / 4–5 s" as a **floor**, not as the number a real user will see.

What that baseline figure does *not* include:

- `openpyxl` + `et_xmlfile` wheels: small, well under 1 MB combined (`openpyxl` itself is a
  few hundred KB packed).
- `fina`'s own wheel: a pure-Python package with a handful of modules — negligible, tens of
  KB at most.
- The app shell itself (HTML/CSS/JS, the file-picker UI, the chart display) — small, this is
  not a heavy JS framework app by this project's own direction.
- The `fullStdLib` question: `fina` needs `decimal` (in by default, §1.1), `pathlib`, `csv`,
  `hashlib`, `re`, `dataclasses`, `datetime` — all ordinary, always-available stdlib. No
  reason to request `fullStdLib: true` (which pulls in things like `sqlite3` this project
  doesn't use) — every extra MB here is pure waste for a project with zero database use.

**[ESTIMATE]**: total cold download, done right (pyodide-core, not the 200+ MB "full" bundle
that vendors every possible scientific package — that bundle is irrelevant here and must not
be what gets shipped), is on the order of **7–10 MB compressed**. On top of the network
transfer, a mid-range Android phone's CPU is meaningfully slower than a developer workstation
at WASM instantiation and Python interpreter start-up, so the "4–5 s" init figure should also
be expected to grow, not just the download.

### 2.2 Translating that to a real phone, honestly

**[ESTIMATE]**, not measured: at a "normal" mobile connection — anywhere from a good LTE
connection (~10+ Mbps effective) down to a congested urban 4G connection (~2–3 Mbps effective,
a realistic worst case rather than an outlier) — a 7–10 MB download alone costs roughly
2–8 seconds of pure transfer time before any WASM/Python startup cost is added. Combined with
a mid-range phone's slower CPU for the interpreter bring-up, a genuine first-ever cold open
(no service worker cache yet, e.g. right after installing the PWA) landing **somewhere in the
5–20 second range** is the honest expectation, with real variance by device and network — not
the sub-5-second figure the roadmap's own baseline states, and not the alarming end of "so slow
it's unusable" either. **[OPEN]**: the only way to replace this range with a real number is to
build the spike from §1.3 and time it on an actual mid-range Android device on real mobile
data — this document cannot responsibly narrow the range further than that.

### 2.3 Whether that is acceptable for "opened once a month"

This is squarely the case Progressive Web Apps are designed for: a service worker precaches
the Pyodide runtime, the bundled wheels, and the app shell on first successful load, and every
subsequent open — including next month's — is served entirely from cache, with **no network
requirement at all**, not even a fast one. The cost above is paid **once**, at install (or
first use), not on every visit.

**Recommendation: acceptable, on three explicit conditions**, none of which are optional:

1. **Ship the minimal build** — `pyodide-core` plus exactly the wheels this project needs
   (§1.2, §4.2), never the ~200 MB "full" distribution that bundles every scientific package
   Pyodide knows how to compile. Shipping that by accident (e.g. by pointing at the wrong CDN
   path) would turn an acceptable 7–10 MB into a genuinely bad mobile experience.
2. **A first-run progress indicator that says what is happening** ("preparing your ledger
   engine — this happens once"), so a 5–20 second wait reads as expected setup, not a hang.
   This is a UI requirement, not a Pyodide one, but it is load-bearing for whether the
   experience *feels* acceptable — the same seconds with no feedback would not be.
3. **The service worker must actually precache successfully before declaring first-run done**
   — if caching silently fails (e.g. storage quota issues on an old/full phone) the app would
   silently re-pay the full cold-start cost every single month, defeating the entire premise.
   This needs its own test once implementation starts (mirroring this project's existing
   discipline of never asserting a fix works without checking it on the real failure mode —
   see `docs/plan/open-questions.md` Q-J's correction for exactly this lesson).

This would be a materially different, harder call for an app opened many times a day — a
5–20 second cost on *every* open would be unacceptable there, and would push toward a
JS-native rewrite despite the re-verification cost. It is the "once a month" usage pattern
specifically that makes "pay it once, cache it, never again" a genuine answer rather than a
workaround.

---

## 3. Bridging the browser file input to the existing `Path`-based adapters

### 3.1 What the existing code actually expects

Confirmed by reading the source directly (not research — this part is simple fact-checking):

- `src/fina/adapters/broker_csv.py::parse(file_path: Path)` calls `file_path.read_bytes()`.
- `src/fina/adapters/bank_xlsx.py::parse(file_path: Path)` and `::read_header_block(path: Path)`
  call `openpyxl.load_workbook(path, ...)` directly on the `Path`.
- `src/fina/pipeline.py::run_pipeline(input_dir: Path, out_dir: Path | None = None)` discovers
  files under `input_dir` and writes the manifest and (via `cli.py`) the chart HTML under
  `out_dir` — everything is `pathlib.Path`-shaped, filesystem-in, filesystem-out.

None of this code opens a network connection, prompts, or otherwise assumes a real OS
filesystem beyond what `Path`/`open()` provide — which matters, because Pyodide's answer to
"give Python a filesystem" is to actually give it one.

### 3.2 The bridging mechanism — write into Pyodide's virtual filesystem, change nothing in Python **[DOCS]** + design

Pyodide runs on Emscripten, which gives the WASM-compiled CPython a real (in-memory, by
default `MEMFS`) filesystem, visible to `pathlib.Path` and `open()` exactly as any other
filesystem would be — this is not a Pyodide-specific API Python code has to know about; it is
transparent below the `pathlib`/`open()` layer. JS can write into it directly
(`pyodide.FS.writeFile(path, Uint8Array)`, creating directories with `FS.mkdirTree` first).

Concrete flow:

1. **JS**: user picks one or more files via `<input type="file">` (or a share-target intent
   on Android, same underlying `File` objects either way).
2. **JS**: for each `File`, `const bytes = new Uint8Array(await file.arrayBuffer())`, then
   `pyodide.FS.writeFile("/uploads/" + file.name, bytes)` (after `FS.mkdirTree("/uploads")`
   once).
3. **JS → Python**: call `fina.pipeline.run_pipeline(Path("/uploads"), Path("/out"))` — the
   exact same call `cli.py::_build` already makes, unmodified, because `/uploads` and `/out`
   are just paths as far as `pathlib` is concerned.
4. **Python**, unmodified: reads each file under `/uploads`, sniffs the right adapter, writes
   `manifest.json` and (via the same code path `cli.py` uses) `section1_chart.html` under
   `/out`.
5. **JS**: reads the results back out with `pyodide.FS.readFile("/out/section1_chart.html", { encoding: "utf8" })` and injects it into the page; the manifest can be read the same way if
   the UI wants structured numbers rather than only the rendered chart.

**Recommendation: this mechanism, not an adapter-level change**, and explicitly because it is
the option that requires the least change to already-verified code — in fact, zero change:
every line inside `src/fina/adapters/*.py`, `io_utils.py`, and `pipeline.py` runs completely as-
is. The alternative — changing the adapters to accept file-like objects or byte buffers instead
of `Path`, so the JS side could hand them over directly without touching a virtual filesystem —
was considered and rejected specifically because it touches code this project has already
carried through `pytest`/`mutmut`/`mypy`/100% coverage (per the implementation plan's gates),
for a benefit (avoiding a filesystem write that costs microseconds for these file sizes) too
small to justify re-opening that surface. The instruction to prefer least change and say so
explicitly applies exactly here.

### 3.3 A boundary this mechanism does *not* decide, flagged rather than guessed

`MEMFS` is memory-only and disappears when the page/worker is torn down — by design here, that
is fine for `/uploads` and `/out`, since the project's own architecture is already
"recompute from raw files each run, nothing hand-maintained between runs" (see
`docs/technical-decisions.md` §5, the own-accounts registry decision, which leans on exactly
this statelessness). What is genuinely undecided is whether the *user* has to re-supply every
bank/broker export from the beginning every single month, or whether the app should persist
something (raw files, or a serialized ledger) across sessions on-device via `IDBFS`/
`IndexedDB` so a monthly visit only means "add this month's new exports." That is a product
decision, not a research question — logged as Q-M in `docs/plan/open-questions.md`.

---

## 4. Do `pytest`/`mutmut`/`mypy`/`ruff` need to run anywhere new?

**No. Short, definitive answer: they stay exactly as they are today.**

### 4.1 Why this is definitive, not merely likely

Every one of these tools operates on the Python **source tree** (`src/fina`, `tests/`) using
a normal CPython interpreter on the machine running the gate — nothing about that changes by
choosing to *ship* the resulting code through Pyodide afterward. Pyodide's WASM-compiled
CPython is a **deployment target for already-gated code**, not a second place that code lives
or gets edited. There is no version of "run mypy inside the browser" that would mean anything:
mypy needs a type-checking environment and the full source tree with its `.py` files as text,
not a wheel already built and loaded into a running interpreter; `mutmut` needs to mutate and
re-run the source tree, which is a build-machine operation; `ruff` lints source text; `pytest`
needs the test suite and fixtures, none of which ship to the browser at all (see 4.2). None of
the four tools has any browser-relevant mode, and this project has no CI workflow file yet
(checked: no `.yml`/`.yaml` under the repo) to have wired any of this into a browser context
in the first place — there is nothing to migrate away from.

### 4.2 The one new thing this architecture adds — a build step, not a test step

What *is* new is a packaging step after the gates pass, not instead of them: `src/fina` gets
built into a wheel (`python -m build`, same `pyproject.toml` this project already has) and that
wheel — plus `openpyxl`'s and `et_xmlfile`'s wheels (§1.2) — is copied into the PWA's static
assets, to be `micropip.install()`-ed from the local, offline-bundled copies at app start. This
is ordinary "ship the artifact the gates already blessed," the same shape as any project that
lints and tests source before building a release — it just happens that the release artifact
here is a wheel loaded by a WASM interpreter instead of, say, a Docker image. `docs/plan/
test-plan.md`'s gates (§1, G-1..G-9) are unaffected in content, scope, or where they run.

---

## Summary of recommendations

1. `decimal` needs no workaround (confidently sourced); bundle `openpyxl`'s wheel as a static
   asset and `micropip.install()` it from that local copy so nothing depends on PyPI being
   reachable at runtime — do not restructure the bank adapter unless a real spike proves
   `openpyxl`-in-Pyodide broken, which nothing found this session suggests.
2. Expect a one-time cold-start cost in the rough range of 5–20 seconds on a mid-range Android
   phone on mobile data (an estimate, not a measurement) — acceptable for a once-a-month app
   only if the service worker precache actually works, the minimal Pyodide build is shipped
   (never the 200+ MB full distribution), and the first run shows explicit progress UI.
3. Bridge the file picker by writing picked files into Pyodide's virtual filesystem and calling
   `fina.pipeline.run_pipeline()` completely unmodified — this is the option that touches zero
   already-verified code, which is why it's the recommendation and not merely one of several.
4. The full quality-gate suite (`pytest`, `mutmut`, `mypy`, `ruff`) stays exactly as it runs
   today, on the source tree, before any packaging step — nothing about it moves into, or
   needs to reckon with, the browser.

Open product decision raised by this research, logged separately: Q-M in
`docs/plan/open-questions.md` (cross-session persistence of source files / ledger state on
the device).
