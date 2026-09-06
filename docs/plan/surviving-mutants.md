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
