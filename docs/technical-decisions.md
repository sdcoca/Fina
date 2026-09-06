# Technical decisions for the master ledger and adapters

Living document. Each entry lists the options considered, which one was adopted, and why — so the decision can be audited later without redoing the analysis.

## 1. `DELIVERY/MIGRATION` rows in the Trade Republic export

### 1.1 The observed fact

In the real export reviewed, on **2025-06-16** (confirmed by searching the full file: it happens only on this single date, nowhere else), a pair of rows appears for **every open position** (stocks, crypto...) within the same minute: one with the quantity negative, one with the same quantity positive, same ISIN, same price, no `amount` (no cash effect). It happens simultaneously for every asset the user held that day — not an event tied to one asset, but to the whole account. Everything points to an internal technical migration by the broker (e.g. a custody-system change), not a real buy/sell: it changes neither what you hold nor your cash.

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

## 4. Own-accounts registry (minimal lifecycle)

To tell an internal transfer (not savings, not an expense) apart from an external flow, every adapter needs to know which IBANs/accounts belong to the user. This is solved with a simple reference table, not business logic scattered across adapters:

`accounts_registry`: `institution, iban_or_account, alias, opened_on, status (active/closed), closed_on`.

Each adapter looks up this table to mark `is_external_flow = false` whenever a movement's counterparty matches an active row. Adding or closing an account is a new row in this registry, not a code change.
