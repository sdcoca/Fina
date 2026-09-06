# Test plan

Companion to `docs/spec/section1-ingestion-spec.md` (rule IDs `R-n.m`) and
`docs/plan/implementation-plan.md` (work packages `WP-n`).

Every test below has an ID (`T-xxx`) and names the rules it covers. §9 is the traceability
matrix: **every rule ID in the spec must appear there against at least one test.** A rule
with no test, or a test asserting behaviour no rule mandates, is a defect to fix before the
work package is considered done.

---

## 1. Quality gates (non-negotiable)

**G-1 Coverage.** `pytest --cov=src/fina --cov-branch --cov-fail-under=100`. Line *and*
branch coverage must be 100% for everything under `src/fina/`.

**G-2 Coverage exemptions.** `# pragma: no cover` is permitted **only** on:
`if TYPE_CHECKING:` blocks, and the D3 stubs that raise `NotImplementedError` (R-2.4).
Any other pragma is a defect. A test (`T-901`) greps the source tree and fails on any
pragma outside that allowlist.

**G-3 Mutation testing.** `mutmut run` over `src/fina/`. Required kill rates:
`money.py`, `models.py`, `reconciliation.py`, `section1.py` ≥ **95 %**;
`adapters/*.py`, `classification.py`, `pipeline.py` ≥ **90 %**.
Every surviving mutant must be listed in `docs/plan/surviving-mutants.md` with a one-line
justification (equivalent mutant, or a test gap accepted by the reviewer). An unlisted
survivor is a defect.
*(rationale: 100 % coverage proves lines ran, not that a wrong answer would be caught.
Mutation score is the only cheap evidence the assertions actually bite — and for money code
that is the whole point.)*

**G-4 Types.** `mypy --strict src/fina` with zero errors. No `Any` in a public signature,
no `# type: ignore` without a trailing comment naming the reason.

**G-5 Lint/format.** `ruff check src tests` and `ruff format --check src tests`, zero
findings.

**G-6 No floats in money paths.** `T-902` walks the AST of every module under `src/fina/`
and fails if it finds: a `float` annotation, a call to `float(`, a float literal, or
`Decimal(` applied to a non-string non-int expression. Covers R-1.1/R-1.2.

**G-7 Determinism.** `T-903` runs the full pipeline twice over the fixtures in separate
temp dirs and asserts byte-identical manifests. Covers R-1.20.

**G-8 No test weakening.** Tests may not be deleted, skipped (`@pytest.mark.skip`),
`xfail`ed, or have their oracle numbers edited to make code pass. If a fixture legitimately
changes, spec §12 and the affected tests change **in the same commit**, and the diff must
show the recomputation. `T-904` fails if any skip/xfail marker exists in the suite.

---

## 2. Test data policy

**TD-1** No file in the repository — fixture, document, code, comment or commit message —
contains real personal data: names, IBANs, amounts, concepts and identifiers are all invented
(CLAUDE.md rule 18). This applies to prose in specs and plans exactly as strictly as to
fixtures; a real name quoted as an "illustrative example" in a design document is the same
leak as one in a data file, and has already happened once in this project's history.

**TD-1a** The guard for TD-1 (`T-905`) MUST NOT work by listing the forbidden real values in
the repository — that would itself be the leak. It works structurally instead:
1. Any string matching an IBAN shape (`[A-Z]{2}\d{2}[A-Z0-9]{10,30}`, ignoring spaces) that
   is not in the fixtures' declared allowlist of invented IBANs fails the test.
2. Any capitalized multi-token personal-name-shaped string in a fixture that is not in the
   fixtures' declared allowlist of invented holder/counterparty names fails the test.
3. Optionally, when the environment variable `FINA_FORBIDDEN_TOKENS_FILE` points at a file
   *outside* the repository, every token in it is also checked. The file is never committed
   and its absence never fails the test.

**TD-1b** The scan covers the whole working tree (`docs/`, `src/`, `tests/`, top-level
files), not only `tests/fixtures/`.

**TD-2** Two canonical fixtures live in `tests/fixtures/`: `broker_ejemplo.csv`,
`banco_ejemplo.xlsx`. They are the oracle sources for spec §12.

**TD-3** Edge-case inputs are **generated in the test** by mutating a canonical fixture in a
temp dir (add a row, corrupt a cent, permute rows, drop a column), never by hand-maintaining
dozens of near-duplicate fixture files. Each builder helper lives in `tests/builders.py`.

**TD-4** Spanish source text inside fixtures stays in Spanish (that is what real exports
contain); code, comments, test names and docs are English.

---

## 3. Unit tests — `money.py` (parsing, normalization, rounding)

| ID | Test | Rules |
|---|---|---|
| T-001 | `"1.234,56€"` → `Decimal("1234.56")` | R-7.6 |
| T-002 | `"12,40€"` (no thousands group) | R-7.6 |
| T-003 | `"1.234.567,89€"` (two thousands groups) | R-7.6 |
| T-004 | `"-650,25€"` negative | R-7.6 |
| T-005 | `"0,00€"` zero | R-7.6 |
| T-006 | `"1.126,84€ EUR"` (header form, trailing ISO code) | R-7.3 |
| T-007 | Non-breaking space (U+00A0) before `€` parses identically | R-7.6 |
| T-008 | Leading/trailing whitespace tolerated | R-7.6 |
| T-009 | `"1234.56"` (dot-decimal, wrong locale) raises `ParseError` | R-7.6 |
| T-010 | `""` raises `ParseError` | R-7.6 |
| T-011 | `"abc"` raises `ParseError` | R-7.6 |
| T-012 | `"1,234"` (3 decimals) raises `ParseError` | R-7.6 |
| T-013 | Parsed values are `Decimal`, never `float` | R-1.1, R-1.2 |
| T-014 | Rounding: `2.675` → `2.68` (half-up, not banker's) | R-1.5 |
| T-015 | Rounding: `-0.005` → `-0.01` (half away from zero) | R-1.5 |
| T-016 | Rounding applied once: `sum(round(x)) != round(sum(x))` case documented and the code takes the round-last path | R-1.5 |
| T-017 | IBAN normalization: `"es00 0000 …"` → upper, no spaces | R-1.12 |
| T-018 | IBAN comparison is exact after normalization (one differing char ⇒ no match) | R-1.12 |
| T-019 | Name: case difference matches | R-1.13 |
| T-020 | Name: word-order permutation matches (`A B C D` vs `C D A B`) | R-1.13 |
| T-021 | Name: trailing/multiple internal spaces match | R-1.13 |
| T-022 | Name: vowel accents fold (`FERNÁNDEZ` = `FERNANDEZ`) | R-1.13 |
| T-023 | Name: `Ñ` does **not** fold (`PEÑA` ≠ `PENA`) | R-1.13 |
| T-024 | Name: different token multiset does not match (`A B C` vs `A B`) | R-1.13, R-1.14 |
| T-025 | Name: no substring/edit-distance matching (`JUAN` ≠ `JUANA`) | R-1.14 |
| T-026 | Spanish date `"07/03/2027"` → `date(2027,3,7)` (day-first) | R-1.11 |
| T-027 | `"31/02/2027"` raises `ParseError` (no fallback parse) | R-1.11 |
| T-028 | `"2027-03-07"` in a `DD/MM/YYYY` column raises `ParseError` | R-1.11 |

## 4. Unit tests — `models.py`

| ID | Test | Rules |
|---|---|---|
| T-050 | `LedgerEntry` is frozen (mutation raises) | R-2.1 |
| T-051 | `raw` is preserved verbatim for every entry | R-2.2 |
| T-052 | `cash_effect` for `BUY` = amount + fee | R-2.6 |
| T-053 | `cash_effect` for `SELL` = amount + fee | R-2.6 |
| T-054 | `cash_effect` for `BUY` with no fee = amount | R-2.6 |
| T-055 | `cash_effect` for `DIVIDEND` ignores `tax_eur` | R-2.6, R-2.7 |
| T-056 | `cash_effect` for `INTEREST` ignores `tax_eur` | R-2.6, R-2.7 |
| T-057 | `cash_effect` for `TECHNICAL_ADJUSTMENT` is exactly `0` | R-2.6, R-2.17 |
| T-058 | `cash_effect` parametrized across **all** `MovementType` members (no member unhandled) | R-2.3, R-2.6 |
| T-059 | `RSU_VESTING` / `ESPP_PURCHASE` construction raises `NotImplementedError` naming D3 | R-2.4 |
| T-060 | `entry_id` from `transaction_id` when present | R-1.21 |
| T-061 | `entry_id` deterministic hash when absent; two runs give the same id | R-1.21 |
| T-062 | `entry_id` differs for two rows differing only in `source_row` | R-1.21 |
| T-063 | `BUY` with positive amount raises `ValidationError` | R-2.10 |
| T-064 | `BUY` with negative quantity raises | R-2.10 |
| T-065 | `SELL` with negative amount raises | R-2.11 |
| T-066 | `SELL` with positive quantity raises | R-2.11 |
| T-067 | `DIVIDEND` with a non-`None` `quantity` raises | R-2.12, R-6.2a |
| T-068 | Positive `fee_eur` raises | R-2.13 |
| T-069 | `amount_eur == 0` allowed, emits warning | R-2.16 |
| T-070 | `TECHNICAL_ADJUSTMENT` with non-zero cash effect raises | R-2.17 |
| T-071 | Every exception class carries its required structured fields | R-1.23 |

## 5. Unit tests — broker CSV adapter

| ID | Test | Rules |
|---|---|---|
| T-100 | Canonical fixture yields exactly 18 entries | R-6.14, §12.2 |
| T-101 | Field-by-field assertion of row 1 (`CUSTOMER_INBOUND`) | R-6.2 |
| T-102 | Field-by-field assertion of row 4 (`BUY` with fee) | R-6.2 |
| T-103 | Field-by-field assertion of row 6 (`DIVIDEND` with FX metadata) | R-6.2, R-2.7 |
| T-104 | Dividend row does **not** populate `quantity` (phantom-shares guard) | R-6.2a |
| T-105 | `account` is `"positions"` for TRADING/DELIVERY, `"cash"` otherwise | R-6.4 |
| T-106 | All 10 `(category,type)` pairs map as specified (parametrized) | R-6.5 |
| T-107 | Unknown `(category,type)` raises `UnknownMovementError` with the observed pair | R-6.6, R-5.3 |
| T-108 | `DELIVERY` + non-`MIGRATION` type raises | R-6.15 |
| T-109 | `CUSTOMER_INBOUND` leaves classification to §3 (still pre-classified external at adapter output) | R-6.7, R-5.2 |
| T-110 | Split trade produces two entries, not one merged | R-6.8 |
| T-111 | Empty numeric string → `None`, not `Decimal("0")` | R-6.3 |
| T-112 | Missing required column raises `ParseError` naming it | R-6.1 |
| T-113 | Extra unknown column → warning, value preserved in `raw` | R-6.1, R-2.2 |
| T-114 | `date` column wins when it differs from `datetime` by days | R-1.9 |
| T-115 | `date` column wins across a UTC day boundary (23:16Z, next-day booking date) | R-1.9, R-1.10 |
| T-116 | `currency != "EUR"` raises `UnsupportedCurrencyError` | R-1.7 |
| T-117 | Duplicate `transaction_id` in one file raises `DuplicateSourceError` | R-2.14 |
| T-118 | UTF-8 BOM stripped | R-1.15 |
| T-119 | cp1252-encoded file parses with a fallback warning | R-1.15 |
| T-120 | Undecodable bytes raise `ParseError` | R-1.15 |
| T-121 | CRLF and LF files produce identical entries | R-1.17 |
| T-122 | Quoted field containing a comma parses as one field | R-1.16 |
| T-123 | Quoted field containing escaped quotes parses | R-1.16 |
| T-124 | Quoted field containing a newline parses | R-1.16 |
| T-125 | Trailing blank lines ignored | R-1.17 |
| T-126 | Empty file → 0 entries, 0 declarations, 1 warning | R-1.18 |
| T-127 | Header-only file → same | R-1.18 |
| T-128 | `source_row` is the physical 1-indexed row (header = 1) | R-1.19 |
| T-129 | MIGRATION pair tagged `TECHNICAL_ADJUSTMENT`, both kept | R-6.14 |
| T-130 | MIGRATION group of 1 raises `MigrationPairError` | R-6.11 |
| T-131 | MIGRATION group of 3 raises | R-6.11 |
| T-132 | MIGRATION quantities not exact inverses raises | R-6.12 |
| T-133 | MIGRATION prices differ raises | R-6.12 |
| T-134 | MIGRATION with non-zero amount raises | R-6.13 |
| T-135 | Account declaration extracted from first self-referencing `CUSTOMER_INBOUND` | R-6.16 |
| T-136 | No such row → no declaration + warning | R-6.17 |
| T-137 | Adapter is pure: no network/clock/env (monkeypatched clock changes nothing) | R-5.4 |

## 6. Unit tests — bank XLSX adapter

| ID | Test | Rules |
|---|---|---|
| T-200 | Canonical fixture yields exactly 7 entries | §12.1 |
| T-201 | Header block located when preceded by 0, 3 and 8 blank rows | R-7.2 |
| T-202 | Missing `CUENTA` label raises `ParseError` naming it | R-7.2 |
| T-203 | Missing `TITULAR` / `SALDO` / `FECHA` each raise (parametrized) | R-7.2 |
| T-204 | Header balance with trailing ` EUR` parses | R-7.3 |
| T-205 | Movements header row located case/accent-insensitively | R-7.4 |
| T-206 | Parsing stops at first blank row after the table; footer ignored + warning | R-7.5 |
| T-207 | Blank row **inside** the table stops parsing + warning (no silent skip) | R-7.5 |
| T-208 | Numeric (non-string) amount cell → `Decimal(str(v))` + exactness warning | R-7.7 |
| T-209 | Numeric balance cell likewise | R-7.7 |
| T-210 | Date cell as `datetime` object parses identically to the string form | R-7.8 |
| T-211 | `Divisa != EUR` raises | R-7.9 |
| T-212 | Each of the 9 concept rules matches its canonical example (parametrized) | R-7.10 |
| T-213 | Rule order: a string matching both rule 3 and rule 4 takes rule 3 | R-7.10 |
| T-214 | `TRANSFERENCIA DE X, CONCEPTO y.` → name `X`, concept suffix stripped | R-7.10 |
| T-215 | `TRANSFERENCIA DE X` without suffix → name `X` | R-7.10 |
| T-216 | Accent/case-insensitive concept matching (`Recibo`, `RECÍBO`) | R-7.10 |
| T-217 | Unmatched concept with negative amount raises `UnknownMovementError` | R-7.11 |
| T-218 | Unmatched concept with positive amount raises | R-7.11 |
| T-219 | `date` = `Fecha operación`, `value_date` = `Fecha valor`, including the rows where they differ | R-7.14 |
| T-220 | Newest-first source order is re-sorted; entry order matches R-1.22 | R-7.15, R-1.22 |
| T-221 | Declaration carries normalized IBAN, holder, export date | R-7.16 |
| T-222 | Second worksheet present → warning, ignored | R-7.1 |
| T-223 | Bank rows carry no `counterparty_iban` (name-only classification path) | R-7.13 |

## 7. Unit tests — classification, reconciliation, Section 1

### 7.1 Classification (§3)

| ID | Test | Rules |
|---|---|---|
| T-300 | IBAN match → internal, `is_external_flow=False` | R-3.4(1), R-3.5 |
| T-301 | Unknown IBAN → external | R-3.4(3) |
| T-302 | No IBAN + holder-name match → internal | R-3.4(2) |
| T-303 | No IBAN + different name → external | R-3.4(3) |
| T-304 | Cross-file: broker row classified via the bank file's declaration | R-3.1, R-3.2, §12.3 |
| T-305 | Direction: positive → `INTERNAL_TRANSFER_IN`, negative → `_OUT` | R-3.5 |
| T-306 | Internal transfer with zero cash effect raises `ValidationError` | R-3.5 |
| T-307 | Same IBAN, two different holders → `AccountConflictError` | R-3.3 |
| T-308 | External row whose name matches an owned holder → R-3.6 warning emitted, still external | R-3.6 |
| T-309 | Manifest records the exact `owned_accounts` used | R-3.7 |
| T-310 | Classification is idempotent (running the pass twice changes nothing) | R-1.20 |
| T-311 | Non-transfer movement types are left with `is_external_flow=None` | R-2.3 |

### 7.2 Reconciliation (§8)

| ID | Test | Rules |
|---|---|---|
| T-350 | Bank fixture reconciles with zero discrepancy on every row | R-8.2, §12.1 |
| T-351 | One cent corrupted → `ReconciliationError` with exact row, expected, declared, delta | R-8.2, R-1.23 |
| T-352 | Same-day rows swapped → reconciliation fails (ordering is load-bearing) | R-1.22, R-8.2 |
| T-353 | First declared-balance row establishes baseline, is not itself checked | R-8.3 |
| T-354 | Broker account (no declared balances) → no error, exactly one "unverified" warning | R-8.4 |
| T-355 | Exactly one such warning per `(institution, account)`, not per row | R-8.4 |
| T-356 | Final declared balance vs header balance mismatch raises | R-8.5 |
| T-357 | Comparison uses exact `Decimal` equality (a 0.001 difference still fails) | R-8.2, R-1.5 |

### 7.3 Section 1 (§9)

| ID | Test | Rules |
|---|---|---|
| T-400 | `cash_balance` per `(institution, account)` matches the §12.2 running column, row by row | R-9.1, §12.2 |
| T-401 | Final broker cash = `21937.82` | §12.2 |
| T-402 | `quantity_held` excludes `TECHNICAL_ADJUSTMENT` rows | R-9.2, R-2.5 |
| T-403 | Holdings match §12.2 exactly (IBM 10, MSFT 10.15, BTC 0.022, bond 50, fund 30) | §12.2 |
| T-404 | `savings_flow` contribution parametrized over all 13 movement types | R-9.5, R-2.3 |
| T-405 | Internal transfers contribute 0 | R-9.5, §12.3 |
| T-406 | `DIVIDEND`/`INTEREST` contribute 0 | R-9.5 |
| T-407 | Broker fixture `savings_flow` total = `+3500.00` | §12.3 |
| T-408 | Bank fixture `savings_flow` for 2027-03 = `+5183.75` | §12.1 |
| T-409 | Month with no entries appears with `savings_flow = 0` and carried balances | R-9.6 |
| T-410 | `t0` seeding: `savings_only(t0) == real_net_worth(t0)` | R-9.7 |
| T-411 | Recursion: `savings_only(t) − savings_only(t−1) == savings_flow(t)` for every t | R-9.8 |
| T-412 | `gap(t) == real_net_worth(t) − savings_only(t)` for every t | R-9.9 |
| T-413 | Partial final month carries `as_of` and a partial flag | R-9.10 |
| T-414 | `completeness == "cash_only"` while D1 is outstanding | R-9.4 |
| T-415 | Every emitted figure is `Decimal` | R-9.11, R-1.1 |
| T-416 | Empty ledger → empty series, no exception | R-1.18 |
| T-417 | Single-entry ledger → one period, `gap == 0` at `t0` | R-9.7 |

## 8. Integration, property-based and system tests

### 8.1 Pipeline / CLI

| ID | Test | Rules |
|---|---|---|
| T-500 | End-to-end over both fixtures produces the expected manifest | R-11.1, R-11.5 |
| T-501 | Adapter selected by header shape, not filename | R-11.2 |
| T-502 | Unrecognized shape raises listing adapters tried | R-11.2 |
| T-503 | Same file content under two names → `DuplicateSourceError` | R-2.15 |
| T-504 | Warnings printed before any figure (assert output ordering) | R-11.3, R-1.24 |
| T-505 | `ReconciliationError` → non-zero exit, no report file written | R-11.4 |
| T-506 | `ValidationError` → same | R-11.4 |
| T-507 | Manifest contains input SHA-256s, owned accounts, warnings, version, series | R-11.5 |
| T-508 | Two runs → byte-identical manifests (also G-7) | R-1.20 |

### 8.2 Property-based (Hypothesis)

| ID | Property | Rules |
|---|---|---|
| T-600 | For any valid Spanish amount string, parse→format→parse is a fixed point | R-7.6 |
| T-601 | For any permutation of input rows, sorted ledger and all balances are identical | R-1.22, R-1.20 |
| T-602 | For any ledger, `Σ cash_effect == final cash balance` | R-9.1 |
| T-603 | For any ledger, `savings_only(t) − savings_only(t−1) == savings_flow(t)` | R-9.8 |
| T-604 | For any ledger, `gap(t) − gap(t−1) == (real(t) − real(t−1)) − savings_flow(t)` | R-9.9 |
| T-605 | Name normalization is idempotent and symmetric (`match(a,b) == match(b,a)`) | R-1.13 |
| T-606 | Classification is idempotent under repeated application | R-1.20 |
| T-607 | No generated input makes any money field a `float` | R-1.1 |

### 8.3 Rendering (§10)

| ID | Test | Rules |
|---|---|---|
| T-700 | Renderer consumes the series and performs no arithmetic on money (AST check on the render module) | R-10.4 |
| T-701 | Every colour token is defined in the base `:root` block, not only in a theme block | R-10.3 |
| T-702 | Band colour switches at the interpolated zero-crossing, not at the nearest data point | R-10.2 |
| T-703 | Tooltip markup is only produced on the click path (no hover handler emitted) | R-10.2 |
| T-704 | `cash_only` qualifier rendered whenever completeness is `cash_only` | R-10.5, R-9.4 |
| T-705 | PDF export smoke test: file produced, non-zero size, one page | R-10.1 |
| T-706 | Rendered at a 390px viewport, tooltip text does not wrap onto an extra line (parametrized over every month, since label/value lengths vary) | R-10.2, R-10.2a |
| T-707 | Rendered at 390px, a pixel sample inside the open tooltip differs measurably from the tooltip's own flat background colour when a coloured series/band is directly behind it (proves visible translucency, not just a non-1.0 alpha value in the CSS) | R-10.2, R-10.2b |
| T-708 | Visual checks in T-700..T-707 run at 390px width first; a desktop-width (≥ 900px) pass is a separate, additional case, never a substitute | R-10.2a |
| T-709 | No rule in this spec's rendering section is satisfied by a component that uses `backdrop-filter` — AST/CSS scan of the render module's stylesheet | R-10.2b |

### 8.4 Meta-tests (the gates themselves)

| ID | Test | Gate |
|---|---|---|
| T-901 | No `# pragma: no cover` outside the allowlist | G-2 |
| T-902 | No floats anywhere under `src/fina/` (AST scan) | G-6 |
| T-903 | Pipeline determinism across two runs | G-7 |
| T-904 | No `skip`/`xfail` markers in the suite | G-8 |
| T-905 | Whole working tree contains no unallowlisted IBAN-shaped or personal-name-shaped strings; optional external token file also checked | TD-1, TD-1a, TD-1b |

---

## 9. Traceability matrix

Maintained as the single source of truth that no rule is untested. Rows are added as work
packages land; a work package is not done until its rules appear here.

| Rule | Tests |
|---|---|
| R-0.3 | (process rule — verified by review, not by test) |
| R-1.1 | T-013, T-415, T-607, T-902 |
| R-1.2 | T-013, T-902 |
| R-1.3 | T-403 |
| R-1.4 | T-103 |
| R-1.5 | T-014, T-015, T-016, T-357 |
| R-1.6 | T-102, T-103 |
| R-1.7 | T-116, T-211 |
| R-1.8 | T-026 |
| R-1.9 | T-114, T-115 |
| R-1.10 | T-115 |
| R-1.11 | T-026, T-027, T-028 |
| R-1.12 | T-017, T-018, T-221 |
| R-1.13 | T-019..T-024, T-605 |
| R-1.14 | T-024, T-025 |
| R-1.15 | T-118, T-119, T-120 |
| R-1.16 | T-122, T-123, T-124 |
| R-1.17 | T-121, T-125 |
| R-1.18 | T-126, T-127, T-416 |
| R-1.19 | T-128 |
| R-1.20 | T-310, T-508, T-601, T-606, T-903 |
| R-1.21 | T-060, T-061, T-062 |
| R-1.22 | T-220, T-352, T-601 |
| R-1.23 | T-071, T-351 |
| R-1.24 | T-504 |
| R-2.1 | T-050 |
| R-2.2 | T-051, T-113 |
| R-2.3 | T-058, T-311, T-404 |
| R-2.4 | T-059 |
| R-2.5 | T-402 |
| R-2.6 | T-052..T-058 |
| R-2.7 | T-055, T-056, T-103 |
| R-2.8 | T-221 |
| R-2.9 | T-135 |
| R-2.10 | T-063, T-064 |
| R-2.11 | T-065, T-066 |
| R-2.12 | T-067 |
| R-2.13 | T-068 |
| R-2.14 | T-117 |
| R-2.15 | T-503 |
| R-2.16 | T-069 |
| R-2.17 | T-057, T-070 |
| R-3.1 | T-304 |
| R-3.2 | T-304 |
| R-3.3 | T-307 |
| R-3.4 | T-300..T-303 |
| R-3.5 | T-300, T-305, T-306 |
| R-3.6 | T-308 |
| R-3.7 | T-309, T-507 |
| R-4.1 | T-103 (FX metadata stored, not applied) |
| R-4.2 | (deferred — no test until an converting adapter exists) |
| R-5.1 | T-100, T-200 |
| R-5.2 | T-109 |
| R-5.3 | T-107, T-217, T-218 |
| R-5.4 | T-137 |
| R-6.1 | T-112, T-113 |
| R-6.2 | T-101, T-102, T-103 |
| R-6.2a | T-067, T-104 |
| R-6.3 | T-111 |
| R-6.4 | T-105 |
| R-6.5 | T-106 |
| R-6.6 | T-107 |
| R-6.7 | T-109 |
| R-6.8 | T-110 |
| R-6.9 | T-403 |
| R-6.10 | T-129 |
| R-6.11 | T-130, T-131 |
| R-6.12 | T-132, T-133 |
| R-6.13 | T-134 |
| R-6.14 | T-100, T-129 |
| R-6.15 | T-108 |
| R-6.16 | T-135 |
| R-6.17 | T-136 |
| R-7.1 | T-222 |
| R-7.2 | T-201, T-202, T-203 |
| R-7.3 | T-006, T-204 |
| R-7.4 | T-205 |
| R-7.5 | T-206, T-207 |
| R-7.6 | T-001..T-012, T-600 |
| R-7.7 | T-208, T-209 |
| R-7.8 | T-210 |
| R-7.9 | T-211 |
| R-7.10 | T-212..T-216 |
| R-7.11 | T-217, T-218 |
| R-7.12 | (known risk — no test; revisited when a card adapter is specified) |
| R-7.13 | T-223 |
| R-7.14 | T-219 |
| R-7.15 | T-220 |
| R-7.16 | T-221 |
| R-8.1 | T-350 |
| R-8.2 | T-350, T-351, T-352, T-357 |
| R-8.3 | T-353 |
| R-8.4 | T-354, T-355 |
| R-8.5 | T-356 |
| R-9.1 | T-400, T-602 |
| R-9.2 | T-402 |
| R-9.3 | T-414 (cash term only while D1 stands) |
| R-9.4 | T-414, T-704 |
| R-9.5 | T-404..T-408 |
| R-9.6 | T-409 |
| R-9.7 | T-410, T-417 |
| R-9.8 | T-411, T-603 |
| R-9.9 | T-412, T-604 |
| R-9.10 | T-413 |
| R-9.11 | T-415 |
| R-9.12 | (deferred D2) |
| R-10.1 | T-705 |
| R-10.2 | T-702, T-703, T-706, T-707 |
| R-10.2a | T-706, T-708 |
| R-10.2b | T-707, T-709 |
| R-10.3 | T-701 |
| R-10.4 | T-700 |
| R-10.5 | T-704 |
| R-11.1 | T-500 |
| R-11.2 | T-501, T-502 |
| R-11.3 | T-504 |
| R-11.4 | T-505, T-506 |
| R-11.5 | T-507, T-508 |
| R-12.1 | T-100, T-350, T-400, T-401, T-403, T-407, T-408 |
