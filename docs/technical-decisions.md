# Technical decisions for the master ledger and adapters

Living document. Each entry lists the options considered, which one was adopted, and why — so the decision can be audited later without redoing the analysis.

## 1. `DELIVERY/MIGRATION` rows in the Trade Republic export

### 1.1 The observed fact and its real-world cause

In the real export reviewed, on **2025-06-16** (confirmed by searching the full file: it happens only on this single date, nowhere else), a pair of rows appears for **every open position** (stocks, crypto...) within the same minute: one with the quantity negative, one with the same quantity positive, same ISIN, same price, no `amount` (no cash effect). It happens simultaneously for every asset the user held that day — not an event tied to one asset, but to the whole account.

This is not a generic "custody system change" guess — it matches a documented, dated event. Trade Republic obtained a full ECB banking license in December 2023 and used it through 2025 to open local branches with local IBANs in several EU markets; in Spain this went live with Spanish (ES) IBANs replacing the original German (DE) ones from 5 June 2025 for new customers, with existing customers migrated shortly after via the app. The observed migration date (16 June 2025) falls squarely inside that rollout window, so the `DELIVERY/MIGRATION` pair is Trade Republic re-booking every position under the new Spanish-branch account as part of this account migration — a structural/regulatory rebooking, not a trade. This does not change the conclusion, it confirms it: the event is bank-driven and account-wide, unrelated to any investment decision, so it must never be read as a disposal and reacquisition for cost-basis purposes.

Sources: [Trade Republic accelerates international growth and to open Spanish branch in 2025](https://www.investinspain.org/content/icex-invest/en/noticias-main/2025/trade.html), [Trade Republic receives full banking license from ECB](https://www.businesswire.com/news/home/20231204046066/en/Trade-Republic-receives-full-banking-license-from-ECB), [Trade Republic IBAN español: todo lo que debes saber en 2026](https://www.rankia.com/blog/cuentas-corrientes/6863067-trade-republic-iban-espanol).

### 1.2 Why it matters

A FIFO cost engine that processed these rows literally would close the original purchase lot (with its real date and price) on the outbound row, and open a new lot dated and priced at that same 2025-06-16 migration event. If the migration price does not exactly match the real average cost (in the sample, very close but not guaranteed to always be exact), this silently rewrites the cost basis of every position — exactly what rule 9 in `CLAUDE.md` (cent-level accuracy) forbids.

### 1.3 Options

1. **Filter in the adapter**: the adapter recognizes the pattern (same day + ISIN + price + opposite quantity + DELIVERY category) and drops both rows before they reach the ledger. The original lot stays alive with its real date/price.
   - Pro: the FIFO engine never needs to know this exists.
   - Con: if the recognition pattern ever misses a genuinely different case that looks similar, a row disappears without a trace.
2. **Keep and tag, ignore in calculations** *(adopted)*: both rows enter the ledger as-is (the ledger's row count still matches the broker's export, full traceability), tagged `movement_type = TECHNICAL_ADJUSTMENT`. The FIFO engine has one rule: skip any `TECHNICAL_ADJUSTMENT` row. The original lot keeps its real purchase date and price until the real sale.
   - Pro: full traceability + correct tax cost basis, with a single explicit, visible rule in the ledger instead of a silent adapter decision.
   - Con: one more thing to maintain (the "skip" rule in the calculation engine), but trivial.
3. **Take it at face value**: process each row as a real sale and a real purchase. The simplest to implement, and the one rejected: it silently rewrites the acquisition date and, unless the price matches exactly, the cost basis.

**Decision: option 2.** It combines the traceability of option 3 with the tax correctness of option 1.

## 2. Debt payments for liabilities not tracked in the ledger (loan/mortgage)

The user has explicitly excluded real estate from "financial net worth." That fixes the rule — it is not a convenience simplification:

**General rule**: a debt payment counts as a pure expense if and only if the asset or liability it finances is not on the balance sheet this system tracks. The moment that liability (or the asset securing it) enters scope, the installment must be split into interest (expense) + principal amortization (reduces the liability, not an expense). While the property stays out of scope, the whole installment — "LIQUIDACION PERIODICA PRESTAMO" in the bank export — is an expense, no exception, and the rule is the same for any bank or debt type (mortgage, personal loan, unfollowed car financing): the question to ask is always "is the liability this pays down inside my ledger?", not the name of the bank concept.

This is also recorded as a system limitation in the assumptions list: if the user later decides to bring the property and its mortgage into scope, this rule changes for that specific debt.

## 3. `Fecha operación` vs. `Fecha valor` in Spanish bank statements

Researched because it directly affects cent-level reconciliation (rule 9 in `CLAUDE.md`).

- **Fecha operación (booking date)**: the date the bank records and displays the movement in the history — the order in which statement rows appear, and the order used to compute the running balance shown after each one.
- **Fecha valor (value date)**: the date that determines the economic/financial effect of the movement (relevant for interest accrual). It is regulated — the EU payment-services framework (PSD2) forbids a bank from setting a debit's value date earlier than the actual charge, and requires incoming funds to be credited with a value date no later than their receipt — precisely to stop a bank from moving the date against the customer. Hence the offsets seen: a card payment reported with `Fecha operación` a few days after the real purchase can carry a `Fecha valor` equal to the purchase day itself.
- **Practical consequence for the ledger**: the statement's `Saldo` (balance) column is computed in `Fecha operación` order (that is how the bank lists and accumulates movements), not in `Fecha valor` order. Ordering the ledger by `Fecha valor` would break row-by-row reconciliation against the declared `Saldo`.

**Decision**: the ledger's `date` = `Fecha operación` (the one that reconciles the balance to the cent). `Fecha valor` is stored as an additional field (`value_date`), useful to cross-check against the real merchant charge date or another related statement's settlement date.

## 4. Same-date ordering: `file_sequence`, not `value_date`, not raw `source_row`

Found during implementation (WP-5), when the reconciliation oracle for the bank fixture
turned out to contradict the original wording of R-1.22.

### 4.1 The options considered

R-1.22 originally sorted entries by `(date, source_file, source_row)`. The bank fixture has
two rows sharing `Fecha operación` where that tiebreak gives the wrong order (verified by
hand: reconciliation is off by exactly the second row's amount, not a rounding artifact).

1. Break same-`date` ties with `value_date`. Fixes the two-row case in the fixture.
2. **Break ties with a per-adapter `file_sequence`** *(adopted)* — see 4.2.
3. Edit the fixture so ascending `source_row` already matches. Rejected outright: only the
   project owner may authorize a fixture edit, and doing it to make a check pass is exactly
   the failure mode the project's rules exist to prevent.

Option 1 was the first instinct and is *wrong*: a real production export (seen at the start
of this project, before any fixture was built) contains three rows sharing **both**
`Fecha operación` and `Fecha valor` on the same day. `value_date` cannot break that tie
either. Hand-verifying the three declared balances against each other shows the correct
order is the exact reverse of the rows' physical position in the file — i.e. the file's own
newest-first listing convention *is* the ordering information, once un-reversed.

### 4.2 The adopted rule

Every adapter assigns each entry a `file_sequence: int` such that ascending `file_sequence`
equals true chronological order within that file, regardless of the file's own listing
direction:
- Broker CSV: `file_sequence = source_row` (the export already lists oldest first).
- Bank XLSX: `file_sequence = -source_row` (the export lists newest first; negating reverses
  it without needing any date field).

R-1.22 now sorts by `(date, file_sequence, source_file)`. `source_row` stays a pure
traceability pointer (CLAUDE.md rule 10) with no ordering role of its own — this was also
true before, but the failure made it worth stating explicitly.

### 4.3 Consequence for already-built packages

This is a data-model change (`LedgerEntry` gains a `file_sequence` field), so it reaches
back into WP-2 (`models.py`), WP-4 (`broker_csv.py`), and WP-5 (`bank_xlsx.py`), all
completed before this was found. Each needs a small patch (add the field; set it per R-6.1a
/ R-7.5a) rather than a rewrite — none of their existing rule ranges or oracle numbers
change.

## 5. Own-accounts registry — derived from ingestion, not hand-maintained

### 5.1 Options considered

1. A hand-maintained reference file (`institution, iban, alias, opened_on, status`) edited whenever an account opens or closes.
2. **Derived from the source documents themselves** *(adopted)*: an account is "the user's own" if and only if the user has supplied at least one statement/export for it. Every adapter, in addition to producing `LedgerEntry` rows, extracts an `AccountDeclaration` (`institution, iban_or_account, holder_name, declared_in_file, as_of_date`) from that same file's own metadata (the bank export's header block, the broker export's `CUSTOMER_INBOUND` counterparty/IBAN). The set of own accounts for a run is the union of every `AccountDeclaration` extracted from the files present in that run.

### 5.2 Why option 2

The user only ever hands over documents for accounts that are theirs — so "this IBAN is mine" is never an unverifiable claim, it is backed by the very statement that proves it, satisfying the traceability rule (rule 10, CLAUDE.md) for free. It also removes a second place that needs manual upkeep in sync with the data: updating which accounts exist is implicit in supplying (or no longer supplying) statements for them, consistent with the stateless, recompute-from-raw-files design (§5.1 of the implementation plan).

### 5.3 Consequence for adapters

An adapter's output is therefore `(list[LedgerEntry], list[AccountDeclaration])`, not just ledger rows. The pipeline first collects every `AccountDeclaration` across all files in the run, then re-runs each adapter's internal/external classification against that combined set (a counterparty matches an own account by IBAN; matching by holder name alone is a lower-confidence fallback — see the full spec for the exact matching order).
