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
