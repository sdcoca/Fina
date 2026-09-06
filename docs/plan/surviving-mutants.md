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
