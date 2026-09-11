# Surviving mutants

Per test-plan.md G-3: every mutant `mutmut run` reports as `survived` must be listed here with
a one-line justification (equivalent mutant, or a test gap accepted by the reviewer). An
unlisted survivor is a defect.

Mutation IDs are `mutmut`'s own identifiers (`fina.<module>.x_<function>__mutmut_<n>`); re-run
`python -m mutmut results` to reproduce the current list, and `python -m mutmut show <id>` to
see the exact diff.

## WP-1 / WP-2 (`money.py`, `models.py`)

Run: 163 mutants generated, 160 killed, 3 survived (98.2% kill rate; threshold is 95%).

| Mutant ID | Diff | Justification |
|---|---|---|
| `fina.money.x__name_multiset__mutmut_6` | `normalized.split(" ")` → `normalized.split(None)` | **Equivalent.** `_name_multiset` always calls `split` on the output of `normalize_name`, which strips leading/trailing whitespace and collapses every internal run of whitespace to a single regular space before this call. On any string with no leading/trailing space and only single internal spaces, `str.split(" ")` and `str.split(None)` return identical results. No input reachable through the public API (`normalize_name`, `names_match`) can distinguish the two. |
| `fina.models.x_compute_entry_id__mutmut_4` | `"\x1f"` → `"\x1F"` (separator literal) | **Equivalent.** `\x1f` and `\x1F` are the same escape sequence (hex digits are case-insensitive in Python string literals); both produce the single character `chr(0x1f)`. Byte-for-byte identical to the original at runtime — verified: `"\x1f" == "\x1F"` is `True`. |
| `fina.models.x_compute_entry_id__mutmut_12` | `.encode("utf-8")` → `.encode("UTF-8")` | **Equivalent.** Python's codec lookup is case-insensitive; `"abc".encode("utf-8") == "abc".encode("UTF-8")` is `True`. No observable difference in the resulting digest. |

All three are genuine equivalent mutants (verified interactively, not merely asserted): no
test can distinguish mutant from original because the two produce byte-identical behaviour on
every input reachable through the module's public functions. Excluding them, the real kill
rate is 160/160 = 100%.

## WP-3 (`io_utils.py`)

`io_utils.py` is not in G-3's mandatory-threshold list (money.py, models.py,
reconciliation.py, section1.py at 95%; adapters/*.py, classification.py, pipeline.py at 90%),
but mutation testing was still run for the same reason as every other module: coverage proves
lines ran, not that a wrong answer would be caught.

Run (money.py + models.py + errors.py + io_utils.py combined): 242 mutants generated, 235
killed, 7 survived (97.1%). The 3 from WP-1/WP-2 above are unchanged. The 4 new ones:

| Mutant ID | Diff | Justification |
|---|---|---|
| `fina.io_utils.x_decode_text_with_fallback__mutmut_3` | `"utf-8-sig"` → `"UTF-8-SIG"` | **Equivalent.** Python's codec lookup is case-insensitive (same reasoning as the WP-2 `"utf-8"`/`"UTF-8"` survivor above); `data.decode("utf-8-sig") == data.decode("UTF-8-SIG")` for every input. |
| `fina.io_utils.x_decode_text_with_fallback__mutmut_7` | `"cp1252"` → `"CP1252"` | **Equivalent**, same reason. |
| `fina.io_utils.x_decode_text_with_fallback__mutmut_31` | drops the explicit `source_row=None` keyword argument to `Warning(...)` | **Equivalent.** `Warning.source_row` defaults to `None` (see `models.py`); omitting the keyword entirely is indistinguishable from passing its own default value. |
| `fina.io_utils.x_empty_table_warning__mutmut_5` | drops the explicit `source_row=None` keyword argument to `Warning(...)` | **Equivalent**, same reason as above. |

Excluding the 7 documented equivalents (3 from WP-1/WP-2, 4 here), the real kill rate across
all four modules is 235/235 = 100%.

One additional io_utils.py mutant appeared only once WP-4's test suite (which also exercises
io_utils indirectly) was added to the run:

| Mutant ID | Diff | Justification |
|---|---|---|
| `fina.io_utils.x_read_csv_table__mutmut_12` | `end -= 1` → `end = 1` inside the trailing-blank-line trim loop | **Timeout, not a survivor.** When every row including the header is blank (`test_file_of_only_blank_lines_is_fully_empty`, text `"\n\n\n"`), this mutant gets stuck: `end` is pinned at `1` forever once the loop reaches it, and `all_rows[0]` (the "header") is itself blank, so the loop condition never becomes false. `mutmut` reports this as `timeout`, a distinct status from `survived` -- the mutation testing run correctly detects the behavioural change (a hang), it just can't classify it as a normal pass/fail. Counted as caught for kill-rate purposes. |

## WP-4 (`adapters/broker_csv.py`)

Run (all four modules + broker_csv.py): 825 mutants generated, 812 killed, 12 survived, 1
timeout (see above). `broker_csv.py` alone: 583 mutants, 578 killed, 5 survived = **99.1%**
(threshold: 90%).

| Mutant ID | Diff | Justification |
|---|---|---|
| `fina.adapters.broker_csv.x__row_to_fields__mutmut_18` | `zip(header, row, strict=True)` → `strict=None` | **Equivalent.** This call is only reached after `len(row) != len(header)` has already raised `ParseError` above it in the same function -- by construction, `row` and `header` are always the same length here, so `strict=True`, `strict=False`, and omitting `strict` entirely all produce identical output. |
| `fina.adapters.broker_csv.x__row_to_fields__mutmut_21` | same call, `strict=True` → omitted (defaults to `False`) | **Equivalent**, same reason. |
| `fina.adapters.broker_csv.x__row_to_fields__mutmut_22` | same call, `strict=True` → `strict=False` | **Equivalent**, same reason. |
| `fina.adapters.broker_csv.x__parse_row__mutmut_73` | `is_migration = category == "DELIVERY" and type_ == "MIGRATION"` → `... or ...` | **Equivalent.** This line only runs after `_MOVEMENT_MAP.get((category, type_))` has already succeeded (an unknown pair raises `UnknownMovementError` first). `"MIGRATION"` appears in `_MOVEMENT_MAP` exactly once, paired only with `"DELIVERY"`. So for any row that reaches this line, `category == "DELIVERY"` and `type_ == "MIGRATION"` are always equal in truth value -- `and` and `or` of two values that are always equal produce the same result. |
| `fina.adapters.broker_csv.x__extract_account_declaration__mutmut_12` | drops the explicit `source_row=None` keyword argument to `Warning(...)` | **Equivalent**, same reasoning as the `io_utils.py` / WP-3 survivors above: `Warning.source_row` defaults to `None`. |

Excluding these 5 documented equivalents, `broker_csv.py`'s real kill rate is 578/578 = 100%.
Across every module built through WP-4, combining all documented equivalents (3 + 4 + 5 = 12)
and the one timeout, the real kill rate is 812/812 = 100%.

## WP-5 (`adapters/bank_xlsx.py`)

Final run (all six modules through WP-5): 1452 mutants generated, 1432 killed, 20 survived, 0
timeout (the WP-3 `read_csv_table` mutant above is a genuine infinite loop whose classification
as `timeout` vs `killed` depends on scheduling/load between runs; it never shows as `survived`
and is treated as caught either way, per the WP-3 section above). `bank_xlsx.py` alone: 626
mutants, 618 killed, 8 survived = **98.7%** (threshold: 90%).

(An earlier pass through this same file's mutants found 13 survivors instead of 8: 2 were
genuine test-construction bugs, fixed below rather than written off; 3 more were genuine gaps
closed with new integration tests, also below. The 8 listed here are the ones that remained
after those fixes and were independently re-verified as equivalent.)

| Mutant ID | Diff | Justification |
|---|---|---|
| `fina.adapters.bank_xlsx.x__find_label_value__mutmut_8` | `cast(int, cell.row)` → `cast(None, cell.row)` | **Equivalent.** `typing.cast` is a pure runtime no-op — its first argument is never inspected at runtime, only by static type checkers. `cast(int, x)` and `cast(None, x)` return the identical object `x`. Same reasoning as every other `cast()` survivor documented for earlier packages. |
| `fina.adapters.bank_xlsx.x__find_label_value__mutmut_13` | `cast(int, cell.column)` → `cast(None, cell.column)` | **Equivalent**, same reason. |
| `fina.adapters.bank_xlsx.x__parse_header_date__mutmut_5` | `raw.split("|", 1)[0]` → `raw.split("|")[0]` (maxsplit dropped, defaults to unlimited) | **Equivalent.** Only index `[0]` of the result is ever used. `str.split(sep, maxsplit)`'s first element is identical regardless of `maxsplit` (a smaller maxsplit only limits how many *further* splits happen; it can never change where the *first* split occurs). Verified interactively: `"10/03/2027 \| 09:00:00 \| extra".split("\|", 1)[0] == "10/03/2027 \| 09:00:00 \| extra".split("\|")[0] == "10/03/2027 \| 09:00:00 \| extra".split("\|", 2)[0]`. |
| `fina.adapters.bank_xlsx.x__parse_header_date__mutmut_8` | same call, `maxsplit=1` → `maxsplit=2` | **Equivalent**, same reason. |
| `fina.adapters.bank_xlsx.x__find_movements_header_row__mutmut_6` | `cast(int, row[0].row)` → `cast(None, row[0].row)` | **Equivalent**, `cast()` no-op (see above). |
| `fina.adapters.bank_xlsx.x__find_movements_header_row__mutmut_10` | `row[0].row` → `row[1].row` | **Equivalent.** `row` is one tuple of `Cell` objects yielded by a single call to `Worksheet.iter_rows()`; by openpyxl's own contract every cell in that tuple belongs to the same physical spreadsheet row, so `.row` is identical across all of them. Verified interactively: for a row appended via `ws.append([...])`, `[c.row for c in row]` is `[1, 1, 1, 1, 1, 1]` — `row[0].row` and `row[1].row` can never differ. |
| `fina.adapters.bank_xlsx.x_parse__mutmut_16` | drops the explicit `source_row=None` keyword argument to `Warning(...)` | **Equivalent**, same reasoning as the WP-3/WP-4 `Warning(source_row=None)` survivors: `Warning.source_row` defaults to `None`. |
| `fina.adapters.bank_xlsx.x__build_entry__mutmut_2` | `compute_cash_effect(row.movement_type, row.amount_eur, None)` → `compute_cash_effect(None, row.amount_eur, None)` | **Equivalent, by construction of this module.** `compute_cash_effect` only branches on its first argument for `MovementType.BUY`, `MovementType.SELL`, `MovementType.TECHNICAL_ADJUSTMENT`, or a D3-deferred type; every other value (including `None`) falls through to the same `return amount_eur`. `bank_xlsx.py`'s `_match_concept` is a fixed, exhaustively-enumerated set of 9 rules that only ever produce `MovementType.EXPENSE`, `MovementType.EXTERNAL_DEPOSIT`, `MovementType.EXTERNAL_WITHDRAWAL`, or `MovementType.PAYROLL_INCOME` (grep-verified: no other `MovementType.*` literal appears in this file) — none of which is BUY/SELL/TECHNICAL_ADJUSTMENT/D3-deferred. So for every `row.movement_type` this module can ever actually produce, `compute_cash_effect(row.movement_type, ...)` and `compute_cash_effect(None, ...)` return the same value. (This mutant would *not* be equivalent in a module that could produce BUY/SELL/TECHNICAL_ADJUSTMENT rows, e.g. `broker_csv.py` — the equivalence is local to `bank_xlsx.py`'s restricted output domain, not a property of `compute_cash_effect` itself.) |

Two mutants that looked at first like genuine test gaps turned out to be **real bugs in the
tests**, not equivalences, and were fixed rather than written off:

- `_find_label_value__mutmut_39` (`(None, "")` → `(None, "XXXX")` in the empty-candidate
  check) survived because the original regression test saved its worksheet to an `.xlsx` file
  and reloaded it before calling `_find_label_value` — and openpyxl does not round-trip a
  genuinely empty-string cell value through `save`/`load_workbook`: it comes back as `None`,
  silently collapsing the test into the already-covered `None` case instead of the `""` case
  it was meant to target (verified interactively). Fixed by asserting directly against the
  in-memory `Worksheet` (no save/reload), which does preserve `""`.
- `_parse_header_date__mutmut_2` (`raw.split("\|", 1)` → `raw.split(None, 1)`, i.e. pipe-split
  vs whitespace-split) survived because the original test's input, `"10/03/2027 \| 09:00:00 \|
  extra"`, has a space immediately before the first `\|` — so the first *whitespace*-delimited
  token and the first *pipe*-delimited token happen to be identical (`"10/03/2027"` either
  way), and the two splitting strategies were never actually distinguished. Fixed by adding a
  second test, `"10/03/2027\|09:00:00"` (no space before the `\|`), where the two strategies
  diverge: pipe-split still isolates the date; whitespace-split sees no whitespace at all and
  returns the full string as one token, which fails to parse as a date.

Three more looked like genuine test gaps and were closed with new integration-level tests
rather than written off as equivalent, since they are not equivalent — they reflect
`_read_header_block_from_sheet` passing `max_row=None` (unbounded) instead of `max_row=boundary`
for one of its four label lookups, which is only observable when the in-block label's own
candidates are all empty *and* a duplicate label with a real value exists beyond the boundary
(a condition the two-line canonical fixture never exercises, but a hand-built one can):

| Mutant ID | Diff | Resolution |
|---|---|---|
| `fina.adapters.bank_xlsx.x__read_header_block_from_sheet__mutmut_13` | `_find_label_value(ws, "CUENTA", ..., max_row=boundary)` → `max_row=None` | Closed by `test_read_header_block_cuenta_lookup_is_bounded_to_the_header_block`. |
| `fina.adapters.bank_xlsx.x__read_header_block_from_sheet__mutmut_24` | same, for `"TITULAR"` | Closed by `test_read_header_block_titular_lookup_is_bounded_to_the_header_block`. |
| `fina.adapters.bank_xlsx.x__read_header_block_from_sheet__mutmut_46` | same, for `"FECHA"` | Closed by `test_read_header_block_fecha_lookup_is_bounded_to_the_header_block`. |

Excluding the 8 documented equivalents above, `bank_xlsx.py`'s real kill rate is 618/618 = 100%.
Across every module built through WP-5, combining all documented equivalents from every package
(3 from WP-1/2 + 4 from WP-3 + 5 from WP-4 + 8 from WP-5 = 20, matching the 20 mutants this run
reports as `survived` exactly) and the one recurring timeout/hang, the real kill rate is
1432/1432 = 100%.

## WP-6 (`classification.py`)

Run (all seven modules through WP-6): 1563 mutants generated, 1542 killed, 20 survived, 1
timeout (the same recurring WP-3 `read_csv_table` hang, unchanged). `classification.py` alone:
111 mutants, **111 killed, 0 survived = 100%** (threshold: 90%).

An initial pass surfaced 11 survivors in `classification.py` -- all genuine test gaps, not
equivalent mutants, closed by strengthening/adding tests rather than written off:

| Mutant ID | Diff | Fix |
|---|---|---|
| `x_collect_owned_accounts__mutmut_5` | `continue` → `break` when skipping a no-IBAN declaration | Added `test_a_no_iban_declaration_does_not_stop_scanning_the_rest_of_the_list`: a no-IBAN declaration followed by a genuine IBAN conflict -- `break` would silently swallow the conflict. |
| `x_classify_entries__mutmut_21` | `continue` → `break` after appending a name-matched internal transfer | Added `test_a_name_matched_internal_transfer_does_not_stop_processing_later_entries`: asserts a second entry after the first is still classified, not dropped. |
| `x_classify_entries__mutmut_27`, `_28` | `"from"` → `"XXfromXX"` / `"FROM"` in the R-3.6 warning's direction wording | `test_t308_external_row_matching_owned_holder_name_warns_but_stays_external` now asserts the warning's exact message text (was previously a substring check on the counterparty name only). |
| `x_classify_entries__mutmut_39`, `_40`, `_41`, `_42` | Case/marker mutations of the warning message's fixed text | Same fix: an exact full-string assertion pins every word. |
| `x__reclassify_internal__mutmut_2` | `cash_effect_eur > 0` → `> 1` | T-305 gained a `Decimal("0.01")` case: strictly between 0 and 1, so `> 0` and `> 1` disagree. |
| `x__reclassify_internal__mutmut_13`, `_15` | Case/marker mutations of the `ValidationError.invariant` text | T-306 now asserts `err.invariant` exactly, not merely `"R-3.5" in err.invariant`. |

Excluding the same 20 already-documented equivalents from WP-1 through WP-5 (unchanged; the
recurring timeout is treated as caught, per the WP-3 section above), the real kill rate across
every module built through WP-6 is 1542/1542 = 100%.

## `file_sequence` patch (WP-2/WP-4/WP-5, before WP-7 — Q-F resolution)

R-1.22 changed from `(date, source_file, source_row)` to `(date, file_sequence, source_file)`;
`LedgerEntry` gained `file_sequence: int` (WP-2, no new mutants -- a bare field declaration),
`broker_csv.py` sets it to `source_row` (R-6.1a, T-360), `bank_xlsx.py` sets it to
`-source_row` and re-sorts by `(date, file_sequence)` instead of `(date, source_row)` (R-7.5a,
T-358/T-359/T-361). Full run after the patch: 1570 mutants generated (+7 over WP-6: `models.py`
unchanged at 46; `broker_csv.py` 583→585; `bank_xlsx.py` 626→631), 1550 killed, the same 20
survivors as every prior section (verified identical by exact mutant ID, not just count) — no
new survivor was introduced by any of the three patches, and no mutant on any of the three new
`file_sequence`-assignment lines or the changed sort-key line survived. `broker_csv.py`:
585 mutants, 5 survived (the same 5 already documented above) = 99.15%. `bank_xlsx.py`: 631
mutants, 8 survived (the same 8 already documented above) = 98.7%. Excluding the 20
already-documented equivalents, the real kill rate is 1550/1550 = 100%.

## WP-7 (`reconciliation.py`)

Run (all eight modules through WP-7): 1648 mutants generated, 1627 killed, 21 survived, 0
timeout. `reconciliation.py` alone: **78 mutants, 77 killed, 1 survived = 98.7%** (threshold:
95%).

| Mutant ID | Diff | Justification |
|---|---|---|
| `fina.reconciliation.x_reconcile__mutmut_18` | drops the explicit `source_row=None` keyword argument to the R-8.4 `Warning(...)` | **Equivalent**, same reasoning as every other `Warning(source_row=<default>)` survivor documented above (WP-3/WP-4): `Warning.source_row` defaults to `None`, so omitting the keyword is indistinguishable from passing it explicitly. |

An earlier draft of this module used `typing.cast()` to narrow `LedgerEntry.declared_balance`
from `Decimal | None` to `Decimal` at each use site (the established idiom elsewhere in this
codebase, e.g. `bank_xlsx.py`'s openpyxl-stub narrowing) and `zip(a, b, strict=False)` to walk
consecutive declared-balance pairs. Both idioms are structurally unkillable by any test
(`cast()` is a pure static-typing no-op; `strict=False`/`strict=None`/omitted are all the same
falsy value at runtime) -- fine in isolation, and already accepted as documented equivalents
elsewhere in this project, but here they would have added 5 more such survivors on top of the
one above, which combined with `reconciliation.py`'s smaller total mutant count would have
left the *raw* kill rate at 93.4%, below this module's 95% threshold despite every survivor
being genuinely equivalent. Rather than lean on documentation to explain away an avoidable
shortfall, the module was refactored to remove both idioms entirely: declared-balance rows are
narrowed once, in a single list comprehension mypy verifies without `cast()` (`entry.declared_
balance` is provably non-`None` inside `... for entry in ordered if entry.declared_balance is
not None`), and consecutive pairs are walked by index (`range(1, len(pairs))`) instead of
`zip()`. This is a genuine design improvement (one comprehension replaces N `cast()` call
sites), not a workaround -- it eliminates the mutants rather than merely justifying them.

Excluding the 1 documented equivalent, `reconciliation.py`'s real kill rate is 77/77 = 100%.
Across every module built through WP-7, combining all documented equivalents from every
package (3 + 4 + 5 + 8 + 1 = 21, matching the 21 mutants this run reports as `survived`
exactly), the real kill rate is 1627/1627 = 100%.

## WP-8 (`section1.py`)

Run (all nine modules through WP-8): 1814 mutants generated, 1793 killed, 21 survived, 0
timeout. `section1.py` alone: **166 mutants, 166 killed, 0 survived = 100%** (threshold: 95%).
The same 21 survivors as every prior section, unchanged -- no new equivalent needed here.

An initial pass found 12 real survivors (dropped `start=Decimal("0")` defaults on four
different `sum()` calls -- a real type bug, since `sum([], start=0)`'s `int` `0` equals
`Decimal("0")` by value but not by type, silently passing a bare `==` check; an inverted
`account == account` → `!=` filter; an off-by-one `date <= as_of` → `< as_of` boundary; three
D3 error-message text mutations; and two `compute_section1` call-site argument swaps that
would have zeroed every period's `savings_flow` without any all-zero-flow test noticing) plus
**8 timeouts**: `_next_month`/`_months_from`'s original `while True` loop hung forever under a
broken termination condition or step function, since no test spanned enough months to force a
wrong step to misbehave *quickly* rather than loop. Mutmut correctly detected each hang and
reported `timeout` rather than `survived`, so none of these were undetected -- but 8 timeouts
meaningfully slow every future run for no real benefit. Both were fixed:

- The 12 survivors were closed with new/strengthened tests: explicit `isinstance(..., Decimal)`
  checks on a filter that matches nothing, a two-distinct-accounts test proving
  `cash_balance`'s account filter actually excludes the other account, a same-day boundary
  test for `quantity_held`'s `<=`, an exact `NotImplementedError` message assertion for the D3
  branch, and a `compute_section1`-level test asserting each period's own `savings_flow`
  value (not just the standalone function tested in isolation) against a ledger with a
  distinct, non-zero flow per month.
- The 8 timeouts were eliminated by design, not by more tests: `_months_from` no longer walks
  month-by-month with a `while True` loop at all. It computes each endpoint's ordinal month
  index (`year * 12 + month - 1`, so consecutive calendar months are consecutive integers) and
  iterates a plain `range()` between them -- a loop that is *always* finite by construction,
  regardless of what a mutation does to the arithmetic inside it. A broken step or termination
  condition now produces a wrong-length list (an assertion failure, caught immediately) instead
  of a hang.

Excluding the 21 already-documented equivalents (unchanged from WP-7), the real kill rate
across every module built through WP-8 is 1793/1793 = 100%.

## WP-9 (`pipeline.py`, `cli.py`, `render/section1_chart.py`, `render/prepare.py`, adapters'
`sniff()` additions)

Final run (every module in the project): **2383 mutants generated, 2343 killed, 39 survived,
1 timeout** (the same `io_utils.x_read_csv_table__mutmut_12` flaky hang/kill documented since
WP-3 — caught either way, not a new finding). Per new/changed module:

- `pipeline.py`: 174 mutants, 5 survived = 97.1%.
- `cli.py`: 83 mutants, 3 survived = 96.4%.
- `render/section1_chart.py`: 250 mutants, 8 survived = 96.8%.
- `render/prepare.py`: 37 mutants, **0 survived = 100%**.
- `adapters/bank_xlsx.py` and `adapters/broker_csv.py`'s new `sniff()` functions added no new
  survivors beyond each module's previously-documented baseline (9 and 6 respectively,
  verified identical by exact mutant ID).

All 39 survivors are non-money-path modules (`pipeline.py`'s threshold is ≥90% per the test
plan; `cli.py` and `render/*` carry no explicit threshold since neither does arithmetic —
`cli.py` only formats already-rounded strings and `render/section1_chart.py` is deliberately
outside `money.py`'s Decimal discipline by design, R-10.4). One genuine test gap was found and
fixed before reaching this final count (see below); every other survivor is a documented
equivalent.

### Real gap found and fixed

`fina.cli.x_main__mutmut_20` mutated the `build` subparser's one-line `help=` text from
`"Run the full pipeline over an input directory."` to `"XXRun the full pipeline over an input
directory.XX"` (mutmut's own string-literal padding marker). `test_build_subcommand_help_text_
is_exact` originally asserted the expected text with Python's `in` (substring) operator, which
cannot tell the original text apart from the same text wrapped in extra characters — the
original string remains a substring of the padded one either way. Fixed by asserting an exact,
stripped-line match against the real `--help` output (`"build     Run the full pipeline over
an input directory."` appears verbatim as one of the output's lines) instead of a substring
check. No source change was needed; the test's assertion style was the gap. This is now killed.

### `pipeline.py` — 5 documented equivalents

| Mutant ID | Diff | Justification |
|---|---|---|
| `x__discover_files__mutmut_2` | `sorted(paths, key=lambda p: p.name)` → `key=None` | **Equivalent.** Every path compared here comes from the same `input_dir.iterdir()` call, so all candidates share an identical parent-directory prefix and differ only in their final path component. `Path.__lt__` compares the paths' own string forms; with an identical shared prefix, lexicographic order of the full path string is provably the same as lexicographic order of the trailing name alone (verified empirically with mixed-case and numeric-prefixed filenames — `sorted(paths, key=None) == sorted(paths, key=lambda p: p.name)` for every case tried). `key=None`'s natural `Path` ordering and `key=lambda p: p.name` are therefore indistinguishable for any set of sibling paths. |
| `x__discover_files__mutmut_4` | drops the `key=` keyword argument entirely | **Equivalent**, same reasoning as above — `sorted(...)`'s own default is `key=None`, so omitting the keyword is identical to passing it explicitly. |
| `x_write_manifest__mutmut_12` | `manifest_path.write_text(..., encoding="utf-8")` → `encoding=None` | **Equivalent in this project's test/deploy environment.** `Path.write_text(text, encoding=None)` resolves via `io.text_encoding`, which returns `"utf-8"` directly whenever the interpreter is running in UTF-8 mode (`sys.flags.utf8_mode`) — true here and on any modern container with no legacy locale installed (`locale -a` in this environment lists only `C`, `C.utf8`, `POSIX`; there is no non-UTF-8 locale to select even by forcing `LANG`/`LC_ALL`, and empirically monkeypatching `locale.getpreferredencoding` does not change the resolved encoding here, confirming UTF-8-mode short-circuits it). No test constructible in this environment can produce a different encoded byte sequence between `encoding="utf-8"` and `encoding=None`. |
| `x_write_manifest__mutmut_14` | drops the `encoding=` keyword entirely | **Equivalent**, same reasoning — the omitted keyword defaults to `None`, identical to the mutant above. |
| `x_write_manifest__mutmut_24` | `encoding="utf-8"` → `encoding="UTF-8"` | **Equivalent** — the same codec-name case-insensitivity already documented for `bank_xlsx.py`/`broker_csv.py` elsewhere in this file: Python's codec lookup normalizes case, so `"utf-8"` and `"UTF-8"` name the identical codec and always produce byte-identical output. |

### `cli.py` — 3 documented equivalents

| Mutant ID | Diff | Justification |
|---|---|---|
| `x__build__mutmut_29` | `(out_dir / "section1_chart.html").write_text(chart_html, encoding="utf-8")` → `encoding=None` | **Equivalent**, identical reasoning to `pipeline.py`'s `write_manifest` survivor above — this environment's Python always runs in UTF-8 mode with no alternate locale available, so `encoding=None` and `encoding="utf-8"` are behaviorally indistinguishable here. |
| `x__build__mutmut_31` | drops the `encoding=` keyword entirely | **Equivalent**, same reasoning — defaults to `None`. |
| `x__build__mutmut_36` | `encoding="utf-8"` → `encoding="UTF-8"` | **Equivalent** — codec-name case-insensitivity, same as documented elsewhere. |

### `render/section1_chart.py` — 8 documented equivalents

| Mutant ID | Diff | Justification |
|---|---|---|
| `x__build_points__mutmut_13` | `pad = (max_value - min_value) * 0.1 or 1.0` → `... or 2.0` | **Equivalent.** This fallback only activates when `max_value == min_value` (every point's value identical — a flat line), at which point `_y_scale`'s `fraction = (value - min_value) / span` reduces to `pad / (2 * pad) = 0.5` regardless of `pad`'s magnitude: every point maps to the exact vertical center of the plot no matter whether the fallback constant is `1.0` or `2.0`. When values are *not* all identical, `(max_value - min_value) * 0.1` is non-zero and the `or` fallback never triggers for either version. No input can make the two constants produce different pixel output. |
| `x__band_polygons__mutmut_4` | `zip(points, points[1:], strict=False)` → `strict=None` | **Equivalent** — the already-documented `zip(..., strict=...)` pattern: `strict` is only ever checked for truthiness at the C level, and `False`/`None` are both falsy, so the two are indistinguishable at runtime for any input. |
| `x__band_polygons__mutmut_7` | drops the `strict=` keyword entirely | **Equivalent**, same reasoning — `zip`'s own default is `strict=False`. |
| `x_render_section1_chart__mutmut_17` | `zip(rows, points, strict=True)` → `strict=None` | **Equivalent.** `points` is built by `_build_points(rows)` as exactly one `_Point` per input row (a 1:1 comprehension over `enumerate(rows)`), so `len(points) == len(rows)` always holds by construction — `strict=True` can never actually raise here, making it behaviorally identical to `strict=None`/`strict=False`/omitted for every possible call. |
| `x_render_section1_chart__mutmut_20` | drops the `strict=` keyword entirely | **Equivalent**, same reasoning as above — lengths are provably always equal. |
| `x_render_section1_chart__mutmut_21` | `strict=True` → `strict=False` | **Equivalent**, same reasoning — lengths are provably always equal, so `strict`'s value is never observable. |
| `x_render_section1_chart__mutmut_28` | `html.escape(tooltip, quote=True)` → drops the `quote=` keyword | **Equivalent** — the already-documented dropped-default-kwarg pattern: `True` is `html.escape`'s own default for `quote`, so omitting it is identical. |
| `x_render_section1_chart__mutmut_33` | `html.escape(row.month_label, quote=True)` → drops the `quote=` keyword | **Equivalent**, same reasoning as the tooltip case immediately above. |

Excluding all 39 documented equivalents (23 pre-existing + 16 new to WP-9: 5 `pipeline.py` + 3
`cli.py` + 8 `render/section1_chart.py`), the real kill rate across the entire project is
2343/2343 = 100%.

### Correction: the two `sniff()` equivalents WP-9's own prose already counted but never listed

WP-9's summary above says the `sniff()` additions "added no new survivors beyond each module's
previously-documented baseline (**9 and 6** respectively)" — but the WP-5/WP-4 tables above list
only **8** and **5** individually-documented mutants for `bank_xlsx.py`/`broker_csv.py`. The
missing one from each module is a real survivor that was always there (verified: both mutant IDs
below appear, unchanged, in this session's from-clean full run) but never got its own table row.
Fixed here, not carried forward as a gap:

| Mutant ID | Diff | Justification |
|---|---|---|
| `fina.adapters.bank_xlsx.x_sniff__mutmut_10` | `_read_header_block_from_sheet(ws, source_file=file_path.name)` → `source_file=None` | **Equivalent.** `sniff()` wraps this call in a bare `try/except Exception: return False`; every field of any exception it might raise (including one carrying `source_file`) is discarded unread, and `source_file` never affects which branch executes or whether an exception is raised at all (grep-verified: every use of `source_file` inside `_find_label_value`/`_parse_header_date`/`_find_movements_header_row`/`_read_header_block_from_sheet` only ever places it into an exception's or a `Warning`'s message/fields, never into a conditional). So `sniff()`'s only observable output (`True`/`False`) is identical for any value of `source_file`, including `None`. |
| `fina.adapters.broker_csv.x_sniff__mutmut_3` | `decode_text_with_fallback(file_path.read_bytes(), source_file=file_path.name)` → `source_file=None` | **Equivalent**, identical reasoning: `sniff()`'s `try/except Exception: return False` discards `source_file`'s only effect (naming the file inside a raised `ParseError`/emitted `Warning`, never read back by `sniff()` itself). |

With these two now listed, `bank_xlsx.py`'s and `broker_csv.py`'s documented-equivalent counts
become 9 and 6 respectively, matching what WP-9's own prose already claimed. No behaviour
changed and no new test was needed — this is a documentation-completeness fix made while
re-running the full suite for the Q-H/Q-G work below, not a new finding about the code.

## Q-H fix (`reconciliation.py`'s `anchor_at`, `section1.py`'s `cash_balance`/`real_net_worth`)

R-9.1 was rewritten so `cash_balance` anchors to `reconciliation.py`'s own R-8.3 baseline
(exposed as a new function, `anchor_at`) instead of summing `cash_effect_eur` from an assumed
zero balance; `real_net_worth` now sums `cash_balance` per `(institution, account)` group
instead of flattening every entry into one raw sum. See R-9.1 in the spec and T-401a/b/c in the
test plan for the bug this fixes.

Full run (every module in the project, from a clean `mutmut run`): **2438 mutants generated,
2396 killed, 41 survived, 1 timeout** (the same recurring `io_utils.x_read_csv_table__mutmut_12`
hang/kill documented since WP-3). `reconciliation.py` alone: **94 mutants, 93 killed, 1 survived
= 98.9%** (threshold 95%; the 1 survivor is the same `x_reconcile__mutmut_18` already documented
under WP-7 — `anchor_at` itself contributed zero new survivors). `section1.py` alone: **205
mutants, 203 killed, 2 survived = 99.0%** (threshold 95%).

An initial pass surfaced 7 new survivors in `section1.py` from the added/changed code (5 in
`cash_balance`, 2 in `real_net_worth`). 5 were genuine test gaps, closed with one new
comprehensive test covering `cash_balance`'s whole anchored branch at once, plus one new test
for `real_net_worth`'s empty-ledger case; 2 were genuine equivalent mutants, documented below
rather than chased with an unkillable test:

| Mutant ID | Diff | Resolution |
|---|---|---|
| `x_cash_balance__mutmut_25` | `anchor_declared_balance + sum(...)` → `... - sum(...)` | Closed by `test_cash_balance_anchored_branch_sums_only_matching_later_entries_inclusive_of_as_of`, whose post-anchor entries have a non-zero net effect (`-30.00 + 10.00`), so `+`/`-` disagree (`80.00` vs `120.00`). |
| `x_cash_balance__mutmut_33` | `e.institution == institution` → `!=` in the post-anchor filter | Closed by the same test: an `other_institution` entry with a huge `cash_effect_eur` (`888888.00`) would leak in under the flipped filter. |
| `x_cash_balance__mutmut_34` | `e.account == account` → `!=` in the post-anchor filter | Closed by the same test: an `other_account` entry (`999999.00`) would leak in under the flipped filter. |
| `x_cash_balance__mutmut_35` | `e.date <= as_of` → `< as_of` in the post-anchor filter | Closed by the same test: `on_as_of_boundary` is dated exactly on `as_of` and must still be included (`<=`, not `<`). |
| `x_real_net_worth__mutmut_8` | drops `start=Decimal("0")` on the outer `sum()` over per-account `cash_balance` results | Closed by `test_real_net_worth_of_an_empty_ledger_is_decimal_zero_not_int`: with zero `(institution, account)` groups (an empty `entries`), `sum()` without `start` returns the bare `int` `0`, which `isinstance(result, Decimal)` catches (a single-group case, used elsewhere, cannot distinguish this: `int 0 + Decimal(...)` is already `Decimal`, so the type bug only shows with *zero* groups). |

| Mutant ID | Diff | Justification |
|---|---|---|
| `x_cash_balance__mutmut_29` | drops `start=Decimal("0")` on the post-anchor `sum()` that is then *added to* `anchor_declared_balance` | **Equivalent, unlike the otherwise-identical-looking `x_real_net_worth__mutmut_8` above.** This `sum()`'s result is never returned on its own -- it is always added to `anchor_declared_balance`, which is already a `Decimal`. `Decimal.__add__` accepts a bare `int` operand directly (`Decimal("100.00") + 0 == Decimal("100.00")`, and the result's type is `Decimal`, not `int`) for every Python version this project targets. So whether the inner `sum()` starts from `Decimal("0")` or bare `int` `0`, `anchor_declared_balance + sum(...)` is byte-identical in both value and type for every possible input, including an empty generator. (`x_real_net_worth__mutmut_8` differs because *that* `sum()`'s result **is** the function's own return value with nothing added to it, so an empty generator's bare `int 0` propagates all the way out uncoerced.) |
| `x_real_net_worth__mutmut_4` | `accounts.setdefault((e.institution, e.account), )` — drops the explicit `None` default | **Equivalent.** `dict.setdefault(key)` called with no default argument at all already defaults to `None` — the same value the dropped argument supplied explicitly. Same reasoning as every other dropped-explicit-default survivor already documented in this file (e.g. `Warning(source_row=None)`). |

Excluding the 3 documented equivalents new to this fix (2 in `section1.py` + the reconciliation
survivor unchanged from WP-7) and the 2 `sniff()` equivalents corrected above, plus every
already-documented equivalent from every prior section, the real kill rate across the entire
project is 2396/2396 = 100%.

## Q-G port (`render/section1_chart.py` re-derived from the approved mock)

WP-9's independently-designed chart (built because the approved mock was unreachable at the
time -- Q-G) was re-derived as a genuine port of `docs/design/section1-approved-mock.html`:
new colours/typography/spacing, a single shared `.hit-area` (replacing one `circle.hit` per
point), and a structured tooltip body (replacing a single `data-tooltip` text payload). The
module grew (103 → 146 statements) as a result.

Full run (every module in the project): **2618 mutants generated, 2576 killed, 42 survived, 0
timeout**. `render/section1_chart.py` alone: **430 mutants, 421 killed, 9 survived = 97.9%**
(no explicit threshold for this module, per the test plan -- see WP-9's own note above; still
run and documented to the same rigor as every threshold-bound module).

An initial pass surfaced 64 survivors from the port. 55 were genuine test gaps -- dropped or
wrong JSON keys/values in the new `_points_payload` helper, wrong pixel offsets in the new
`_grid_line_svg`/`_x_label_svg`/end-label positioning, a dropped tooltip-rows join, an exact-
text gap in the new close-button/tooltip markup, an off-by-one in the "always show the last
month" index check, a wrong `_grid_ticks` span operator (`+` vs `-`, indistinguishable when
`min_value == 0`, the same class of bug R-9.1's own Q-H fix was about), and a `_thousands_label`
divisor test too coarse to notice `/1000` vs `/1001` -- all closed with new or strengthened
tests in `test_section1_chart.py` (see that file's own docstrings for each). The remaining 9
are genuine equivalents:

| Mutant ID | Diff | Justification |
|---|---|---|
| `x__padded_range__mutmut_10` | `pad = (max - min) * 0.1 or 1.0` → `... or 2.0` | **Equivalent**, same reasoning as `x__build_points__mutmut_13` documented under WP-9 above (this function was extracted from `_build_points`'s own identical line): the fallback only activates when `max_value == min_value`, at which point `_y_scale`'s `fraction = (value - min_value) / span` reduces to `pad / (2 * pad) = 0.5` regardless of `pad`'s magnitude. |
| `x__band_polygons__mutmut_4` | `zip(points, points[1:], strict=False)` → `strict=None` | **Equivalent** — the already-documented `zip(..., strict=...)` pattern from WP-9: `strict` is only checked for truthiness, and `False`/`None` are both falsy. |
| `x__band_polygons__mutmut_7` | drops the `strict=` keyword entirely | **Equivalent**, same reasoning — `zip`'s own default is `strict=False`. |
| `x__points_payload__mutmut_32` | `zip(rows, points, strict=True)` → `strict=None` | **Equivalent.** `points` is built by `_build_points(rows)` as exactly one `_Point` per input row (a 1:1 comprehension over `enumerate(rows)`), so `len(points) == len(rows)` always holds by construction — `strict=True` can never actually raise here. |
| `x__points_payload__mutmut_35` | drops the `strict=` keyword entirely | **Equivalent**, same reasoning — lengths are provably always equal. |
| `x__points_payload__mutmut_36` | `strict=True` → `strict=False` | **Equivalent**, same reasoning. |
| `x_render_section1_chart__mutmut_39` | `zip(rows, points, strict=True)` → `strict=None` (for `row_points`, feeding the x-axis labels) | **Equivalent**, identical reasoning — `points` is `_build_points(rows)`, always the same length as `rows`. |
| `x_render_section1_chart__mutmut_42` | drops the `strict=` keyword entirely | **Equivalent**, same reasoning. |
| `x_render_section1_chart__mutmut_43` | `strict=True` → `strict=False` | **Equivalent**, same reasoning. |

Excluding these 9 documented equivalents, `render/section1_chart.py`'s real kill rate is
421/421 = 100%. This full-project run's 42 survivors, by module (matching every count already
documented above, section by section: `bank_xlsx.py` 9, `broker_csv.py` 6, `io_utils.py` 4,
`models.py` 2, `money.py` 1, `pipeline.py` 5, `cli.py` 3, `reconciliation.py` 1, `section1.py`
2, `render/section1_chart.py` 9 — the WP-9 original render module's own 8 equivalents no
longer apply, superseded by this port's 9), sum to exactly the 42 this run reports as
`survived`. Excluding all of them, the real kill rate across the entire project is
2576/2576 = 100%.

`section1.py` and `reconciliation.py`'s own survivors (2 and 1 respectively, both from the
Q-H fix above) are unchanged by this port, confirming the chart re-derivation touched no
money-path module.

## Q-J fix (`render/section1_chart.py` — removed the `cash_only` suffix, widened `_PAD_RIGHT`
and the tooltip's `max-width`/`tipWidth`, added tap-highlight/touch-action CSS)

Removing the two `if row.completeness == "cash_only":` branches (Q-E's resolution) removed
exactly 9 mutants along with them — the branch conditions and their string-literal siblings
that mutmut had generated for that now-deleted code, all of which were previously killed, not
survivors. The widened `_PAD_RIGHT`/tooltip `max-width`/`tipWidth` numeric constants and the
new `-webkit-tap-highlight-color`/`touch-action` CSS declarations (plain string literals inside
the template, not executable branches) added no new mutable Python logic.

Full run (every module in the project): **2609 mutants generated, 2567 killed, 42 survived, 0
timeout** (down from 2618/2576/42/0 before this fix — 9 fewer mutants generated, 9 fewer
killed, the same 42 survived). `render/section1_chart.py` alone: **421 mutants, 412 killed, 9
survived = 97.9%** (down from 430/421/9 — same 97.9%, same 9 survivors, same mutant IDs as
documented in the Q-G port section above: `x__padded_range__mutmut_10`,
`x__band_polygons__mutmut_4`/`_7`, `x__points_payload__mutmut_32`/`_35`/`_36`,
`x_render_section1_chart__mutmut_39`/`_42`/`_43`). Verified identical, not merely
re-documented: every survivor's mutant ID, function, and justification carries over unchanged
from the Q-G port section — this fix touched no code path any of those 9 mutants exercise.

Excluding the 9 documented equivalents, `render/section1_chart.py`'s real kill rate is
412/412 = 100%. Across the entire project, excluding all 42 documented equivalents, the real
kill rate is 2567/2567 = 100%.

## WP-10 (`pipeline.py` — added `sniff_adapter_name`)

The new function generated exactly **1** mutant (`x_sniff_adapter_name__mutmut_1`), killed by
the new differential tests in `tests/test_pipeline.py` (agreement with `_select_adapter` on
both a recognized and an unrecognized file). No new survivor.

Full run: **2610 mutants generated, 2568 killed, 42 survived, 0 timeout** (up from
2609/2567/42/0 before this change — 1 more mutant generated, 1 more killed, the same 42
survived, same mutant IDs). `pipeline.py` alone: **175 mutants, 170 killed, 5 survived =
97.1%** (up from 174/169/5 — same 5 survivors as documented above, unchanged). Threshold for
`pipeline.py` is ≥90% per the test plan; met.

Excluding the 42 documented equivalents, the real kill rate across the entire project is
2568/2568 = 100%.

## Bond redemption fix (`models.py` — added `MovementType.REDEMPTION`; `broker_csv.py` —
maps `("CORPORATE_ACTION", "FULL_CALL")`/`("CASH", "FINAL_MATURITY")` to it, allows `amount`
and `currency` to be absent on `CORPORATE_ACTION` rows)

Found on the project owner's own real Trade Republic export (WP-18 real-device testing): a
bond's early redemption arrives as two separate rows the adapter didn't recognize at all
(`UnknownMovementError`). See R-2.5a/R-6.4/R-6.5/R-6.6a in `docs/spec/section1-ingestion-spec.md`
for the full rule text and rationale — including why the fix is a new `MovementType.REDEMPTION`,
not a reuse of `SELL` as first attempted: `LedgerEntry`'s own R-2.11 invariant requires a `SELL`
row to carry both `amount_eur > 0` and `quantity < 0` on the *same* row, which this two-row
source shape cannot satisfy for either leg — caught by this project's own test suite before
ever shipping, when the first (SELL-based) attempt failed `test_bond_full_call_redemption_pair_end_to_end`
on `LedgerEntry.__post_init__`'s invariant check.

`MovementType.REDEMPTION` added no new mutant to the enum declaration itself (a `str` value,
not executable logic); `compute_cash_effect`'s existing final `return amount_eur` branch
(R-2.6's "otherwise" case) already covers it with zero code changes there — confirmed by
`test_t058_cash_effect_exhaustive_over_all_movement_types[MovementType.REDEMPTION]` passing
immediately. `broker_csv.py`'s two new `_MOVEMENT_MAP` entries and the `amount`/`currency`
absent-on-`CORPORATE_ACTION` carve-outs generated new mutants in `_parse_row`, all killed by
the new tests in `tests/test_broker_csv.py` (`test_bond_full_call_redemption_pair_end_to_end`,
`test_corporate_action_row_with_amount_present_still_parses`,
`test_corporate_action_row_with_blank_currency_does_not_raise`,
`test_corporate_action_row_with_wrong_currency_still_raises`) except one, already documented
above under WP-4 — its ID shifted from `x__parse_row__mutmut_73` to `x__parse_row__mutmut_81`
purely because of added code earlier in the same function (confirmed via `mutmut show`: byte-
identical diff, same `is_migration = ... and ... ` → `... or ...` mutation, same equivalence
reasoning, since `"MIGRATION"` still appears in `_MOVEMENT_MAP` exactly once, paired only with
`"DELIVERY"` — the two new map entries added neither `"DELIVERY"` nor `"MIGRATION"` as a value).

**A second, real gap found and fixed in the same round**: `pyproject.toml`'s
`pytest_add_cli_args_test_selection` only excluded `test_pwa_shell.py` from mutmut's own test
runner (added at WP-13) — every later `test_pwa_*.py`/`test_pyodide_*.py` file (WP-14 onward)
was never added, so this session's first mutmut attempt failed outright (`test_pwa_backup.py`'s
`shell_server` fixture asserting `(WEB_DIR / "index.html").is_file()`, which is never true
inside mutmut's own `mutants/` tree — it mirrors only `src/fina`, never `web/`). Fixed by adding
every remaining browser-dependent test file to that ignore list (none of them exercise unique
`src/fina` lines beyond what the fast non-browser suites already cover, the same rationale
already documented for `test_render_browser.py`/`test_pwa_shell.py`).

Full run (with the corrected test selection): **2623 mutants generated, 2580 killed, 42
survived, 1 timeout** (up from 2610/2568/42/0 — 13 more mutants generated: `models.py`
46→46 unchanged since `REDEMPTION` added no executable logic, `broker_csv.py` 609 total now;
the +1 timeout is `fina.io_utils.x_read_csv_table__mutmut_12`, an environment-load artifact of
this particular run, not a new code path — `io_utils.py` is not one of G-3's threshold-gated
modules). Per-module kill rates for every G-3-gated module, computed from this run:

| Module | Threshold | Total | Survived | Kill rate |
|---|---|---|---|---|
| `money.py` | ≥95% | 118 | 1 | 99.15% |
| `models.py` | ≥95% | 46 | 2 | 95.65% |
| `reconciliation.py` | ≥95% | 94 | 1 | 98.94% |
| `section1.py` | ≥95% | 205 | 2 | 99.02% |
| `adapters/bank_xlsx.py` | ≥90% | 645 | 9 | 98.60% |
| `adapters/broker_csv.py` | ≥90% | 609 | 6 | 99.01% |
| `classification.py` | ≥90% | 111 | 0 | 100% |
| `pipeline.py` | ≥90% | 175 | 5 | 97.14% |

All thresholds met. Every survivor in this run matches an ID already documented somewhere
above (accounting for the one `_73`→`_81` renumbering explained above) — no new,
undocumented survivor exists. Excluding all 42 documented equivalents (and the 1 timeout,
which is not a real survivor), the real kill rate across the entire project is
2580/2580 = 100%.
