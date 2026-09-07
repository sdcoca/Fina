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
