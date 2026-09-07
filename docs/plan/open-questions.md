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

## Q-E — Presenting a cash-only net worth

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
(`reconciliation.py`) is blocked on this question**: T-350 ("Bank fixture reconciles with
zero discrepancy on every row") cannot pass under interpretation (b), and implementing
interpretation (a) or (c) without the project owner's sign-off would be guessing at which
rule is wrong — exactly what R-0.3 forbids, on precisely the rule (R-8.2, zero-tolerance
reconciliation) implementation-plan.md §4.7 names as requiring the most adversarial scrutiny.
I did not implement any reconciliation code while this stood open.

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

**Status**: awaiting project-owner confirmation of option (a) before implementation resumes.
