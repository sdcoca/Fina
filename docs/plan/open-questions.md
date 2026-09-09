# Open questions

Anything an implementer cannot resolve from the spec goes here instead of being guessed
(`docs/plan/implementation-plan.md` rule I-2, spec R-0.3). One entry per question: context,
the ambiguity, options seen, recommendation. Entries are answered by the project owner or by
the reviewing model — never quietly by the implementer who found them.

## Q-A — Returning money vs. new external income

**Context**: R-3.4 classifies any transfer whose counterparty is not an owned account as
external, and R-9.5 counts every external flow as savings.
**Ambiguity**: a repaid personal loan (money of the user's that comes back) is
indistinguishable from genuinely new income, so it inflates the savings line — the original
brief explicitly warns against counting returning money as new saving.
**Options**: (a) accept the distortion and document it; (b) let the user tag specific
counterparties as "money returning" in a small reference file; (c) infer from matching an
earlier outflow of the same amount to the same counterparty.
**Recommendation**: (b) — explicit, auditable, no inference. Not implemented; awaiting a
decision.

## Q-B — FX conversion parameters

**Context**: R-4.1/R-4.2. No adapter converts currency yet (D5); the US employee-plan broker
will.
**Ambiguity**: rate source, rate date and rounding are unfixed.
**Options**: ECB daily reference rate vs. the broker's own applied rate; rate dated on the
transaction date vs. settlement date.
**Recommendation**: ECB reference rate at the transaction date, rounded per R-1.5, and store
the source's own rate alongside for comparison. To be decided before the employee-plan
adapter is specified.

## Q-C — Credit-card settlement double-count

**Context**: R-7.12. The current-account statement charges the monthly card settlement as an
expense.
**Ambiguity**: if a card-statement adapter is added later, each individual card purchase
would also be ingested, counting the same spending twice (CLAUDE.md rule 13).
**Options**: (a) never ingest card statements, keep the settlement as the expense;
(b) ingest card statements and reclassify the settlement row as an internal transfer.
**Recommendation**: (b) if per-merchant spending detail is ever wanted; (a) otherwise. Must
be decided *before* any card adapter is written, not after.

## Q-D — Reconciling the broker cash balance

**Context**: R-8.4. The broker transaction export carries no running-balance column, so the
computed broker cash balance is unverified against any source document.
**Ambiguity**: which document would close this gap.
**Options**: a periodic account statement PDF; an in-app balance screenshot with a date; a
different export type from the same broker.
**Recommendation**: ask the user whether the broker offers a statement export with a stated
balance; until then the R-8.4 warning stands on every run.

## Q-E — Presenting a cash-only net worth — RESOLVED

**Decision (project owner, confirmed, this session)**: neither (a), (b) nor (c) below as
originally weighed — the project owner instead decided the Section 1 chart should stop
repeating the `cash_only` qualifier inline on every figure (end-of-line labels, every tooltip
row) entirely. Rationale given: at this stage the qualifier repeated on every number is noise,
not clarity; portfolio composition and its own completeness caveats belong in a later,
dedicated report section built for exactly that purpose, not folded into this interim working
chart one figure at a time. This is narrower than option (b) below (which this project had been
running with) and stops short of (a) (this chart is not being omitted) — a fourth option the
original framing didn't anticipate.

**What changed**: `render/section1_chart.py`'s `_tooltip_rows` and `render_section1_chart` no
longer branch on `ChartRow.completeness` at all — the `if row.completeness == "cash_only":`
branches that appended `" (cash_only)"` were removed as dead code once the suffix itself was
removed (not left unreachable behind a pragma). `ChartRow.completeness` itself is unchanged and
still populated by every caller (`fina.render.prepare.to_chart_rows`), since a future caller —
the dedicated completeness section this decision defers to — still needs it; only this module's
own use of the field changed. R-9.4 and R-10.5 were reworded to match (R-9.4 keeps its general
inline-qualifier requirement for every other surface — the CLI line in `cli.py` is unchanged and
still shows it; R-10.5 now records this chart as the one narrower exception, not a repeal).
`tests/test_section1_chart.py`'s T-704 tests were rewritten to assert the qualifier's absence
explicitly (including with `completeness="cash_only"` set, to prove the omission is
unconditional, not merely untested for that value) rather than deleted.

**Original entry, for the record:**

**Context**: R-9.4/R-10.5. Until D1 (price feed) lands, `real_net_worth` covers cash only.
**Ambiguity**: how the two-page report should present this without misleading the reader —
a "net worth" line that silently excludes every open position is exactly the kind of figure
this project exists to prevent.
**Options**: (a) omit the Section 1 chart entirely until D1; (b) plot it labelled
"cash only" throughout; (c) plot positions at cost basis as an explicit interim proxy,
clearly marked as such.
**Recommendation**: (a) for anything shown to the user as a result, (b) for internal
verification runs. (c) is tempting and should be refused: cost basis is not value, and a
plausible-looking wrong number is worse than a missing one.

## Q-F — R-1.22's sort key contradicts the bank fixture's own reconciliation (found in WP-5) — RESOLVED

**Decision (project owner, confirmed)**: option (a) as originally proposed was superseded
before confirmation — a real production export (predating any fixture) contains a three-row
tie on **both** `Fecha operación` and `Fecha valor`, which a `value_date` tiebreak cannot
resolve. The adopted fix is a per-adapter `file_sequence` field instead (broker:
`file_sequence = source_row`; bank: `file_sequence = -source_row`), with R-1.22 now sorting
by `(date, file_sequence, source_file)`. Full rule text in
`docs/spec/section1-ingestion-spec.md` R-1.22/R-6.1a/R-7.5a, rationale in
`docs/technical-decisions.md` §4. This unblocks WP-7 — see implementation-plan.md's WP-7
entry for the required patches to WP-2/WP-4/WP-5 that come with it.

The original analysis is kept below for the record (it correctly identified the problem and
ruled out options (b) and (c); it just proposed the wrong fix for option (a)).



**Context**: `tests/fixtures/banco_ejemplo.xlsx`'s movements table is newest-first and
contains two rows both dated `07/03/2027` (physical/`source_row` 9: "PAGO MOVIL…", Importe
`-12,40`, declared `Saldo` `6.183,75`; `source_row` 10: "COMPRA…", Importe `-64,20`, declared
`Saldo` `6.196,15`). R-7.15 says the bank adapter "MUST sort per R-1.22 before
reconciliation," and R-1.22 fixes the sort key as exactly `(date, source_file, source_row)`
ascending, with no other tiebreaker.

**Ambiguity — this is a direct arithmetic contradiction, not a style question**: I hand-
verified the full balance chain from the fixture (baseline at the oldest row, `source_row`
15, per R-8.3; each subsequent step `previous.declared_balance + current.cash_effect_eur`).
For every row except this one tied pair, `(date, source_row)` ascending reconciles exactly.
For the tied pair, it does not:
- Sorting `(07/03, source_row=9)` before `(07/03, source_row=10)` — i.e. literal R-1.22 —
  gives, after `source_row` 11's balance `6260.35`: `6260.35 + (-12.40) = 6247.95`, but
  `source_row` 9's own declared `Saldo` is `6.183,75`. **Mismatch of 64.20, not a
  rounding-scale discrepancy.**
- Sorting `source_row` 10 before `source_row` 9 (i.e. by *descending* `source_row`, or
  equivalently by ascending `value_date` — `05/03` for row 10 vs. `07/03` for row 9 — as a
  tiebreaker) reconciles exactly: `6260.35 + (-64.20) = 6196.15` (row 10's declared balance),
  then `6196.15 + (-12.40) = 6183.75` (row 9's declared balance), which also matches the
  file's header balance (§12.1's asserted final figure). This is also the order spec §12.1's
  own table lists the two rows in (`-64,20` row before `-12,40` row).
- `docs/technical-decisions.md` §3 explicitly rejects sorting by `Fecha valor` as the
  ordering key generally ("would break row-by-row reconciliation against the declared
  Saldo") — but that warning is about using it as a *primary* key across the whole ledger;
  using it only to break a tie on `Fecha operación` is a narrower claim the doc doesn't
  address, and it is the only tiebreaker I found that reproduces the oracle's own numbers.

So: R-1.22 taken literally reproduces neither §12.1's stated table order nor a reconciling
balance chain for this fixture; a `(date, value_date, source_row)` key does both, but
contradicts R-1.22's literal text (which names only three keys) and stands as an unrequested
guess about which of the two rules — R-1.22 as written, or the oracle's arithmetic — is
authoritative.

**Options**:
(a) Amend R-1.22 to read `(date, value_date, source_file, source_row)` — value_date breaks
    same-date ties before source_row does — and recompute/annotate §12.1 to show this
    explicitly for this pair.
(b) Keep R-1.22 exactly as written and treat this fixture's reconciliation as expected to
    fail for this one adjacent pair — but that contradicts §12.1's flat assertion
    "reconciliation passes with zero discrepancy on every row" and CLAUDE.md rule 9's
    zero-tolerance framing; I do not think this is viable.
(c) Treat this as evidence the fixture (not the rule) needs a tiny edit — e.g. swap the two
    rows' physical position so ascending `source_row` already matches ascending `value_date`
    — but `tests/fixtures/*` is explicitly marked don't-modify, and rule I-3 treats editing a
    fixture to make a check pass as the most serious defect this project recognizes; only the
    project owner should authorize a fixture change, never an implementer.

**Recommendation**: (a). It is the smallest change, it is the one case-by-case rule already
visible in the file's own real-world timestamp behavior (a card settlement's `Fecha valor`
can legitimately precede its `Fecha operación` — see `docs/technical-decisions.md` §3's own
PSD2 discussion of exactly this lag), and it changes zero already-reconciling rows anywhere
else in either fixture (verified by hand above).

**Status / where this leaves the work**: WP-5 (`bank_xlsx.py`) is implemented and its own
test catalogue (T-200..T-223, which does not exercise cross-row reconciliation) is green
using the literal R-1.22 `(date, source_row)` key, since R-1.22/R-7.15 is what WP-5's own
rule range directs and nothing in T-200..T-223 depends on the resolution above. **WP-7
(`reconciliation.py`) is blocked on this question**: T-350 ("Bank fixture reconciles with
zero discrepancy on every row") cannot pass under interpretation (b), and implementing
interpretation (a) or (c) without the project owner's sign-off would be guessing at which
rule is wrong — exactly what R-0.3 forbids, on precisely the rule (R-8.2, zero-tolerance
reconciliation) implementation-plan.md §4.7 names as requiring the most adversarial scrutiny.
I did not implement any reconciliation code while this stood open.

## Q-G — No approved visual mock available for the WP-9 chart (found in WP-9) — RESOLVED

**Decision (project owner, confirmed)**: option (b). The approved mock was never lost — it
existed outside this repository (an Artifact iterated on directly with the project owner,
never committed) and is now saved at `docs/design/section1-approved-mock.html` for exactly
this reason: so no future package ever finds it unreachable again. WP-9's chart must be
re-derived as a genuine port of this file (colours, spacing, translucent-tooltip behaviour,
mobile-first sizing — all already validated against real phone-width renders during design,
per the defect this file's own history records) rather than kept as the independently-designed
version. The independent version was reasonable engineering under the constraint that produced
it, but the constraint no longer holds.

**Original entry, for the record:**

**Context**: R-10.2 fixes the chart's visual contract "validated against the approved mock,"
and `implementation-plan.md`'s WP-9 entry is explicit: "the chart's visual contract (R-10.2)
is fixed by the approved mock; port it, do not redesign it," and even cites a specific
historical defect already found and fixed in that mock (a fixed-width tooltip and
`backdrop-filter` blur that "both looked fine on desktop and were unusable on an actual
phone"). Both documents treat the mock as an existing, authoritative artifact.

**Ambiguity**: no such mock exists anywhere in this working tree, and an exhaustive search
(`git log --all --diff-filter=A` across every branch and tag, plus a full-tree grep for
"mock"/image assets) found no file it could ever have been — it is genuinely inaccessible
from here, not merely hard to find.

**What I did (flagging per CLAUDE.md rule 7, since this departs from an explicit instruction
rather than filling a silent gap)**: rather than stopping WP-9 entirely on a missing asset I
have no way to obtain, I proceeded with an independently-designed chart that satisfies every
literal, independently-testable rule in R-10.1..R-10.5 and R-10.2a/R-10.2b (two series styled
distinctly, a band coloured by `gap`'s sign with an exact linear zero-crossing boundary,
click-only dismissible tooltips that don't wrap at 390px and read the band behind them via
plain alpha — no `backdrop-filter` — final-point value labels, no band legend, no footer,
both theme palettes token-defined, mobile verified first via real headless-browser
screenshots per R-10.2a). None of these rules requires seeing the mock to verify — each is
checked directly against the rendered output (T-700..T-709). What is **not** verified, and
cannot be without the mock, is whether this implementation's actual colours, spacing, and
layout match the approved design the spec assumes exists — only that it does not violate any
rule the spec states explicitly.

**Options for the project owner**:
(a) Accept the independently-designed chart as-is (it is rule-compliant on every literal,
    testable term) and treat "port the mock" as moot until the mock resurfaces.
(b) Supply the approved mock now (file, screenshot, or link) so WP-9's chart can be redone as
    a genuine port rather than an independent design, per the original instruction.
(c) Treat this as blocking and hold WP-9's chart output as provisional/unapproved pending (b),
    even though every other gate (tests, coverage, mutation testing, mypy, ruff) is green.

**Recommendation**: (a) to avoid discarding verified, rule-compliant work over an asset that
was never reachable from this environment, **conditioned on the project owner reviewing the
actual rendered chart** (both the 390px and desktop screenshots) before treating its specific
visual design — as opposed to its rule-compliance — as final; if the owner has the real mock
and sees a meaningful mismatch, redoing the visual design against it is a bounded, low-risk
change (`render/section1_chart.py`'s template/CSS only — no other module depends on its exact
appearance, only on `ChartRow`'s data contract).

**Status**: unresolved — this is a genuine deviation from an explicit instruction, not merely
an unstated gap, so it is not marked RESOLVED and awaits the project owner's choice among the
options above.

**IMPLEMENTED (this session)**: `render/section1_chart.py` was re-derived as a genuine port of
`docs/design/section1-approved-mock.html` — the same colour tokens (`--page`, `--surface`,
`--ink`/`--ink-2`/`--muted`, `--grid`, `--border`, `--line-real`/`--line-ahorro`,
`--gain-fill`/`--gain-line`, `--loss-fill`/`--loss-line`, `--tooltip-bg`) in both light and
dark, IBM Plex Serif/Sans/Mono typography (see Q-I below for the one deliberate deviation —
the actual Google Fonts `<link>` is not ported, only the font-family declarations), the mock's
spacing and layout (960×420 viewBox, gridlines with "Nk" axis labels, every-other-month x-axis
labels, direct end-of-line value labels), the translucent click-to-reveal tooltip with its own
close button and the mock's own month-header/row/bordered-gap-row structure, and the two-line
series legend with no band-colour chips. The interaction model (a single shared hit-area
mapping a click position to the nearest month, rather than one target per point) is also
ported from the mock, replacing WP-9's original one-`circle.hit`-per-point design.

Every literal rule (R-10.1..R-10.5, R-10.2a, R-10.2b) and every T-700..T-709 test still passes,
re-verified against the new markup — see `tests/test_section1_chart.py` and
`tests/test_render_browser.py` (the latter re-run with a real headless Chromium browser at both
390px and desktop, confirming the translucent tooltip, no-wrap text, click-to-open/close, and
PDF export all still work against the ported design). Coverage stayed 100% and mutation testing
was re-run in full: `render/section1_chart.py` alone is 421/430 (97.9%) with the 9 survivors
documented in `docs/plan/surviving-mutants.md` as equivalent mutants (`zip(strict=...)`
variants and one flat-line `or`-fallback, the same patterns already documented elsewhere in
this project) — excluding them, 421/421 = 100%. `ChartRow`'s data contract, `pipeline.py`, and
`cli.py` were unchanged; no money-path module was touched by this port.

## Q-H — `real_net_worth` silently omits any unrecorded pre-ledger opening balance (found in final review, first end-to-end run)

**Context**: found by the reviewing session running `python -m fina build` over both fixtures
together — the first time this had ever been executed; every prior gate (unit tests, 100%
coverage, mutation testing on `section1.py` and `reconciliation.py` individually) was green
and none of them caught it.

**The bug, with real numbers**: the bank fixture's earliest entry (`TRANSFERENCIA`, per R-8.3
the reconciliation baseline) has `declared_balance = 7500.00` immediately after a `+6500.00`
effect — meaning the account genuinely held `1000.00` the instant before that entry, an
amount that predates the ledger's first recorded row for that account and that no entry's
`cash_effect_eur` will ever sum to. `section1.py`'s `cash_balance` (R-9.1, as originally
written) summed `cash_effect_eur` from an assumed zero balance: `6500.00 − 120.50 − 430.00 −
650.25 − 38.90 − 64.20 − 12.40 = 5183.75`, not the true `6183.75`. Running the full pipeline
over both fixtures together produced `real_net_worth (cash_only) = 27121.57`; the true figure
is `21937.82` (broker, correct — its ledger happens to start at account opening) `+ 6183.75`
(bank, true balance) `= 28121.57`. **Silently short by exactly 1000.00**, on a "cash-only"
figure whose entire purpose is to be trustworthy as far as it goes.

**Why 100% coverage and high mutation scores didn't catch it**: `reconciliation.py` validates
the bank fixture's chain correctly (it *has* the right baseline, `7500.00`, as R-8.3
requires) but only raises on mismatch — it never exposed that baseline for reuse.
`section1.py` re-derived a balance independently, using a different, unstated assumption (an
implicit zero starting balance) that only the broker fixture happens to satisfy. Every test in
the original T-400/T-401 catalogue exercised the broker fixture alone, where the assumption is
true by coincidence, so nothing in either module's own test suite could see the two modules
disagreed once combined. This is the exact failure mode implementation-plan.md §4 step 6
(determinism/idempotency) and the general "run the whole pipeline, not just units" principle
exist for — found this session specifically because that step was finally run for real.

**Options**:
(a) Fix `R-9.1` so `cash_balance` is computed relative to the latest reconciliation anchor
    (`reconciliation.py`'s own R-8.3 baseline) plus effects after it, falling back to raw
    summation (still flagged unverified, R-8.4) only when an account has no declared balance
    at all. `reconciliation.py` exposes the anchor lookup; `section1.py` calls it — one
    formula, not two independent derivations of the same fact.
(b) Leave `section1.py` as an independent raw-summation and treat the discrepancy as an
    inherent limitation to document. Rejected: it is not inherent — the correct anchor is
    already computed by `reconciliation.py` and simply not being used; leaving it broken when
    the fix is a straightforward reuse would be choosing convenience over the accuracy rule
    (CLAUDE.md rule 9) this entire project exists to enforce.

**Recommendation**: (a). Already written into the spec (R-9.1, revised) and the test plan
(T-401a/b/c) by the reviewing session. Requires a patch to `WP-7` (`reconciliation.py` exposes
the anchor) and `WP-8` (`section1.py` calls it instead of raw-summing), each re-verified in
full before `WP-9`'s already-built chart is considered to still be operating on correct
numbers — `WP-9` itself does not need code changes for this bug, only re-confirmation once
`WP-8`'s output changes.

**Status**: **RESOLVED / IMPLEMENTED (this session)**. Option (a), confirmed by the project
owner. `reconciliation.py` now exposes `anchor_at(entries, institution, account, as_of)`: the
entry with the latest R-1.22 `sort_key` (also newly exposed, renamed from `_sort_key`) among
all entries for that `(institution, account)` pair carrying a non-`None` `declared_balance` and
dated on or before `as_of`, or `None` when the pair has no declared balance at all (R-8.4's
case). `section1.py`'s `cash_balance` calls it: `anchor.declared_balance + Σ cash_effect_eur`
for entries with a later sort key, dated on or before `as_of`; falls back to the original raw
summation, unchanged, only when `anchor_at` returns `None`. `real_net_worth` was also changed
from a flat sum of every entry's `cash_effect_eur` to `Σ cash_balance(·, t)` over each distinct
`(institution, account)` pair — the two stopped being equivalent the moment any pair could be
non-zero-anchored.

Regression tests T-401a/b/c are implemented in `tests/test_section1.py`, plus new direct unit
tests for `anchor_at` itself in `tests/test_reconciliation.py` (T-362/T-362b/T-362c/T-363) and
additional `cash_balance`/`real_net_worth` edge-case tests closing every mutation-testing gap
the change introduced (see `docs/plan/surviving-mutants.md`'s "Q-H fix" section). Confirmed via
full re-run:
- **Before** (bug): combined run over both fixtures printed `real_net_worth (cash_only) =
  27121.57 EUR` (`21937.82` broker + `5183.75` bank, the anchor-blind raw sum).
- **After** (fixed): `python -m fina build --input tests/fixtures --out <dir>` now prints
  `real_net_worth (cash_only) = 28121.57 EUR` (`21937.82` broker, unchanged + `6183.75` bank,
  now correctly anchored) — an exact +1000.00 correction, matching the fixture's real
  unrecorded pre-ledger opening balance to the cent.
- 100% line+branch coverage maintained on every module; `section1.py` mutation-tested at
  203/205 (99.0%, 2 documented equivalents, 100% excluding them) and `reconciliation.py` at
  93/94 (98.9%, 1 documented equivalent, unchanged from WP-7, 100% excluding it) — both above
  the 95% threshold.
- Two clean runs over the fixtures produce byte-identical output (G-7/R-1.20), confirmed after
  this fix.

`WP-9`'s chart needed no code change for this fix (it already consumes whatever series
`section1.py` produces per R-10.4) and was separately re-derived for Q-G above.

## Q-I — The approved mock loads external fonts; this renderer's own contract is self-contained (found while implementing Q-G)

**Context**: implementing Q-G (porting the approved mock into `render/section1_chart.py`)
surfaced one genuine conflict between the mock's own design and this module's pre-existing,
literal contract, flagged per Q-G's own instruction ("flag any case where the mock's design
actually conflicts with a literal spec rule rather than silently picking one side") rather than
resolved silently.

**The conflict**: `docs/design/section1-approved-mock.html` loads "IBM Plex Serif/Sans/Mono"
via `<link rel="stylesheet" href="https://fonts.googleapis.com/...">`. `render/section1_chart.py`'s
own module docstring (predating this session, present since WP-9) states its output is "a
standalone HTML/SVG document (**no external assets**)" — a real requirement, not decoration:
R-10.1's rationale is that the document must be "suitable for a headless browser to screenshot
or export to PDF," and `tests/browser_support.py` documents, as an established fact about this
project's own environment, that "Playwright's own browser auto-download is unavailable in this
environment (the CDN it uses is blocked by network policy)." A chart-rendering step that
silently depends on reaching `fonts.googleapis.com` at render time would: (a) degrade
inconsistently between environments with and without that access (a determinism risk, R-1.20,
in spirit if not in R-1.20's literal own scope, which is about the *pipeline's* determinism
rather than the renderer specifically), and (b) risk a slow/stalled request in a sandboxed
render environment rather than a clean, fast failure.

Neither the spec (R-10.1..R-10.5) nor `implementation-plan.md`'s WP-9 entry says anything
explicit about network access one way or the other — this is a real gap between what the mock
literally does and what this module's own established contract requires, not a spec rule the
mock violates.

**Options**:
(a) Port the font-family *declarations* verbatim (`"IBM Plex Serif"` / `"IBM Plex Sans"` /
    `"IBM Plex Mono"`, each with the mock's own fallback stack) but omit the `<link>` tag
    itself — the page reads correctly (via its fallback stack) with or without the real
    typeface installed, and never depends on network access to render.
(b) Embed the actual IBM Plex font files as `data:` URIs so the exact typeface always renders,
    fully offline. Requires fetching and embedding several font-weight files (a few hundred KB
    combined), not obtainable from within this session's environment for the same reason (b) is
    blocked in the first place.
(c) Keep the mock's `<link>` tag as-is and accept that it may fail to load (silently degrading
    to the fallback stack) in a network-restricted environment.

**Decision (made this session, not deferred)**: (a). It is the option that is both faithful to
the mock's *typographic intent* (the correct font families are still named, in the same order,
with the same fallback chain) and consistent with this module's pre-existing, load-bearing
"no external assets" contract — no correctness risk either way (this is cosmetic, not
financial), so proceeding without stopping for confirmation does not risk masking an unresolved
correctness issue (the bar this session's instructions set for proceeding on a new ambiguity).
(b) is the ideal end state if the real font files are ever made available to a future session,
but is not achievable here. (c) was rejected as a step backward from this module's own
established reliability contract for no functional gain over (a).

**Status**: RESOLVED (this session) — implemented as (a) in `render/section1_chart.py`; see
that module's own docstring for the same explanation, kept close to the code it describes.

## Q-J — Three real visual defects the automated suite never caught; a systemic test-coverage
gap, not three unrelated bugs (found by the project owner, this session) — RESOLVED

**Context**: the project owner personally rendered `render/section1_chart.py`'s actual output
with a real headless browser — light and dark mode, a 390px mobile width, tooltip open and
closed — and, separately, on a real Android phone. Every gate in `docs/plan/test-plan.md` §1
(100% coverage, high mutation-kill rates, mypy, ruff, the existing T-700..T-709 browser tests)
was green at the time. Three real defects were found anyway:

1. **Tooltip value-column overflow.** The tooltip's `.t-row` value column (`white-space:
   nowrap`) overflowed the tooltip's own box — `max-width: 176px` in CSS, `tipWidth = 176` in
   the page's own script — with no background behind the overflowing text, visually colliding
   with the chart's own end-of-line labels.
2. **SVG end-label overflow.** The SVG's own end-of-line value label text overflowed almost to
   the card's own right edge (measured: under 1px of clearance), triggered by the `(cash_only)`
   suffix (see Q-E above) but not caused by it alone — `_PAD_RIGHT`'s budget was already
   razor-thin before that suffix existed; any month with a large enough `real_net_worth` (more
   digits) could still have overflowed it.
3. **Mobile tap-highlight artifact.** A real touch tap on `.hit-area` on a real Android phone
   shows the browser's default `-webkit-tap-highlight-color` rectangle covering the whole
   element — never seen in any headless-browser screenshot this project had taken, including
   the reviewing session's own, because none of them used a real touch event.

**Root cause (one, not three)**: every existing visual test asserted a narrow, pre-specified
property chosen in advance — "text does not wrap", "translucency is present", "no
`backdrop-filter`" — rather than a general-purpose, content-agnostic one. 100% line/branch
coverage and a high mutation-kill rate on `render/section1_chart.py` only prove that the code
that *was tested* behaves as its own narrow assertions require; neither proves the rendered
output actually fits inside its own containers, because nothing before this session ever
checked that as its own property. Defect 3 has a second, independent cause: no test in this
project, before this session, ever drove a *real* touch event (see below).

**Why no mouse-click-based test could ever have caught defect 3**: `-webkit-tap-highlight-color`
is a mobile-browser affordance that Chromium's rendering path only activates for genuine
touch-type pointer input — a real finger tap, or Playwright's own `page.touchscreen.tap()` in a
browser context created with `has_touch=True`. `page.mouse.click()` — used by every test in
`tests/test_render_browser.py` before this session, including the ones sized at the 390px
mobile viewport — synthesizes a mouse-type pointer event regardless of the viewport's pixel
dimensions; a "mobile-sized" viewport does not, by itself, make Chromium treat a click as a
touch. So this was never a matter of a weaker assertion catching less than a stronger one could
have — every existing test used an event type that structurally cannot reach the code path in
question, on any assertion.

**Fix**:
1. `.tooltip`'s CSS `max-width` and the script's own `tipWidth` were both widened from `176` to
   `230`, sized with real headroom (verified against a synthetic worst-case tooltip — every row
   filled with a 9-digit euro amount and a minus sign, the longest label and month text this
   chart renders) rather than just enough to fit today's fixture figures with the suffix gone.
2. `_PAD_RIGHT` was widened from `104.0` to `128.0`, sized with real headroom the same way
   against a synthetic worst-case end-of-line label.
3. `.hit-area` and `.tooltip-close` both gained `-webkit-tap-highlight-color: transparent` and
   `touch-action: manipulation` (the latter also removes the double-tap-zoom delay on mobile).
4. A new mandatory gate, **G-9** (`docs/plan/test-plan.md` §1), requires every text-bearing
   element's bounding box (via real `getBoundingClientRect()`) to sit fully inside its intended
   container, in both themes, at 390px, in both tooltip states — implemented as `T-710`,
   deliberately assertion-general (it does not hardcode today's copy or figure lengths) so it
   also catches a defect from *any future text change*, not only these two.
5. A new test, `T-711`, drives a real touch tap (`page.touchscreen.tap`, `has_touch=True`) on
   `.hit-area` and `.tooltip-close` and asserts the computed `-webkit-tap-highlight-color` is
   fully transparent in both themes, and that the tap still functionally opens/closes the
   tooltip (so a `touch-action` value that accidentally blocked tapping would also fail it).

Both new tests live in `tests/test_render_browser.py`, alongside T-700..T-709, per this
project's existing file-organization convention for real-browser visual verification (as
opposed to `tests/test_section1_chart.py`'s fast, pure-Python markup/geometry tests).

**Options considered for where to record this**: (a) `docs/technical-decisions.md`, matching
its narrative "options considered → decision → why" pattern for design/testing-methodology
decisions (e.g. §3-4's date-ordering research); (b) a new resolved entry here in
`open-questions.md`, matching Q-G/Q-H's pattern of "bug found on a real render → why the
existing 100%-covered, highly-mutation-tested suite didn't catch it → fix → resolution", which
is exactly this entry's own shape. **Decision**: (b) — this is a single investigative finding
with a concrete before/after fix and a resolution status, not an open-ended narrative rationale
document; Q-G and Q-H are the closer precedent.

**Status**: RESOLVED (this session). All three defects fixed in `render/section1_chart.py`;
G-9 added to `docs/plan/test-plan.md` §1 with `T-710`/`T-711` implementing it in
`tests/test_render_browser.py`; the traceability matrix updated (new rules R-10.6/R-10.7).

### Correction (appended, later session) — the T-711 fix was necessary but not sufficient

**This is a correction to a prior claim of "done" from this same project.** The tap-highlight-
color fix above (defect 3, `T-711`) was reported RESOLVED and verified. It was not fully
verified, and the underlying defect — an orange rectangle around the chart's plot area on a
real touch tap — was still present on a real Android phone after that fix landed. That pattern
(reporting a fix as verified when the verification did not actually reach the real failure
mode) is exactly what this project's owner has now had to catch twice and does not want to
happen a third time; this entry exists to make the correction explicit rather than quietly
folding it into the original, already-RESOLVED entry above.

**What was actually verified the first time, and what it missed**: `T-711` drove a real
`page.touchscreen.tap()` and asserted `getComputedStyle(hitArea).webkitTapHighlightColor` was
fully transparent. That assertion is correct and still holds — `-webkit-tap-highlight-color`
is genuinely suppressed. What `T-711` never checked was `getComputedStyle(hitArea).outline` at
all. A follow-up session reproduced the project owner's real-device report directly: after a
real `page.touchscreen.tap()` (not `page.mouse.click()`) in a headless browser,
`getComputedStyle(hitArea)` showed `-webkit-tap-highlight-color: rgba(0,0,0,0)` (correctly
transparent) **and, separately, also** `outline: rgb(229, 151, 0) auto 5px`, with
`document.activeElement === hitArea` and `hitArea.matches(':focus-visible') === false`.

**Actual root cause**: `.hit-area` carries `tabindex="0"` (needed so the tooltip is reachable
by keyboard). A real touch tap focuses it. The pre-existing CSS only styled
`.hit-area:focus-visible { outline: 2px solid var(--line-real); outline-offset: 2px; }` — it
never addressed plain `:focus`. Chromium's heuristic for whether a given focus event counts as
"focus-visible" does not always trigger for a synthetic/real touch tap; when it doesn't, the
browser falls back to painting its own unstyled native default focus ring
(`outline: auto 5px`, rendered orange on the platform this was caught on), which nothing in the
page's CSS suppressed. This is a **different CSS mechanism entirely** from
`-webkit-tap-highlight-color` — disabling one has no effect on the other, which is exactly why
the original fix left the defect in place while its own test stayed green.

**Fix**: added `.hit-area:focus { outline: none; }` immediately alongside the existing
`.hit-area:focus-visible` rule, which is kept exactly as it was. This is the standard
`:focus-visible` + `:focus { outline: none; }` pairing: `:focus-visible` supplies the intended
ring for genuine keyboard navigation; `:focus { outline: none; }` only suppresses the browser's
unstyled default for focus events not classified as keyboard-driven. Recorded normatively as a
new rule, R-10.8 (`docs/spec/section1-ingestion-spec.md`), since this is a requirement `T-711`
and R-10.7 never stated, not merely an under-tested case of an existing one.

**How this was verified this time — both the touch path and the keyboard path, not just one**,
since checking only the touch path a second time would repeat the same category of mistake in
a smaller way:
1. A real `page.touchscreen.tap()` (`has_touch=True`, `is_mobile=True` context) on `.hit-area`,
   in both light and dark mode at a 390px viewport: confirms `document.activeElement` is the
   hit-area (the focus really does happen) and that the computed `outline` now shows no visible
   ring (`outlineStyle: "none"` or `outlineWidth: "0px"`).
2. Separately, a real `page.keyboard.press("Tab")` navigating to the same element, in both
   themes: confirms `document.activeElement` is the hit-area, `el.matches(':focus-visible')` is
   `true`, and the outline is still visible (non-`"none"` style, non-`"0px"` width) — proving
   the fix did not also blind keyboard users, which a blanket `outline: none` with no
   `:focus-visible` rule would have done, and which nothing about fixing defect 3 the first time
   would have caught either way, since no test before this correction ever drove a keyboard
   event against this element.
3. Fresh screenshots taken (light and dark, tooltip open via a real touch tap) at 390px: no
   orange or otherwise colored rectangle visible around the chart's plot area in either theme.

New regression test `T-712` (`tests/test_render_browser.py`, under the existing G-9 gate)
implements both halves of check 1 and 2 above so this cannot regress silently again. Full gate
suite (mypy --strict, ruff check + format --check, pytest --cov 100%, mutmut) re-run in full
after this fix — see the commit for this correction for the exact numbers.

**Status**: RESOLVED (this correcting session). The original Q-J entry above stays as the
historical record of what was found and fixed at that time; this appended section is the
correction to its "done" claim for defect 3 specifically, per this project's stability rule
(CLAUDE.md rule 12) — the change is identified and explained here rather than silently
overwriting the earlier text.
