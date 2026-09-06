# Specification: master ledger, adapters, reconciliation, and the Section 1 net-worth bridge

Status: normative. This document is written to be implemented by engineers/models who have
**not** seen the conversation that produced it, and to be verified against by a reviewer who
has not either.

**Every normative statement carries a rule ID (`R-n.m`).** `docs/plan/test-plan.md` maps
every rule ID to at least one test ID; a rule with no test is a defect in the test plan, and
a test asserting behaviour with no rule ID is a defect in this spec. Non-normative prose is
marked *(rationale)*.

Related documents:
- `CLAUDE.md` — project-wide quality rules (references like "CLAUDE.md rule 9" point there).
- `docs/technical-decisions.md` — narrative rationale for decisions this spec formalizes.
- `docs/plan/implementation-plan.md` — work packages and per-package verification protocol.
- `docs/plan/test-plan.md` — exhaustive test catalogue, coverage and mutation policy.

---

## 0. Scope

**R-0.1** In scope for the iteration this spec governs:
1. The `LedgerEntry` / `AccountDeclaration` data model and the `MovementType` taxonomy.
2. Two source adapters: Trade-Republic-shaped broker CSV; Spanish retail-bank XLSX.
3. Own-accounts derivation and internal/external flow classification.
4. Balance reconciliation against a source-declared balance, where one exists.
5. The Section 1 net-worth-bridge computation, to the extent computable without a price feed.
6. Chart rendering of Section 1 as HTML/SVG, and its export to PDF.
7. A CLI that runs the whole pipeline over a directory of source files.

**R-0.2** Explicitly deferred. These MUST NOT be implemented in this iteration, and the data
model MUST NOT be shaped in a way that blocks them:
- **D1** Mark-to-market valuation of open positions (no price feed exists).
- **D2** Per-asset FIFO cost basis and realized/unrealized P&L, and the `gap` cross-check
  against it.
- **D3** RSU vesting / ESPP purchase contributions (no employee-plan adapter exists).
- **D4** Incremental ingestion across repeated loads: every run treats the files present as
  the complete current truth.
- **D5** FX conversion performed by this system (both adapters consume source-converted EUR).

**R-0.3** When an implementer encounters a situation this spec does not cover, they MUST
stop and record it in `docs/plan/open-questions.md` (one entry: context, the ambiguity, the
options seen, a recommendation) rather than choose silently. Guessing is a defect even when
the guess is correct.

---

## 1. Global conventions

### 1.1 Numeric types

**R-1.1** Every monetary value is `decimal.Decimal` at every stage. `float` MUST NOT appear
in any code path that touches money, quantities, prices or FX rates — including intermediate
arithmetic, sorting keys, and serialization.

**R-1.2** Construction from source text MUST be `Decimal("<string>")`, never
`Decimal(<float>)`, and never via `float()` as an intermediate step.

**R-1.3** Quantities (shares/units) are `Decimal`, preserved at the source's precision
(observed: up to 10 decimal places), never rounded during ingestion or aggregation.

**R-1.4** FX rates are `Decimal`, preserved at the source's precision (observed: 6 dp).

**R-1.5** Rounding, when unavoidable, is `ROUND_HALF_UP` to 2 decimal places, applied
exactly once, at the point a value is rendered for display or written to a report figure —
never to a value that will be summed again afterwards. *(rationale: rounding then summing is
the classic source of cent drift that CLAUDE.md rule 9 forbids.)*

**R-1.6** Adapters MUST NOT recompute an amount from `quantity × unit_price`. The source's
own amount field is authoritative even when it differs from that product. *(rationale:
substituting our arithmetic for the document's breaks traceability against the document, and
sources legitimately embed micro-fees and their own rounding.)*

**R-1.7** A row whose `currency` is not `EUR` MUST raise `UnsupportedCurrencyError` (D5:
this system performs no conversion of its own yet). It MUST NOT be silently treated as EUR.

### 1.2 Dates and times

**R-1.8** `LedgerEntry.date` is a `datetime.date` with no time component.

**R-1.9** Where a source provides both a booking date column and an ISO timestamp, the
**date column is authoritative** and `date` MUST be parsed from it. The timestamp is stored
in `source_timestamp` for audit and same-day ordering only.
*(rationale: observed in real broker data, the two disagree — e.g. a row timestamped
`2025-07-21T03:30:05Z` carries booking date `2025-07-18` (3 days earlier), and a row
timestamped `2025-11-24T23:16:00Z` carries booking date `2025-11-25` (the next day, because
23:16 UTC is 00:16 the following day in Europe/Madrid). Deriving `date` from the UTC
timestamp would silently move transactions between months and corrupt the monthly bridge.)*

**R-1.10** No timezone conversion is ever applied to derive `date`. Timestamps are stored
as-is (UTC, as given).

**R-1.11** Spanish-format dates (`DD/MM/YYYY`) MUST be parsed with explicit day-first
semantics. A date that is ambiguous under day-first parsing is impossible in this format, but
an unparseable or out-of-range date MUST raise `ParseError`, never fall back to another
format.

### 1.3 Text normalization

**R-1.12** IBAN normalization: strip all whitespace, upper-case. Comparison of IBANs is
exact string equality after normalization.

**R-1.13** Holder-name normalization for matching: upper-case; strip leading/trailing
whitespace; collapse internal runs of whitespace to a single space; remove accents from
vowels only (`Á É Í Ó Ú Ü` → `A E I O U`); then compare as an **unordered multiset of
tokens**. `Ñ` MUST be preserved as a distinct letter and MUST NOT be folded to `N`.
*(rationale: accents on vowels vary between systems for the same person, so folding them
avoids false negatives; `Ñ` is a distinct Spanish letter, and folding it would let `PEÑA`
match `PENA` — a false positive, which under R-3.5 silently reclassifies an external flow as
internal and erases it from savings. False negatives are visible via R-3.6; false positives
are not.)*
*(rationale: real source data spells the same account holder three different ways across
institutions and even across rows of one file — surname-block first vs. given-names first,
all-caps vs. title case, and with trailing whitespace. Using the fixture's invented holder as
the illustration: `FERNANDEZ ORTIZ LUCIA`, `LUCIA FERNANDEZ ORTIZ`, `Lucia Fernandez Ortiz `
must all match each other.)*

**R-1.14** Name matching MUST NOT be fuzzy beyond R-1.13 (no edit distance, no substring
matching). *(rationale: a false positive silently reclassifies an external flow as internal
and erases it from savings; a false negative is visible as a warning under R-3.6.)*

### 1.4 File reading

**R-1.15** CSV files are read as UTF-8; a leading BOM (`﻿`) MUST be stripped if
present. If UTF-8 decoding fails, the reader MUST retry as `cp1252` and emit a warning
naming the file and the fallback used. If both fail, raise `ParseError`.

**R-1.16** CSV parsing MUST follow RFC 4180 quoting (fields may contain commas, escaped
double quotes, and newlines inside quotes). Hand-rolled `split(",")` is forbidden.

**R-1.17** Both `\n` and `\r\n` line endings MUST parse identically. Trailing blank lines
MUST be ignored, not parsed as empty rows.

**R-1.18** An empty file, or a file containing only a header row, MUST produce zero entries
and zero account declarations, plus a warning — not an exception. *(rationale: a legitimately
empty period is not an error; a silent empty result is.)*

**R-1.19** `source_row` is the 1-indexed physical row number within the file, counting the
header row as row 1, so a human can open the file and jump to it (CLAUDE.md rule 10).

### 1.5 Determinism

**R-1.20** The pipeline MUST be deterministic: given the same input files, two runs produce
byte-identical outputs, including ordering of entries, warnings, and any serialized report
data. Iteration over sets/dicts MUST NOT leak into output ordering.

**R-1.21** `entry_id` MUST be a deterministic function of the source: the source
`transaction_id` when present, else a stable hash of
`(institution, account, source_file, source_row)`. It MUST NOT incorporate wall-clock time,
randomness, or process state.

**R-1.22** Entries MUST be sorted by `(date, source_file, source_row)` before any
running-balance or period aggregation. *(rationale: same-day rows must have a total order
that comes from the document, not from parse order or hash order.)*

### 1.6 Error taxonomy

**R-1.23** All failures raise a subclass of `FinaError`, never a bare `Exception`, and each
carries structured fields (not just a message string):

| Exception | Raised when | Required fields |
|---|---|---|
| `ParseError` | a field cannot be parsed as its declared type | `source_file`, `source_row`, `column`, `raw_value`, `expected` |
| `UnknownMovementError` | a `(category, type)` pair or a `Concepto` string matches no rule | `source_file`, `source_row`, `observed` |
| `UnsupportedCurrencyError` | R-1.7 | `source_file`, `source_row`, `currency` |
| `MigrationPairError` | R-6.10..R-6.13 violated | `source_file`, `date`, `asset`, `rows` |
| `ReconciliationError` | R-8.2 violated | `source_file`, `source_row`, `expected`, `declared`, `delta` |
| `AccountConflictError` | R-3.3 violated | `iban`, `holder_names`, `files` |
| `DuplicateSourceError` | R-2.14 / R-2.15 violated | `files` or `transaction_id`, `source_rows` |
| `ValidationError` | a sign/consistency invariant in §2.5 fails | `source_file`, `source_row`, `invariant` |

**R-1.24** No failure path may be silent. Anything that is not fatal MUST appear in
`AdapterResult.warnings` (adapters) or `PipelineResult.warnings` (pipeline) and MUST be
surfaced by the CLI before any report figure is printed (CLAUDE.md rule 15).

---

## 2. Data model

### 2.1 `LedgerEntry`

**R-2.1** `LedgerEntry` is immutable (frozen dataclass) with exactly these fields:

| Field | Type | Nullable | Description |
|---|---|---|---|
| `entry_id` | `str` | no | Per R-1.21. |
| `date` | `date` | no | Per R-1.8/R-1.9. |
| `value_date` | `date` \| `None` | yes | Economic value date where the source distinguishes it (bank only). |
| `source_timestamp` | `datetime` \| `None` | yes | Per R-1.9/R-1.10. |
| `institution` | `str` | no | `"trade_republic"` \| `"bank_es"`. |
| `account` | `str` | no | `"cash"` \| `"positions"` (broker), `"current_account"` (bank). |
| `movement_type` | `MovementType` | no | §2.2. |
| `asset` | `str` \| `None` | yes | ISIN or ticker. `None` for pure cash movements. |
| `asset_class` | `str` \| `None` | yes | `STOCK`/`CRYPTO`/`BOND`/`MUTUAL_FUND`/`None`. |
| `quantity` | `Decimal` \| `None` | yes | Signed: `+` increases holdings. |
| `unit_price` | `Decimal` \| `None` | yes | In trade currency. |
| `currency` | `str` | no | Per R-1.7, always `EUR` in this iteration. |
| `amount_eur` | `Decimal` | no | Source amount verbatim (R-1.6). Signed: `+` = cash in. |
| `fee_eur` | `Decimal` \| `None` | yes | Source fee verbatim. |
| `tax_eur` | `Decimal` \| `None` | yes | Source tax verbatim. Informational only (R-2.7). |
| `original_amount` | `Decimal` \| `None` | yes | Pre-conversion amount. |
| `original_currency` | `str` \| `None` | yes | ISO code of `original_amount`. |
| `fx_rate` | `Decimal` \| `None` | yes | Rate the source used. Audit only (R-1.6). |
| `cash_effect_eur` | `Decimal` | no | Computed per R-2.6. |
| `declared_balance` | `Decimal` \| `None` | yes | Balance the source claims after this row. |
| `counterparty_name` | `str` \| `None` | yes | Verbatim (normalization happens at match time). |
| `counterparty_iban` | `str` \| `None` | yes | Normalized per R-1.12. |
| `is_external_flow` | `bool` \| `None` | yes | Per §3. `None` where the concept does not apply. |
| `status` | `"actual"` \| `"estimated"` | no | CLAUDE.md rule 11. Both adapters emit only `"actual"`. |
| `source_file` | `str` | no | File name. |
| `source_row` | `int` | no | Per R-1.19. |
| `raw` | `Mapping[str, str]` | no | The original row verbatim, before parsing. |

**R-2.2** `raw` MUST be retained for every entry, even when every field has been parsed into
a typed attribute. *(rationale: it is the only thing that lets a reviewer prove the parse was
faithful without re-opening the source file.)*

### 2.2 `MovementType`

**R-2.3** The enum has exactly these members, with these semantics:

| Value | Cash-affecting | Quantity-affecting | Counts toward `savings_flow` | `is_external_flow` applies |
|---|---|---|---|---|
| `EXTERNAL_DEPOSIT` | yes | no | yes | yes |
| `EXTERNAL_WITHDRAWAL` | yes | no | yes | yes |
| `INTERNAL_TRANSFER_IN` | yes | no | **no** | yes (`False`) |
| `INTERNAL_TRANSFER_OUT` | yes | no | **no** | yes (`False`) |
| `BUY` | yes | yes (+) | no | no (`None`) |
| `SELL` | yes | yes (−) | no | no (`None`) |
| `DIVIDEND` | yes | no | no | no (`None`) |
| `INTEREST` | yes | no | no | no (`None`) |
| `EXPENSE` | yes | no | yes | yes (`True`) |
| `PAYROLL_INCOME` | yes | no | yes | yes (`True`) |
| `RSU_VESTING` | n/a (D3) | yes (+) | yes (D3) | no (`None`) |
| `ESPP_PURCHASE` | n/a (D3) | yes (+) | yes (D3) | no (`None`) |
| `TECHNICAL_ADJUSTMENT` | no (forced 0) | **no — skipped entirely** | no | no (`None`) |

**R-2.4** `RSU_VESTING` and `ESPP_PURCHASE` MUST exist in the enum but MUST NOT be produced
by any adapter in this iteration (D3). Any code path that would produce them raises
`NotImplementedError` with a message naming D3.

**R-2.5** `TECHNICAL_ADJUSTMENT` rows MUST be excluded from holdings computation entirely
(not merely netted against their pair). *(rationale: a future FIFO engine must never open or
close a lot because of them; netting to zero would still perturb per-lot bookkeeping.)*

### 2.3 Cash effect

**R-2.6**
```
cash_effect_eur(e) =
    Decimal("0")                              if e.movement_type is TECHNICAL_ADJUSTMENT
    e.amount_eur + (e.fee_eur or Decimal(0))  if e.movement_type in {BUY, SELL}
    e.amount_eur                              otherwise
```

*(rationale, verified against real source data: a broker BUY split across a whole-share row
(`amount=-3963.60`, `fee=-1.00`) and a fractional-remainder row (`amount=-36.40`, no fee)
sums to `-4001.00` for 12.110202 shares at 330.30 ≈ 4000.99 — so the fee is an additional
outflow charged once per logical trade, on the whole-share leg.)*

**R-2.7** `tax_eur` MUST NOT enter `cash_effect_eur`.
*(rationale, verified: a dividend row has `original_amount=24.44 USD`, `fx_rate=0.876424`,
`amount_eur=21.42`, `tax_eur=-3.22`, and `24.44 × 0.876424 = 21.42` exactly — `amount_eur`
is already net of withholding; subtracting `tax_eur` again would double-count it. It is
retained solely as input to the future foreign-tax-credit computation.)*

### 2.4 `AccountDeclaration`

**R-2.8** Fields: `institution`, `iban_or_account` (`str | None`, normalized per R-1.12),
`holder_name` (`str`, verbatim), `declared_in_file` (`str`), `as_of_date` (`date`).

**R-2.9** `iban_or_account` MAY be `None` when the source states no IBAN for the account it
describes (see R-6.16).

### 2.5 Validation invariants

Each of these MUST be checked at adapter output and raise `ValidationError` on breach:

**R-2.10** `movement_type is BUY` ⇒ `amount_eur < 0` and `quantity > 0`.
**R-2.11** `movement_type is SELL` ⇒ `amount_eur > 0` and `quantity < 0`.
**R-2.12** `movement_type in {DIVIDEND, INTEREST}` ⇒ `amount_eur >= 0` and `quantity is None`.
**R-2.13** `fee_eur`, when present, `<= 0`.
**R-2.14** Within one file, `transaction_id` values, where present, MUST be unique; a repeat
raises `DuplicateSourceError`.
**R-2.15** Within one pipeline run, no two input files may have identical content (SHA-256
of bytes); a repeat raises `DuplicateSourceError`. *(rationale: the same export supplied
twice under two names would double every figure, and under D4 there is no dedup layer to
catch it.)*
**R-2.16** `amount_eur == 0` is permitted (some corrections are booked at zero) but MUST
emit a warning naming the row.
**R-2.17** `movement_type is TECHNICAL_ADJUSTMENT` ⇒ `cash_effect_eur == 0`.

---

## 3. Own accounts and internal/external classification

**R-3.1** `owned_accounts` for a run is the union of every `AccountDeclaration` produced by
every adapter over every file in that run. There is no hand-maintained registry
(`docs/technical-decisions.md` §4).

**R-3.2** Classification runs as a **second pass**, after all files are parsed, because a
file's rows may only be classifiable against a declaration found in a different file.

**R-3.3** If two declarations share a normalized `iban_or_account` but differ in normalized
`holder_name` (R-1.13), raise `AccountConflictError`.

**R-3.4** For every entry whose adapter-assigned `movement_type` is transfer-shaped
(`EXTERNAL_DEPOSIT` / `EXTERNAL_WITHDRAWAL` — the adapters' pre-classification default),
apply in order:
1. If `counterparty_iban` is non-empty and equals (R-1.12) the `iban_or_account` of any
   owned account → **internal**.
2. Else if `counterparty_iban` is empty/absent and `counterparty_name` matches (R-1.13) the
   `holder_name` of any owned account → **internal**.
3. Else → **external**.

**R-3.5** On internal, rewrite `movement_type` to `INTERNAL_TRANSFER_IN` when
`cash_effect_eur > 0`, `INTERNAL_TRANSFER_OUT` when `< 0`, and set `is_external_flow=False`.
On external, keep `EXTERNAL_DEPOSIT`/`EXTERNAL_WITHDRAWAL` and set `is_external_flow=True`.
An internal transfer with `cash_effect_eur == 0` raises `ValidationError`.

**R-3.6 (stability guard)** For every row classified **external** by rule 3 whose
`counterparty_name` matches an owned `holder_name` under R-1.13 *(i.e. right person, IBAN
not recognized)*, the run MUST emit a warning naming the row, the counterparty, and the
likely cause ("a statement for this account may not have been supplied").
*(rationale: classification depends on which files are present in the run. If the user has
not yet supplied a statement for one of their own accounts, transfers to it look external
today and become internal once that statement arrives — silently changing published savings
figures, which CLAUDE.md rule 12 forbids. This warning is how that risk becomes visible
instead of retroactive.)*

**R-3.7** The pipeline output MUST record the exact `owned_accounts` set used for a run
(institution, IBAN, holder, source file), so a later change in classification is explainable
by a change in inputs rather than appearing as an unexplained restatement.

---

## 4. Currency conversion

**R-4.1** No adapter in this iteration performs FX conversion (D5); each consumes the
source's own EUR amount.

**R-4.2** When a future source requires conversion, this spec MUST be extended before that
adapter is written, fixing: rate source, rate date, and rounding. The current proposal —
**not a decision** — is ECB daily reference rate, rate dated on `LedgerEntry.date`, rounded
per R-1.5. Recorded as open question Q-B.

---

## 5. Adapter contract

**R-5.1** Signature:
```
Adapter.parse(file_path: Path) -> AdapterResult
AdapterResult = (entries: list[LedgerEntry], accounts: list[AccountDeclaration], warnings: list[Warning])
```

**R-5.2** An adapter MUST NOT perform cross-file classification (that is §3, second pass); it
assigns the pre-classification default only.

**R-5.3** An adapter MUST raise rather than guess when a row matches no mapping rule
(`UnknownMovementError`). Defaulting an unrecognized row to any movement type is forbidden.

**R-5.4** An adapter MUST be a pure function of the file bytes: no network, no clock, no
environment lookups. *(rationale: required by R-1.20 determinism and by testability.)*

---

## 6. Adapter: Trade Republic broker CSV

### 6.1 Source columns

**R-6.1** The header MUST contain exactly these 23 columns, in any order; a missing column
raises `ParseError`, an unrecognized extra column emits a warning and is preserved in `raw`:

`datetime, date, account_type, category, type, asset_class, name, symbol, shares, price,
amount, fee, tax, currency, original_amount, original_currency, fx_rate, description,
transaction_id, counterparty_name, counterparty_iban, payment_reference, mcc_code`

**R-6.2** Field mapping: `date`→`date` (R-1.9), `datetime`→`source_timestamp`,
`asset_class`→`asset_class`, `symbol`→`asset`, `shares`→`quantity`, `price`→`unit_price`,
`amount`→`amount_eur`, `fee`→`fee_eur`, `tax`→`tax_eur`, `currency`→`currency`,
`original_amount`→`original_amount`, `original_currency`→`original_currency`,
`fx_rate`→`fx_rate`, `transaction_id`→ seeds `entry_id`,
`counterparty_name`→`counterparty_name`, `counterparty_iban`→`counterparty_iban`.
`account_type`, `name`, `description`, `payment_reference`, `mcc_code` are `raw`-only.

**R-6.2a** `shares` maps to `quantity` **only** when `category` is `TRADING` or `DELIVERY`.
For `CASH`-category rows it MUST NOT populate `quantity`; it is retained in `raw` and, where
useful, in the informational field `position_at_record_date`.
*(rationale: real `DIVIDEND` rows carry `shares` equal to the holding that generated the
dividend — e.g. `shares=10.0` on an IBM dividend. Mapping that into `quantity` would add 10
phantom IBM shares to holdings on the dividend date, silently inflating net worth and, once
D2 lands, corrupting every FIFO lot after it. This is the single most dangerous field
mapping in this adapter.)*

**R-6.3** Empty string in a numeric column means "absent" (`None`), not zero.

**R-6.4** `account` is `"positions"` when `category` is `TRADING` or `DELIVERY`, else
`"cash"`.

### 6.2 Movement mapping

**R-6.5** `(category, type)` → `movement_type`:

| `category` | `type` | `movement_type` |
|---|---|---|
| `CASH` | `CUSTOMER_INBOUND` | `EXTERNAL_DEPOSIT` (pre-classification) |
| `CASH` | `TRANSFER_INBOUND` | `EXTERNAL_DEPOSIT` (pre-classification) |
| `CASH` | `TRANSFER_INSTANT_INBOUND` | `EXTERNAL_DEPOSIT` (pre-classification) |
| `CASH` | `TRANSFER_OUTBOUND` | `EXTERNAL_WITHDRAWAL` (pre-classification) |
| `CASH` | `TRANSFER_INSTANT_OUTBOUND` | `EXTERNAL_WITHDRAWAL` (pre-classification) |
| `CASH` | `INTEREST_PAYMENT` | `INTEREST` |
| `CASH` | `DIVIDEND` | `DIVIDEND` |
| `TRADING` | `BUY` | `BUY` |
| `TRADING` | `SELL` | `SELL` |
| `DELIVERY` | `MIGRATION` | `TECHNICAL_ADJUSTMENT` |

**R-6.6** Any other pair raises `UnknownMovementError`.

**R-6.7** `CUSTOMER_INBOUND` MUST NOT be assumed external despite its name; §3 decides.
*(rationale: in real data these are usually the user funding the broker from their own bank,
i.e. internal — counting them as savings would double-count money already counted as saved
when it was earned into the bank account, breaching CLAUDE.md rule 13.)*

**R-6.8** A single logical trade may be split across two rows (whole-share leg carrying the
fee, fractional leg without). Both rows are ingested as separate entries; no merging.
*(rationale: each leg is a genuine lot line for a future FIFO engine, and merging would
destroy the row-to-source correspondence required by CLAUDE.md rule 10.)*

**R-6.9** `unit_price` for crypto rows is per whole unit (observed: BTC at ~44 500 EUR with
quantities like `0.0300000000`); no scaling is applied.

### 6.3 `MIGRATION` pairing

**R-6.10** Group rows with `category == DELIVERY and type == MIGRATION` by `(date, asset)`.
**R-6.11** Each group MUST contain exactly 2 rows, else `MigrationPairError`.
**R-6.12** The two quantities MUST be exact additive inverses, and the two `unit_price`
values MUST be equal, else `MigrationPairError`.
**R-6.13** Both rows MUST have absent-or-zero `amount`, else `MigrationPairError`.
**R-6.14** Both rows are emitted as entries tagged `TECHNICAL_ADJUSTMENT` (kept for
traceability: entry count equals source row count) and excluded from holdings per R-2.5.
*(rationale and real-world cause — Trade Republic's German→Spanish IBAN/branch migration of
June 2025 — in `docs/technical-decisions.md` §1.)*

**R-6.15** A `DELIVERY` row with a `type` other than `MIGRATION` raises
`UnknownMovementError` (do not generalize the pairing rule to unseen delivery types).

### 6.4 Account declaration

**R-6.16** The broker export states no IBAN for the broker account itself. Emit:
`AccountDeclaration(institution="trade_republic", iban_or_account=None,
holder_name=<the `name` field of the first row where `type == CUSTOMER_INBOUND` and
`name == counterparty_name`>, declared_in_file=<file>, as_of_date=max(date over all rows))`.

**R-6.17** If no such row exists, emit no declaration and a warning. *(rationale: without it,
broker-side transfers can only be classified by IBAN against other files' declarations; this
must be visible, not silent.)*

---

## 7. Adapter: Spanish retail-bank XLSX

### 7.1 Header block

**R-7.1** Read the first worksheet only; additional sheets emit a warning and are ignored.

**R-7.2** The header block is located by label, not by fixed coordinates: find the cell whose
normalized text is `CUENTA`; the account IBAN is the cell to its right or below-right per the
observed layout, and likewise `TITULAR` → holder, `SALDO` → current balance, `FECHA` →
export timestamp. If any of the four labels is absent, raise `ParseError` naming which.
*(rationale: exports vary in how many blank/title rows precede the block; fixed row indices
break silently on the next export.)*

**R-7.3** The current-balance header value may carry a currency suffix
(`"1.126,84€ EUR"`); the parser MUST strip both the `€` symbol and any trailing ISO code
before parsing (R-7.6).

**R-7.4** The movements table is located by finding the row whose cells equal, in order,
`Fecha operación, Fecha valor, Concepto, Importe, Saldo, Divisa` (normalized: trimmed,
case-insensitive, accent-insensitive). Absent ⇒ `ParseError`.

**R-7.5** Parsing stops at the first fully-blank row after the table header; any content
after that (footers, disclaimers) is ignored with a warning. Blank rows *inside* the table
(a single blank row followed by more data rows) MUST also stop parsing and warn, rather than
being skipped. *(rationale: a mid-table blank row means the file's shape is not what this
adapter believes; continuing would risk misaligning columns.)*

### 7.2 Cell value handling

**R-7.6** Spanish amount strings have the form `[-]D{1,3}(.DDD)*,DD€`. Parse by: strip
whitespace and `€` and any trailing ISO currency code; remove `.` (thousands separator);
replace `,` with `.`; `Decimal(...)`. A string that does not match the expected shape raises
`ParseError` — no best-effort salvage.

**R-7.7** Amount and balance cells MAY arrive from the workbook as numeric cells rather than
strings (depends on the export). When a cell is numeric, it MUST be converted via
`Decimal(str(cell_value))` and the run MUST emit a warning naming the file, since a numeric
cell has already passed through a binary float in the workbook and its exactness cannot be
proven from the document. `float` arithmetic on the value is still forbidden (R-1.1).
*(rationale: this is the one place where the source itself can cost us exactness; it must be
declared, not hidden — CLAUDE.md rule 11.)*

**R-7.8** Date cells MAY arrive as `datetime`/`date` objects instead of `DD/MM/YYYY`
strings; both MUST be handled, producing identical results.

**R-7.9** `Divisa` other than `EUR` raises `UnsupportedCurrencyError` (R-1.7).

### 7.3 Concept categorization

**R-7.10** Rules are ordered, first match wins, applied to the trimmed `Concepto` with
case-insensitive, accent-insensitive matching:

| # | Pattern (anchored at start unless noted) | `movement_type` |
|---|---|---|
| 1 | `PAGO MOVIL EN ` | `EXPENSE` |
| 2 | `TRANSACCION CONTACTLESS EN ` | `EXPENSE` |
| 3 | `COMPRA ` … containing `TARJETA` | `EXPENSE` |
| 4 | `RECIBO ` | `EXPENSE` |
| 5 | `LIQUIDACION PERIODICA PRESTAMO` | `EXPENSE` (see `technical-decisions.md` §2) |
| 6 | `LIQUIDACION DE LAS TARJETAS DE CREDITO` | `EXPENSE` (see R-7.12) |
| 7 | `TRANSFERENCIA DE (?P<name>.+?)(,\s*CONCEPTO\b.*)?$` | `EXTERNAL_DEPOSIT` (pre-classification); `name` → `counterparty_name` |
| 8 | `TRANSFERENCIA A (?P<name>.+?)(,\s*CONCEPTO\b.*)?$` | `EXTERNAL_WITHDRAWAL` (pre-classification); `name` → `counterparty_name` |
| 9 | `NOMINA` or `ABONO NOMINA` | `PAYROLL_INCOME` |

**R-7.11** No match ⇒ `UnknownMovementError`, regardless of amount sign. A catch-all default
is forbidden. *(rationale: an unrecognized concept is exactly the case CLAUDE.md rule 15
requires to surface; silently defaulting it to `EXPENSE` would quietly distort the savings
rate.)*

**R-7.12 (known risk, not resolved)** Rule 6 charges the credit-card settlement as an
expense. If a card-statement adapter is ever added, the individual card purchases would
also be ingested and the same spending would be counted twice (CLAUDE.md rule 13). No
card adapter exists in this iteration; this rule MUST be revisited before one is written.
Recorded as open question Q-C.

**R-7.13** The bank export provides no counterparty IBAN; transfer rows carry only a name,
so their classification relies on R-3.4 rule 2.

### 7.4 Dates and declaration

**R-7.14** `date = Fecha operación`; `value_date = Fecha valor`.
*(rationale in `docs/technical-decisions.md` §3: the `Saldo` column is accumulated in
`Fecha operación` order, so reconciliation only closes if the ledger uses the same ordering.)*

**R-7.15** Rows appear newest-first in the source; the adapter MUST NOT assume order and
MUST sort per R-1.22 before reconciliation.

**R-7.16** Emit `AccountDeclaration(institution="bank_es", iban_or_account=<header IBAN,
normalized>, holder_name=<header holder>, declared_in_file=<file>, as_of_date=<header export
date>)`.

---

## 8. Reconciliation

**R-8.1** Reconciliation runs per `(institution, account)` over entries sorted per R-1.22.

**R-8.2** For each consecutive pair of entries that both carry a `declared_balance`:
```
expected = previous.declared_balance + current.cash_effect_eur
if expected != current.declared_balance: raise ReconciliationError(...)
```
Tolerance is exactly zero (CLAUDE.md rule 9). No epsilon, no rounding before comparison.

**R-8.3** The first entry carrying a `declared_balance` establishes the baseline and is not
itself checked. The account's balance before that row is neither claimed nor verified by that
file.

**R-8.4** Entries with `declared_balance is None` (i.e. the whole broker file) are skipped by
the reconciliation check; the run MUST emit exactly one warning per such
`(institution, account)` stating that its computed balance is **unverified against any
source-declared balance** (CLAUDE.md rule 11).
*(rationale: the broker export format carries no running-balance column at all. The honest
position is "computed, unverified", not "reconciled". Resolving it requires a periodic
statement the user does not currently supply — open question Q-D.)*

**R-8.5** After reconciliation, the final `declared_balance` of a bank account MUST equal the
balance stated in that file's header block (R-7.2); a mismatch raises `ReconciliationError`
with both figures. *(rationale: an independent second check of the same file against
itself — cheap, and catches a truncated export.)*

---

## 9. Section 1: the net-worth bridge

Let `M` be the set of all entries in a run.

**R-9.1** `cash_balance(institution, account, t) = Σ { e.cash_effect_eur : e ∈ M,
e.institution/account match, e.date ≤ t }`.

**R-9.2** `quantity_held(institution, account, asset, t) = Σ { e.quantity : matching, e.date
≤ t, e.movement_type is not TECHNICAL_ADJUSTMENT, e.quantity is not None }`.

**R-9.3** `real_net_worth(t) = Σ cash_balance(·, t) + Σ quantity_held(·, t) ×
close_price(asset, t)`. The second term is **D1**: no price feed exists.

**R-9.4** Until D1 lands, the pipeline MUST emit `real_net_worth` with an explicit
`completeness` flag of `"cash_only"` and MUST NOT present it as total net worth anywhere —
no chart, table or CLI line may display a cash-only figure without that qualifier
(CLAUDE.md rule 11).

**R-9.5** `savings_flow(month)` = `Σ contribution(e)` over entries dated within that
calendar month, where:
```
contribution(e) =
    e.cash_effect_eur   if e.movement_type in {EXTERNAL_DEPOSIT, EXTERNAL_WITHDRAWAL,
                                                EXPENSE, PAYROLL_INCOME}
                           and e.is_external_flow is True
    <D3>                if e.movement_type in {RSU_VESTING, ESPP_PURCHASE}
    Decimal("0")        otherwise
```

**R-9.6** Months are calendar months in the account's own booking dates (no timezone
conversion — R-1.10). A month with no entries MUST still appear in the output series with
`savings_flow = 0` and balances carried forward. *(rationale: a hole in the series would
break both the chart's x-axis and the recursion in R-9.8.)*

**R-9.7** `t0` = the calendar month of the earliest entry in `M`. `savings_only(t0) =
real_net_worth(t0)`.

**R-9.8** `savings_only(t) = savings_only(t−1) + savings_flow(t)` for `t > t0`.

**R-9.9** `gap(t) = real_net_worth(t) − savings_only(t)`.

**R-9.10** The final period may be partial (data ends mid-month). It MUST be emitted with an
explicit `as_of` date equal to the latest entry date, and labelled as partial in the output
data; it MUST NOT be silently presented as a full month.

**R-9.11** All Section 1 outputs are `Decimal`; rounding for display happens only per R-1.5
at render time.

**R-9.12 (D2)** The cross-check against the per-asset view, to be implemented when the FIFO
engine exists:
```
gap(t) ?= Σ realized_pl + Σ unrealized_pl(t) + Σ (DIVIDEND, INTEREST cash) − Σ fees
```
asserted to exact equality. Not implemented in this iteration.

---

## 10. Chart and report rendering

**R-10.1** The Section 1 chart is rendered as HTML/SVG and exported to PDF via a headless
browser (decision 5.5, option B).

**R-10.2** The chart's visual contract, validated against the approved mock:
two series (`real net worth` solid, `savings only` dashed and muted); the band between them
filled and coloured by the sign of `gap`, with the colour boundary placed at the exact
linear zero-crossing between adjacent points; direct value labels at the final point; a
month tooltip that opens **only on click**, is dismissible via its own close control or a
click anywhere outside it, and is translucent enough to read the chart behind it; no legend
entries for the band colours; no explanatory footer.

**R-10.3** Both light and dark themes MUST be defined via tokens; no colour may be defined
only inside a media query or theme block.

**R-10.4** The renderer MUST accept the Section 1 series as data and MUST NOT recompute any
financial figure itself. *(rationale: two implementations of one formula is how two answers
to one question appear.)*

**R-10.5** Any figure displayed as a total while D1 is outstanding MUST carry the
`cash_only` qualifier from R-9.4.

---

## 11. CLI and pipeline

**R-11.1** One command runs: discover files → parse (per adapter) → validate → classify
(§3) → reconcile (§8) → compute Section 1 (§9) → render (§10).

**R-11.2** Adapter selection is by file shape (header signature), not by file name or
extension alone; an unrecognized shape raises with the list of adapters tried.

**R-11.3** All warnings are printed **before** any figure (CLAUDE.md rule 15).

**R-11.4** A `ReconciliationError` or any `ValidationError` aborts the run with a non-zero
exit code; no partial report is written. *(rationale: a report that exists but is wrong is
worse than no report.)*

**R-11.5** The run writes a machine-readable run manifest: input files with their SHA-256,
the `owned_accounts` set (R-3.7), every warning, the tool version, and the resulting Section
1 series. Two runs over identical inputs MUST produce identical manifests (R-1.20).

---

## 12. Worked oracle against the fixtures

**R-12.1** The implementation MUST reproduce these figures exactly. They were computed by
hand from the fixture files, independently of any implementation. If a fixture changes, this
section MUST be recomputed in the same commit.

*(note: the two fixtures use non-overlapping date ranges — broker 2023, bank 2027 — because
they are independent format samples for two adapters, not one coherent history. The
cross-file IBAN match in §12.3 is an identity match, independent of dates.)*

### 12.1 `tests/fixtures/banco_ejemplo.xlsx`

| Fecha operación | Importe | Saldo declared |
|---|---|---|
| 01/03/2027 | +6.500,00 | 7.500,00 |
| 02/03/2027 | −120,50 | 7.379,50 |
| 03/03/2027 | −430,00 | 6.949,50 |
| 04/03/2027 | −650,25 | 6.299,25 |
| 05/03/2027 | −38,90 | 6.260,35 |
| 07/03/2027 | −64,20 | 6.196,15 |
| 07/03/2027 | −12,40 | 6.183,75 |

Expected: reconciliation passes with zero discrepancy on every row (R-8.2); the last
declared balance `6183.75` equals the header balance (R-8.5); the two rows dated 07/03 are
ordered by `source_row` (R-1.22) — reversing them breaks reconciliation, which is itself a
required test.

Expected `savings_flow` for 2027-03: `+6500.00 − 120.50 − 430.00 − 650.25 − 38.90 − 64.20 −
12.40 = +5183.75` (every row is `EXPENSE` or an external `EXTERNAL_DEPOSIT`; `MORENO SANZ
DAVID` matches no owned account).

### 12.2 `tests/fixtures/broker_ejemplo.csv`

| # | Movement | `cash_effect_eur` | Running cash |
|---|---|---|---|
| 1 | `CUSTOMER_INBOUND` +8000.00 | +8000.00 | 8000.00 |
| 2 | `BUY` IBM 10 @ 200.00, fee −1.00 | −2001.00 | 5999.00 |
| 3 | `INTEREST_PAYMENT` +37.10 | +37.10 | 6036.10 |
| 4 | `BUY` MSFT 10 @ 310.50, fee −1.00 | −3106.00 | 2930.10 |
| 5 | `BUY` MSFT 0.15 @ 310.50, no fee | −46.58 | 2883.52 |
| 6 | `DIVIDEND` IBM (17.50 USD @ 0.874286) | +15.30 | 2898.82 |
| 7 | `CUSTOMER_INBOUND` +12000.00 | +12000.00 | 14898.82 |
| 8–9 | `MIGRATION` pair (IBM) | 0.00 | 14898.82 |
| 10 | `TRANSFER_INBOUND` +5000.00 | +5000.00 | 19898.82 |
| 11 | `BUY` BTC 0.03 @ 44500.00, fee −1.00 | −1336.00 | 18562.82 |
| 12 | `SELL` BTC 0.01 @ 45000.00, fee −1.00 | +449.00 | 19011.82 |
| 13 | `BUY` BTC 0.002 @ 44000.00, no fee | −88.00 | 18923.82 |
| 14 | `TRANSFER_INSTANT_OUTBOUND` −80.00 | −80.00 | 18843.82 |
| 15 | `TRANSFER_OUTBOUND` −3000.00 | −3000.00 | 15843.82 |
| 16 | `BUY` bond 50 @ 1.00, fee −1.00 | −51.00 | 15792.82 |
| 17 | `BUY` fund 30 @ 11.80, fee −1.00 | −355.00 | 15437.82 |
| 18 | `TRANSFER_INSTANT_INBOUND` +6500.00 | +6500.00 | 21937.82 |

Expected final cash: **21 937.82 EUR**. Expected holdings: IBM `10.0`, MSFT `10.15`, BTC
`0.022`, `FR0000000001` `50.0`, `IE00BYX5NX33` `30.0`. Expected entry count: 18 (both
MIGRATION rows present, R-6.14). Expected: exactly one R-8.4 warning for
`(trade_republic, cash)`.

### 12.3 Cross-file classification

Rows 1, 7, 10, 14 carry `counterparty_iban = ES0000000000000000000202`, which equals the
bank fixture's declared IBAN ⇒ internal (R-3.4 rule 1) ⇒ contribute 0 to `savings_flow`.
Rows 15 and 18 carry `ES0000000000000000000303`, matching no owned account ⇒ external.

Expected broker-side `savings_flow` over the whole fixture: `−3000.00 + 6500.00 =
+3500.00`.

---

## 13. Open questions

- **Q-A** (R-3.4): distinguishing "money of mine coming back" from genuinely new external
  income, without manual tagging.
- **Q-B** (R-4.2): FX rate source, rate date and rounding, before any converting adapter.
- **Q-C** (R-7.12): double-count risk between card-settlement rows and a future card adapter.
- **Q-D** (R-8.4): which document would let the broker cash balance be reconciled.
- **Q-E** (R-9.4): how the report should present a cash-only net worth in the two-page
  report without misleading the reader, until D1 lands.
