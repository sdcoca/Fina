# Specification: master ledger, adapters, reconciliation, and the Section 1 net-worth bridge

Status: draft for external review. This document is meant to be read and checked by a
reviewer (human or model) who has **not** seen the conversation that produced it. Every
field, rule and formula needed to implement and test this slice is written out in full;
nothing is left as "as discussed." Where something is intentionally out of scope for this
iteration, it is marked **DEFERRED** rather than silently omitted.

Related documents:
- `CLAUDE.md` — interaction rules and project-wide quality rules (rule numbers referenced
  below, e.g. "CLAUDE.md rule 9", are from that file).
- `docs/technical-decisions.md` — narrative rationale for the design choices this spec
  formalizes (DELIVERY/MIGRATION handling, debt-payment treatment, fecha operación vs
  fecha valor, the derived accounts registry).

## 0. Scope of this iteration

In scope:
1. The `LedgerEntry` and `AccountDeclaration` data model.
2. Two source adapters: a Trade-Republic-shaped broker CSV export, and a Spanish
   retail-bank XLSX export (Santander-shaped).
3. Per-account balance reconciliation against a declared balance, where the source
   provides one.
4. The Section 1 net-worth-bridge *cash and savings* computation: `savings_flow`,
   `savings_only`, and the ledger-side half of `gap`.
5. Tests against the two fixtures in `tests/fixtures/`.

Explicitly **DEFERRED** (not built this iteration, but the data model must not block them
later):
- D1. Mark-to-market valuation of open positions (`close_price(asset, t)`) — no price feed
  exists yet. Without it, `real_net_worth(t)` cannot be fully computed for accounts holding
  open positions; only its cash component can.
- D2. The per-asset FIFO cost-basis / realized-and-unrealized P&L engine ("Section 7"), and
  therefore the `gap(t)` cross-check against it (decision 5.6, accepted: recommendation B).
- D3. RSU vesting and ESPP purchase contribution to `savings_flow` — no employee-plan
  adapter exists yet. Their formula is written below with a placeholder.
- D4. Incremental/merged ingestion across repeated monthly loads (each run currently
  treats the full set of source files present as the complete, current truth — decision
  5.1, accepted: recommendation A).
- D5. Currency conversion computed by this system (both adapters currently only consume
  the source's own already-converted EUR `amount` field; see §4).

## 1. Global conventions

1.1. **Numeric types.** Every monetary value is `decimal.Decimal`, never `float`, at any
     stage of the pipeline (CLAUDE.md rule 9). A test must fail the build if a `float`
     reaches a money-bearing field (see §11.5).

1.2. **Precision.**
     - Monetary amounts reported in EUR: stored at full precision as parsed, **never
       pre-rounded**; rounding to 2 decimals (`ROUND_HALF_UP`) happens only at the point a
       value is *displayed* or *summed into a final report figure*, never in intermediate
       storage. Source files in this iteration already carry amounts at 2 decimals, so no
       rounding decision is actually exercised yet — this rule exists so it is not
       improvised later when a source with more precision appears.
     - Share/unit quantities: `Decimal`, preserved at the source's own precision (observed
       up to 10 decimal places), never rounded.
     - FX rates: `Decimal`, preserved at the source's own precision (observed at 6 decimal
       places).

1.3. **Dates.** `date` fields are `datetime.date` (no time-of-day) unless explicitly noted
     as a timestamp. Source timestamps (ISO-8601 with `Z`) are parsed but only their date
     part is used for ledger dating; the full timestamp is retained in
     `LedgerEntry.source_timestamp` for tie-breaking same-day ordering and for audit.

1.4. **Currency codes.** ISO 4217, upper-case (`EUR`, `USD`).

1.5. **Identifiers.** `institution` is a short fixed string per source system
     (`"trade_republic"`, `"bank_es"` for this iteration — see §6 and §7). IBANs are
     stored upper-case with no spaces.

1.6. **Source of truth for amounts.** Adapters **never** recompute an amount from
     `quantity × price`. The source file's own amount field is authoritative, even when it
     differs from `quantity × price` by a cent due to the source's own rounding or
     micro-fees. Recomputing and silently "fixing" it would violate CLAUDE.md rule 9
     (cent-level accuracy against the source document) by substituting our own number for
     the document's.

## 2. Data model

### 2.1 `LedgerEntry`

| Field | Type | Nullable | Description |
|---|---|---|---|
| `entry_id` | `str` (UUID) | no | Stable identifier. For rows with a source transaction id, derived from it; otherwise a deterministic hash of `(institution, account, source_file, source_row)`. |
| `date` | `date` | no | Ledger date. See §1.3 and, for the bank adapter, §7.5 for which source column feeds this. |
| `value_date` | `date` | yes | Economic value date, when the source distinguishes it (bank adapter only — §7.5). |
| `source_timestamp` | `datetime` \| `None` | yes | Full source timestamp when available (broker adapter). |
| `institution` | `str` | no | See §1.5. |
| `account` | `str` | no | Sub-account within the institution, e.g. `"cash"` or `"positions"` for the broker, `"current_account"` for the bank. |
| `movement_type` | `MovementType` | no | See §2.2. |
| `asset` | `str` \| `None` | yes | ISIN or ticker/symbol. `None` for pure cash movements. |
| `asset_class` | `str` \| `None` | yes | `STOCK`, `CRYPTO`, `BOND`, `MUTUAL_FUND`, or `None` for cash. |
| `quantity` | `Decimal` \| `None` | yes | Signed. Positive = increases holdings, negative = decreases. `None` for pure cash movements. |
| `unit_price` | `Decimal` \| `None` | yes | Price per unit in the movement's trade currency. `None` when not applicable (e.g. transfers). |
| `currency` | `str` | no | Currency of `amount_eur` — always `EUR` for both adapters in this iteration (see §1.6/§4). |
| `amount_eur` | `Decimal` | no | The source's own amount field, taken as-is (see §1.6). Signed: positive = cash in, negative = cash out. This is *not yet* the full cash effect — see `cash_effect_eur`. |
| `fee_eur` | `Decimal` \| `None` | yes | Source's fee field, when present. Signed (observed always ≤ 0, i.e. a cost). |
| `tax_eur` | `Decimal` \| `None` | yes | Source's tax field, when present. **Informational only** — see §2.4; not added into `cash_effect_eur`. |
| `original_amount` | `Decimal` \| `None` | yes | Pre-conversion amount in `original_currency`, when the source reports one (broker dividend rows). |
| `original_currency` | `str` \| `None` | yes | ISO code of `original_amount`. |
| `fx_rate` | `Decimal` \| `None` | yes | Rate the source used to derive `amount_eur` from `original_amount`. Stored for audit; not used to recompute anything (§1.6). |
| `cash_effect_eur` | `Decimal` | no | **Computed field** — the actual signed change to the account's cash balance caused by this row. Formula in §2.3. This is the field reconciliation and savings calculations use — never `amount_eur` alone. |
| `declared_balance` | `Decimal` \| `None` | yes | The balance the source document claims *after* this row, when it provides one (bank adapter: yes; broker adapter: no — see §8.2). |
| `counterparty_name` | `str` \| `None` | yes | As given by the source. |
| `counterparty_iban` | `str` \| `None` | yes | As given by the source, normalized per §1.5. |
| `is_external_flow` | `bool` \| `None` | yes until classified | Whether this movement crosses the boundary of the user's own accounts. `None` for movement types where the concept does not apply (`BUY`, `SELL`, `DIVIDEND`, `INTEREST`, `TECHNICAL_ADJUSTMENT`), `True`/`False` for transfer-shaped types once classified against the accounts registry (§5). |
| `status` | `"actual"` \| `"estimated"` | no | CLAUDE.md rule 11. Both adapters in this iteration only ever produce `"actual"` (every field comes directly from a source document); `"estimated"` is reserved for future adapters that must infer a value. |
| `source_file` | `str` | no | File name the row was parsed from. |
| `source_row` | `int` | no | 1-indexed row/line number within that file, for a human to find it in under a minute (CLAUDE.md rule 10). |
| `raw` | `dict` | no | The original row, verbatim, as a dict of source column → string value, before any parsing. Kept for audit even after typed fields are derived. |

### 2.2 `MovementType`

| Value | Meaning | Cash-affecting? | Quantity-affecting? | Contributes to `savings_flow`? |
|---|---|---|---|---|
| `EXTERNAL_DEPOSIT` | Cash entering from outside the user's own accounts | yes | no | yes |
| `EXTERNAL_WITHDRAWAL` | Cash leaving to outside the user's own accounts | yes | no | yes |
| `INTERNAL_TRANSFER_IN` | Cash arriving from another account of the same user | yes | no | **no** |
| `INTERNAL_TRANSFER_OUT` | Cash leaving to another account of the same user | yes | no | **no** |
| `BUY` | Purchase of a security/asset | yes (outflow) | yes (+) | no |
| `SELL` | Disposal of a security/asset | yes (inflow) | yes (−) | no |
| `DIVIDEND` | Cash dividend received | yes | no | no |
| `INTEREST` | Interest received | yes | no | no |
| `EXPENSE` | Bank-side spending (card payment, direct debit, etc.) | yes (outflow) | no | yes |
| `PAYROLL_INCOME` | Salary credited (bank side, when identifiable) | yes | no | yes |
| `RSU_VESTING` | RSU vests — **DEFERRED (D3)**, no adapter produces this yet | n/a | n/a | yes, at fair market value on vesting date, once D3 is built |
| `ESPP_PURCHASE` | ESPP purchase at payroll-deduction date — **DEFERRED (D3)** | n/a | n/a | yes, at contribution + discount value, once D3 is built |
| `TECHNICAL_ADJUSTMENT` | Non-economic re-booking by the source institution (§9 of `technical-decisions.md`) | no (forced to 0) | **no — must be skipped entirely**, not merely netted | no |

### 2.3 `cash_effect_eur` formula

```
cash_effect_eur(row) =
    0                                    if movement_type == TECHNICAL_ADJUSTMENT
    amount_eur + coalesce(fee_eur, 0)    if movement_type in {BUY, SELL}
    amount_eur                           otherwise
```

Verified against the real source data (not invented): a Trade Republic BUY split across a
whole-share row (`amount=-3963.60`, `fee=-1.00`) and a fractional-remainder row
(`amount=-36.40`, `fee=""`) for the same logical trade totals `-3963.60 - 1.00 - 36.40 =
-4001.00` for 12.110202 shares at 330.30, i.e. `12.110202 × 330.30 ≈ 4000.99`, confirming
fee is a real, additional cash outflow on top of `amount`, charged once per logical trade
(the whole-share leg carries it; the fractional leg does not).

For dividends, `tax_eur` is **not** subtracted again: a sample row has
`original_amount=24.44 USD`, `fx_rate=0.876424`, `tax_eur=-3.22`, `amount_eur=21.42`, and
`24.44 × 0.876424 = 21.42` exactly — i.e. `amount_eur` is already net of the withholding tax
at source; `tax_eur` is retained only as fiscal metadata (for the future foreign-tax-credit
computation in the tax module), never added into `cash_effect_eur`.

### 2.4 `AccountDeclaration`

| Field | Type | Nullable | Description |
|---|---|---|---|
| `institution` | `str` | no | Matches `LedgerEntry.institution`. |
| `iban_or_account` | `str` \| `None` | yes | Normalized IBAN when the source states one for *this* account. May be `None` for a broker account that has no IBAN of its own in this export type (see §6.5). |
| `holder_name` | `str` | no | As given by the source. |
| `declared_in_file` | `str` | no | Source file name. |
| `as_of_date` | `date` | no | The date the source document's own header/metadata claims to be current as of. |

Rationale for deriving this from ingested files instead of a hand-maintained registry: see
`docs/technical-decisions.md` §4.

## 3. Accounts registry and internal/external classification

3.1. For a given run, `owned_accounts = union of AccountDeclaration across every file in
     that run`.

3.2. For any `LedgerEntry` whose `movement_type` is transfer-shaped
     (`EXTERNAL_DEPOSIT`/`EXTERNAL_WITHDRAWAL`/`INTERNAL_TRANSFER_IN`/`INTERNAL_TRANSFER_OUT`
     — the adapter's *initial* guess, before this classification runs, treats every such row
     as external by default), classification proceeds in this order:
     1. If `counterparty_iban` is non-empty and matches `iban_or_account` of any entry in
        `owned_accounts` (case-insensitive, spaces stripped) → **internal**.
     2. Else, if `counterparty_iban` is empty and `counterparty_name` matches
        `holder_name` of any entry in `owned_accounts` (case-insensitive, diacritics
        normalized, word order ignored) → **internal** (lower-confidence fallback, used
        because the broker's own account has no IBAN of its own to match against — see
        §6.5).
     3. Otherwise → **external**.
     Internal matches are relabeled `INTERNAL_TRANSFER_IN`/`_OUT`; external matches keep
     `EXTERNAL_DEPOSIT`/`EXTERNAL_WITHDRAWAL`. `is_external_flow` is set to `False`/`True`
     accordingly.

3.3. **Open question (not resolved by this spec — see §12, item Q-A):** an external inflow
     from a named third party (e.g. a repaid personal loan) is currently indistinguishable
     from a genuine one-off gift; both are classified `EXTERNAL_DEPOSIT` and both currently
     count fully toward `savings_flow`. This may double-count "your own money coming back"
     as new savings, which the original brief explicitly warns against. Flagged, not solved,
     here.

## 4. Currency conversion

Both adapters in this iteration only ever consume an `amount_eur` (or, for the bank
adapter, an amount already stated in EUR) that the *source* has already converted. Neither
adapter performs its own FX conversion (D5). When a future source provides amounts only in
a foreign currency with no pre-computed EUR figure, this spec must be extended with: the
FX rate source (proposed: ECB daily reference rate), the rate date (proposed: the
`date` of the transaction, per Spanish IRPF practice), and the rounding rule (proposed:
`ROUND_HALF_UP` to 2 decimals, applied once, at the point EUR is produced — not
re-derived later). This is a placeholder, not a decision — flagged in §12 (Q-B).

## 5. Adapter contract

```
Adapter.parse(file_path: Path) -> AdapterResult

AdapterResult:
    entries: list[LedgerEntry]
    accounts: list[AccountDeclaration]
    warnings: list[str]   # non-fatal parsing issues, surfaced to the user, never silently dropped
```

An adapter must raise (not warn) if a row cannot be classified into any known
`movement_type` — CLAUDE.md rule 15 (a mismatch must be surfaced before results are
presented, never absorbed).

## 6. Adapter: Trade Republic broker CSV

### 6.1 Source columns

| Column | Type as given | Used for |
|---|---|---|
| `datetime` | ISO-8601 timestamp, `Z` suffix | `source_timestamp` |
| `date` | `YYYY-MM-DD` | `date` |
| `account_type` | constant `"DEFAULT"` in all observed rows | not used |
| `category` | `CASH` \| `TRADING` \| `DELIVERY` | combined with `type` — see §6.2 |
| `type` | e.g. `CUSTOMER_INBOUND`, `BUY`, `SELL`, `DIVIDEND`, `INTEREST_PAYMENT`, `MIGRATION`, `TRANSFER_INBOUND`, `TRANSFER_OUTBOUND`, `TRANSFER_INSTANT_INBOUND`, `TRANSFER_INSTANT_OUTBOUND` | combined with `category` — see §6.2 |
| `asset_class` | `STOCK` \| `CRYPTO` \| `BOND` \| `MUTUAL_FUND` \| empty | `asset_class` |
| `name` | asset display name, or (only on `CUSTOMER_INBOUND` rows) the account holder's own name | `raw` only; not a typed field |
| `symbol` | ISIN or ticker | `asset` |
| `shares` | signed decimal string, up to 10 dp | `quantity` |
| `price` | decimal string | `unit_price` |
| `amount` | signed decimal string, 2 dp | `amount_eur` |
| `fee` | signed decimal string or empty | `fee_eur` |
| `tax` | signed decimal string or empty | `tax_eur` |
| `currency` | ISO code, observed always `EUR` | `currency` |
| `original_amount` | decimal string or empty | `original_amount` |
| `original_currency` | ISO code or empty | `original_currency` |
| `fx_rate` | decimal string or empty | `fx_rate` |
| `description` | free text | `raw` only |
| `transaction_id` | UUID | seed for `entry_id` |
| `counterparty_name` | free text or empty | `counterparty_name` |
| `counterparty_iban` | IBAN or empty | `counterparty_iban` |
| `payment_reference` | free text or empty, unused in all observed rows | `raw` only |
| `mcc_code` | empty in all observed rows | `raw` only |

### 6.2 `(category, type)` → `movement_type` mapping

| `category` | `type` | → `movement_type` | Notes |
|---|---|---|---|
| `CASH` | `CUSTOMER_INBOUND` | `EXTERNAL_DEPOSIT` (pre-classification; see §3) | Despite the name, this is frequently the user's own bank-to-broker funding and is reclassified `INTERNAL_TRANSFER_IN` once matched — §3.2 rule 1/2 overrides the raw `type` label. |
| `CASH` | `INTEREST_PAYMENT` | `INTEREST` | |
| `CASH` | `DIVIDEND` | `DIVIDEND` | |
| `CASH` | `TRANSFER_INBOUND` | `EXTERNAL_DEPOSIT` (pre-classification) | |
| `CASH` | `TRANSFER_INSTANT_INBOUND` | `EXTERNAL_DEPOSIT` (pre-classification) | |
| `CASH` | `TRANSFER_OUTBOUND` | `EXTERNAL_WITHDRAWAL` (pre-classification) | |
| `CASH` | `TRANSFER_INSTANT_OUTBOUND` | `EXTERNAL_WITHDRAWAL` (pre-classification) | |
| `TRADING` | `BUY` | `BUY` | |
| `TRADING` | `SELL` | `SELL` | |
| `DELIVERY` | `MIGRATION` | `TECHNICAL_ADJUSTMENT` | See §6.4. |

Any `(category, type)` pair not in this table must raise per §5, not be guessed at.

### 6.3 Cash effect

As defined generically in §2.3; no broker-specific exception.

### 6.4 `MIGRATION` pairing algorithm

```
group all rows with category == DELIVERY and type == MIGRATION by (date, asset)
for each group:
    assert len(group) == 2, else raise (an unpaired MIGRATION row is a data
        problem to surface, not to silently half-process)
    assert group[0].quantity == -group[1].quantity, else raise
    assert group[0].unit_price == group[1].unit_price, else raise
    assert group[0].amount_eur in (None, 0) and group[1].amount_eur in (None, 0), else raise
    tag both rows movement_type = TECHNICAL_ADJUSTMENT
```

Both rows are kept in `entries` (full traceability — row count matches the source file
exactly) but the holdings/FIFO computation (§2.2 table, "Quantity-affecting?" column) must
skip `TECHNICAL_ADJUSTMENT` rows entirely, not merely treat their net quantity as zero — the
distinction matters once a per-lot FIFO engine exists (D2), which must never open or close
a lot because of these rows. See `docs/technical-decisions.md` §1 for why (documented,
dated real-world cause: Trade Republic's German→Spanish IBAN/branch migration, June 2025).

### 6.5 `AccountDeclaration` extraction

This export format carries no IBAN for the broker account itself. The declaration is
therefore:

```
AccountDeclaration(
    institution="trade_republic",
    iban_or_account=None,
    holder_name=<name field from the first CUSTOMER_INBOUND row where
                 counterparty_name == name>,
    declared_in_file=<file name>,
    as_of_date=<max(date) across all rows in the file>,
)
```

This is why §3.2 needs a name-matching fallback (rule 2): without it, no broker-side
transfer could ever be classified internal.

### 6.6 Internal/external classification

Delegated entirely to §3 using the `AccountDeclaration` from §6.5 plus whatever the bank
adapter (§7) contributes in the same run.

## 7. Adapter: Spanish retail-bank XLSX (Santander-shaped)

### 7.1 Header block

Fixed relative layout, read from the first worksheet, 1-indexed rows as they appear in the
file (blank cells omitted below):

| Row | Column C | Column D |
|---|---|---|
| 1 | `"Cuenta"` (label) | `"Fecha"` (label) |
| 2 | IBAN | export timestamp, format `DD/MM/YYYY | HH:MM:SS` |
| 3 | `"Titular"` (label) | `"Saldo"` (label) |
| 4 | holder full name | current balance, format `"1.234,56€ EUR"` |

The movements table header (`"Fecha operación"`, `"Fecha valor"`, `"Concepto"`,
`"Importe"`, `"Saldo"`, `"Divisa"`) is located by searching for that exact row rather than
a fixed row offset, since the number of blank/title rows before it may vary between
exports.

### 7.2 Movements table columns

| Column | Format | Used for |
|---|---|---|
| `Fecha operación` | `DD/MM/YYYY` | `date` — see §7.5 |
| `Fecha valor` | `DD/MM/YYYY` | `value_date` |
| `Concepto` | free text | categorization input, §7.4; kept verbatim in `raw` |
| `Importe` | `"-12,34€"` / `"12,34€"` | parsed per §7.3 → `amount_eur` |
| `Saldo` | same format as `Importe`, always non-negative in samples seen | parsed per §7.3 → `declared_balance` |
| `Divisa` | ISO code, observed always `EUR` | `currency` |

Rows are listed most-recent-first; the parser must not assume chronological order and must
sort by `(date, row position)` before any running-balance computation.

### 7.3 Spanish amount string parsing

Format: `[-]D{1,3}(.DDD)*,DD€` (thousands separator `.`, decimal separator `,`, trailing
`€`, optional leading `-`). Algorithm:

```
strip trailing "€" and surrounding whitespace
strip "." (thousands separator)
replace "," with "."
parse as Decimal
```

Must be exact — this is exactly the kind of silent-off-by-a-power-of-ten bug CLAUDE.md
rule 9 exists to catch, so a unit test asserts this parser against at least: a value with
thousands separator, a value without one, a negative value, and a zero-cents value (e.g.
`"1.126,00€"`).

### 7.4 `Concepto` categorization ruleset

Applied as ordered, first-match-wins regex rules (case-insensitive) against `Concepto`:

| Pattern (regex, illustrative) | → `movement_type` |
|---|---|
| `^PAGO MOVIL EN` | `EXPENSE` |
| `^TRANSACCION CONTACTLESS EN` | `EXPENSE` |
| `^COMPRA .+TARJETA` | `EXPENSE` |
| `^RECIBO ` | `EXPENSE` |
| `^LIQUIDACION PERIODICA PRESTAMO` | `EXPENSE` — see `technical-decisions.md` §2 for why this is correct, not a simplification |
| `^LIQUIDACION DE LAS TARJETAS DE CREDITO` | `EXPENSE` — **flagged risk, not yet resolved (§12, Q-C)**: if the card's own purchases are *also* ingested from a separate card statement in the future, this row and those purchases would double-count the same spending under CLAUDE.md rule 13. Out of scope this iteration (no card-statement adapter exists yet), but the rule must be revisited before one is built. |
| `^TRANSFERENCIA DE (.+?), CONCEPTO` | `EXTERNAL_DEPOSIT` (pre-classification; capture group 1 → `counterparty_name`, no `counterparty_iban` given by this format) |
| Anything else, amount > 0 | raise — do not guess |
| Anything else, amount < 0 | raise — do not guess |

A rule that raises on the unmatched cases (rather than defaulting to, say, `EXPENSE`) is
deliberate: an unrecognized concept string is exactly the kind of thing CLAUDE.md rule 15
requires to surface, not absorb.

### 7.5 Date field decision

`date = Fecha operación`, `value_date = Fecha valor`. Rationale and the regulatory context
for why these two dates can differ are in `docs/technical-decisions.md` §3. This choice is
required for §8's reconciliation to work: `Saldo` is stated in `Fecha operación` order.

### 7.6 `AccountDeclaration` extraction

```
AccountDeclaration(
    institution="bank_es",
    iban_or_account=<header row 2, column C, spaces stripped>,
    holder_name=<header row 4, column C>,
    declared_in_file=<file name>,
    as_of_date=<parsed from header row 2, column D>,
)
```

## 8. Reconciliation

### 8.1 Algorithm (bank adapter)

```
sort entries for the account by (date, source_row) ascending
running_balance = None
for entry in entries:
    if running_balance is not None:
        expected = running_balance + entry.cash_effect_eur
        assert expected == entry.declared_balance, else raise with:
            (source_file, source_row, expected, declared_balance, expected - declared_balance)
    running_balance = entry.declared_balance
```

Tolerance is **zero** — CLAUDE.md rule 9 leaves no room for an epsilon. The very first row
of a file reconciles trivially (no prior `running_balance` to check against); the account's
balance *before* the file's earliest row is not claimed or verified by this file alone (see
§8.2).

### 8.2 Broker adapter: no declared balance available

The Trade Republic export format used here carries **no running-balance column at all** —
unlike the bank export, there is nothing in this file to reconcile the computed cash
balance against. This is a genuine, current limitation (CLAUDE.md rule 11): the ledger's
computed `cash_balance(institution="trade_republic", t)` is, for now, **unverified** against
an independent source. It would be resolved by a periodic account statement (PDF or
in-app screenshot showing "cash balance as of `<date>`") that the user supplies separately
— flagged in §12 (Q-D), not solved here.

## 9. Section 1: the net-worth bridge

Let `M` be the set of all `LedgerEntry` rows across every ingested account for a given run.

### 9.1 Cash balance per account

```
cash_balance(a, t) = Σ { m.cash_effect_eur : m ∈ M, m.account == a, m.date ≤ t }
```

### 9.2 Holdings per account/asset

```
quantity_held(a, p, t) = Σ { m.quantity : m ∈ M, m.account == a, m.asset == p,
                              m.date ≤ t, m.movement_type != TECHNICAL_ADJUSTMENT }
```

### 9.3 Real net worth — **partially deferred (D1)**

```
real_net_worth(t) = Σ_a cash_balance(a, t)
                   + Σ_{a,p} quantity_held(a, p, t) × close_price(p, t)      [D1: close_price not yet available]
```

This iteration can compute the first term (`Σ cash_balance`) exactly from the two
adapters built. The second term requires a price feed that does not exist yet; until it
does, `real_net_worth(t)` **must be reported as partial** (cash-only) rather than silently
treated as complete — the output must carry a flag distinguishing "full" from
"cash-only" net worth so a future report never mixes the two without saying so.

### 9.4 `savings_flow` (monthly)

```
savings_flow(t) = Σ { contribution(m) : m ∈ M, m.date ∈ month(t) }

contribution(m) =
    m.cash_effect_eur   if m.movement_type ∈ {EXTERNAL_DEPOSIT, EXTERNAL_WITHDRAWAL,
                                                EXPENSE, PAYROLL_INCOME}
                           and m.is_external_flow == True
    vesting_value(m)     if m.movement_type == RSU_VESTING          [D3 — not yet produced]
    contribution_value(m) if m.movement_type == ESPP_PURCHASE       [D3 — not yet produced]
    0                    otherwise (BUY, SELL, DIVIDEND, INTEREST,
                                     INTERNAL_TRANSFER_IN/OUT, TECHNICAL_ADJUSTMENT)
```

### 9.5 `savings_only` and `gap`

```
t0 = the earliest month with any entry in M
savings_only(t0) = real_net_worth(t0)          [subject to the D1 caveat in §9.3]
savings_only(t)  = savings_only(t - 1 month) + savings_flow(t)     for t > t0

gap(t) = real_net_worth(t) − savings_only(t)
```

### 9.6 Cross-check against the per-asset view — **DEFERRED (D2)**

Once the FIFO engine exists:

```
gap(t) ?= Σ_{m ∈ M, m.date ≤ t} realized_pl(m)
        + Σ_{a,p} unrealized_pl(a, p, t)
        + Σ_{m ∈ M, m.date ≤ t, m.movement_type ∈ {DIVIDEND, INTEREST}} m.cash_effect_eur
        − Σ_{m ∈ M, m.date ≤ t} coalesce(m.fee_eur, 0)
```

to be asserted exactly equal, per decision §1 (option 2) in `technical-decisions.md`; not
implemented this iteration (recommendation 5.6.B, accepted).

## 10. Worked oracle against the fixtures

These numbers are computed by hand from the current fixture files and must be reproduced
exactly by the implementation's tests. If a fixture file is edited, this section must be
recomputed and updated in the same change.

**Note:** the two fixtures use non-overlapping, independently-chosen date ranges (broker:
2023, bank: 2027) — they were built as independent format samples for two different
adapters, not as one coherent user history. The cross-file IBAN-matching example in §10.3
is illustrated at the identity level (string equality of the IBAN), independent of dates.

### 10.1 `tests/fixtures/banco_ejemplo.xlsx` — reconciliation

Chronological order (oldest first), each row's declared `Saldo` must equal the previous
row's `Saldo` plus this row's `Importe`:

| Date (Fecha operación) | Importe | Saldo (declared) |
|---|---|---|
| 01/03/2027 | +6.500,00€ | 7.500,00€ |
| 02/03/2027 | −120,50€ | 7.379,50€ |
| 03/03/2027 | −430,00€ | 6.949,50€ |
| 04/03/2027 | −650,25€ | 6.299,25€ |
| 05/03/2027 | −38,90€ | 6.260,35€ |
| 07/03/2027 | −64,20€ | 6.196,15€ |
| 07/03/2027 | −12,40€ | 6.183,75€ |

Expected test result: reconciliation passes with **zero** discrepancy on every row; final
declared balance `6.183,75€` matches the header block's stated current balance exactly.

### 10.2 `tests/fixtures/broker_ejemplo.csv` — cash balance and holdings

Running cash balance (chronological; no declared balance exists in this format — see
§8.2 — so this is an internal-consistency assertion, not a reconciliation):

| # | Movement | cash_effect_eur | Running cash |
|---|---|---|---|
| 1 | `CUSTOMER_INBOUND` +8000.00 | +8000.00 | 8000.00 |
| 2 | `BUY` IBM 10 @ 200.00, fee −1.00 | −2001.00 | 5999.00 |
| 3 | `INTEREST_PAYMENT` +37.10 | +37.10 | 6036.10 |
| 4 | `BUY` MSFT 10 @ 310.50, fee −1.00 | −3106.00 | 2930.10 |
| 5 | `BUY` MSFT 0.15 @ 310.50, no fee | −46.58 | 2883.52 |
| 6 | `DIVIDEND` IBM | +15.30 | 2898.82 |
| 7 | `CUSTOMER_INBOUND` +12000.00 | +12000.00 | 14898.82 |
| 8–9 | `MIGRATION` pair (IBM) | 0 | 14898.82 |
| 10 | `TRANSFER_INBOUND` +5000.00 (own bank) | +5000.00 | 19898.82 |
| 11 | `BUY` BTC 0.03 @ 44500.00, fee −1.00 | −1336.00 | 18562.82 |
| 12 | `SELL` BTC 0.01 @ 45000.00, fee −1.00 | +449.00 | 19011.82 |
| 13 | `BUY` BTC 0.002 @ 44000.00, no fee | −88.00 | 18923.82 |
| 14 | `TRANSFER_INSTANT_OUTBOUND` −80.00 (own bank) | −80.00 | 18843.82 |
| 15 | `TRANSFER_OUTBOUND` −3000.00 (external) | −3000.00 | 15843.82 |
| 16 | `BUY` bond 50 @ 1.00, fee −1.00 | −51.00 | 15792.82 |
| 17 | `BUY` fund 30 @ 11.80, fee −1.00 | −355.00 | 15437.82 |
| 18 | `TRANSFER_INSTANT_INBOUND` +6500.00 (external) | +6500.00 | 21937.82 |

Expected final cash balance: **21,937.82 EUR**.

Expected final holdings (quantity only — no `close_price` exists to value them, D1):
IBM 10.0, MSFT 10.15, BTC 0.022 (0.03 − 0.01 + 0.002), bond "Bono Ejemplo 2027" 50.0,
Fidelity MSCI World fund 30.0. The `MIGRATION` pair (rows 8–9) must **not** appear in this
total twice or zero it out incorrectly — it must simply be excluded (§6.4).

### 10.3 Cross-file internal/external classification example

Rows 1, 7, 10, 14 in §10.2 carry `counterparty_iban = ES0000000000000000000202`. The bank
fixture's `AccountDeclaration.iban_or_account` (§7.6) is exactly
`ES0000000000000000000202`. Per §3.2 rule 1, all four rows must be classified
`INTERNAL_TRANSFER_IN`/`_OUT` and contribute **0** to `savings_flow`.

Rows 15 and 18 carry `counterparty_iban = ES0000000000000000000303`, which matches no
`AccountDeclaration` in either fixture → classified external → contribute to
`savings_flow`:

```
savings_flow (whole fixture period) = -3000.00 (row 15) + 6500.00 (row 18) = +3500.00
```

Expected test result: a Section 1 computation run against only this fixture (ignoring
D1's missing valuation) must report `savings_flow` totaling exactly `3500.00` and zero
contribution from every internal-transfer row.

## 11. Testing requirements

11.1. `test_bank_adapter_parses_fixture` — every row of §7.2 maps to the fields in §2.1;
      spot-check at least one `EXPENSE` row and the one `EXTERNAL_DEPOSIT` row.

11.2. `test_bank_adapter_amount_parsing` — the four cases listed in §7.3.

11.3. `test_bank_reconciliation_passes` — asserts §10.1 exactly, including that a
      deliberately corrupted fixture (one `Importe` changed by 0.01) makes the test raise
      with the exact row number and delta, not merely "fails."

11.4. `test_broker_adapter_migration_pair` — asserts the algorithm in §6.4 on the fixture's
      rows 8–9; asserts a synthetic unpaired `MIGRATION` row raises.

11.5. `test_broker_cash_balance` — asserts the full running balance in §10.2, row by row,
      not just the final figure (so a mismatch is localized).

11.6. `test_no_float_in_money_fields` — introspects every `LedgerEntry` produced from both
      fixtures and asserts every monetary field is `Decimal`.

11.7. `test_accounts_registry_cross_file_matching` — asserts §10.3 using both fixtures
      loaded together in one run.

11.8. `test_savings_flow_totals` — asserts the `3500.00` figure in §10.3.

11.9. `test_unknown_movement_raises` — a synthetic row with an unmapped `(category, type)`
      (broker) or an unmatched `Concepto` (bank) must raise, per §5 and §7.4.

## 12. Open questions (not resolved by this spec)

- **Q-A** (§3.3): should a named external inflow be split into "returning money" (do not
  count as savings) vs. genuine new income, and if so, how would the system tell them
  apart without the user manually tagging it?
- **Q-B** (§4): once a source requires this system to perform its own FX conversion, which
  rate source (ECB reference rate proposed) and rounding rule apply?
- **Q-C** (§7.4): how to avoid double-counting spending once a separate credit-card
  statement adapter exists alongside `LIQUIDACION DE LAS TARJETAS DE CREDITO` rows from the
  current account.
- **Q-D** (§8.2): what document would let the broker's cash balance be reconciled the same
  way the bank's is.
