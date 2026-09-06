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
