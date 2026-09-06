# Implementation plan

How this slice gets built, by whom, and how each unit of work proves itself finished.

- **Normative source of behaviour**: `docs/spec/section1-ingestion-spec.md` (rules `R-n.m`).
- **Normative source of testing**: `docs/plan/test-plan.md` (tests `T-xxx`, gates `G-n`).
- This document adds only: sequencing, work-package boundaries, and verification protocol.

**Division of labour.** Work packages WP-1..WP-9 are implemented by capable-but-narrower
models working one package at a time. WP-0 and the final verification (§4) are done by the
reviewing model. Each package is written so that it can be executed with **no context beyond
the three documents above and the repository** — no memory of any prior conversation.

---

## 1. Rules for every implementer (read before touching code)

**I-1 Scope.** Implement exactly the rules listed in your work package. Do not implement a
neighbouring package's rules "while you are there". Unlisted improvements are out of scope
even when obviously good.

**I-2 Ambiguity.** If the spec does not determine what to do, **stop**. Append an entry to
`docs/plan/open-questions.md` (context, ambiguity, options, your recommendation) and stop
work on that path. Do not guess. A correct guess is still a defect, because the next
implementer will not know it was a guess. (Spec R-0.3.)

**I-3 Never move the target.** The oracle numbers in spec §12 and the assertions in the test
plan are the specification. If your code disagrees with them, your code is wrong until
proven otherwise. Editing an expected value, deleting an assertion, adding `skip`/`xfail`,
or loosening a comparison to make a test pass is the most serious defect this project
recognizes. If you believe an oracle is genuinely wrong, stop and file it per I-2.

**I-4 No coverage theatre.** Do not add tests that merely execute lines. Every test asserts
an outcome that would differ if the behaviour were wrong. Mutation score (G-3) is what this
is measured by, not line count.

**I-5 Decimal discipline.** `float` may not appear in your diff under `src/fina/`, in any
form, including type annotations and test helpers that feed money values (R-1.1, R-1.2, G-6).

**I-6 Errors are features.** Every `raise` in the spec is a required behaviour with a
required test. Swallowing an exception, or replacing it with a default value, is a defect
(R-5.3, R-11.4, CLAUDE.md rule 15).

**I-7 Determinism.** No wall clock, no randomness, no network, no environment lookups, no
iteration over unordered collections that reaches output (R-1.20, R-5.4).

**I-8 Language.** Code, comments, docstrings, test names, documentation: English. Fixture
content in Spanish stays Spanish (test-plan TD-4).

**I-9 Commit protocol.** One commit per work package, message naming the WP id and the rule
ids implemented. The commit must leave `main` green: all gates in §3 pass at that commit.

**I-10 Definition of done is §3.** A package is done when §3's checklist passes *in full*,
not when the feature "works".

---

## 2. Work packages

Dependencies are strict: do not start a package whose dependencies are not merged and green.

### WP-0 — Scaffolding and gates *(reviewing model)*
Creates: `pyproject.toml` (package `fina`, `src/` layout, deps: `openpyxl`, `hypothesis`,
`pytest`, `pytest-cov`, `mutmut`, `mypy`, `ruff`; **no pandas** — decision 5.4), tool
configuration for all gates, `tests/builders.py` skeleton, CI workflow running §3, and the
meta-tests T-901..T-905.
Rules: G-1..G-8, TD-1..TD-4.
Done when: the gate suite runs and fails loudly on a deliberately introduced float, pragma,
skip marker and non-determinism (each verified once, then reverted).

### WP-1 — `money.py`: parsing, normalization, rounding
Depends on: WP-0.
Implements: R-1.2, R-1.5, R-1.11, R-1.12, R-1.13, R-1.14, R-7.3, R-7.6.
Tests: T-001..T-028, T-600, T-605.
Notes: the `Ñ` rule (R-1.13) is deliberate and load-bearing — do not "simplify" it to a
blanket NFKD strip.

### WP-2 — `models.py`: entities, movement taxonomy, cash effect, validation
Depends on: WP-1.
Implements: R-1.21, R-1.23, R-2.1..R-2.17.
Tests: T-050..T-071.
Notes: `cash_effect` must be exhaustive over `MovementType` such that adding a member
without handling it fails a test (T-058), not at runtime in production.

### WP-3 — `io_utils.py`: file reading, encodings, row indexing
Depends on: WP-0.
Implements: R-1.15..R-1.19, R-2.15 (content hashing).
Tests: T-118..T-128 (the file-level subset), T-503.

### WP-4 — `adapters/broker_csv.py`
Depends on: WP-1, WP-2, WP-3.
Implements: R-5.1..R-5.4, R-6.1..R-6.17.
Tests: T-100..T-137.
Notes: R-6.2a (dividend `shares` must not become `quantity`) is the highest-risk rule in this
package; T-104 exists specifically to catch it.

### WP-5 — `adapters/bank_xlsx.py`
Depends on: WP-1, WP-2, WP-3.
Implements: R-7.1..R-7.16.
Tests: T-200..T-223, T-001..T-012 reused via the shared parser.
Notes: handle both string and numeric cells (R-7.7) — the warning it emits is required
output, not optional noise.

### WP-6 — `classification.py`: owned accounts and internal/external
Depends on: WP-4, WP-5.
Implements: R-3.1..R-3.7.
Tests: T-300..T-311.
Notes: R-3.6's warning is the project's only defence against the retroactive-restatement
risk described there; it is not cosmetic.

### WP-7 — `reconciliation.py`
Depends on: WP-6.
Implements: R-8.1..R-8.5.
Tests: T-350..T-357.

### WP-8 — `section1.py`: the net-worth bridge
Depends on: WP-7.
Implements: R-9.1..R-9.11.
Tests: T-400..T-417, T-602, T-603, T-604.
Notes: `real_net_worth` is cash-only until D1; the `completeness` flag is mandatory output
(R-9.4), and no caller may drop it.

### WP-9 — `render/section1_chart.py` + `pipeline.py` + CLI
Depends on: WP-8.
Implements: R-10.1..R-10.5, R-11.1..R-11.5.
Tests: T-500..T-508, T-700..T-709.
Notes: the chart's visual contract (R-10.2) is fixed by the approved mock; port it, do not
redesign it. The renderer performs no arithmetic on money (R-10.4). **Verify at a 390px
viewport first, desktop second** (R-10.2a, CLAUDE.md rule 19) — render a screenshot with a
real headless browser and look at it; do not infer how it looks from the CSS. Do not reach
for `backdrop-filter` for any translucency effect (R-10.2b, CLAUDE.md rule 20) — plain alpha
only. This was a real defect in the approved mock (fixed before WP-9 started): a
fixed-width tooltip and `backdrop-filter` blur both looked fine on desktop and were unusable
on an actual phone.

---

## 3. Self-verification protocol (run before declaring any package done)

Run in this order. **Every command must pass. A package with any red step is not done, and
must not be reported as done.**

```bash
# 1. Types
mypy --strict src/fina

# 2. Lint and format
ruff check src tests
ruff format --check src tests

# 3. Full suite with 100% line+branch coverage
pytest --cov=src/fina --cov-branch --cov-fail-under=100 -q

# 4. Mutation testing (thresholds per G-3)
mutmut run --paths-to-mutate src/fina
mutmut results

# 5. Determinism: two full runs must be byte-identical
python -m fina build --input tests/fixtures --out /tmp/run_a
python -m fina build --input tests/fixtures --out /tmp/run_b
diff -r /tmp/run_a /tmp/run_b
```

Then answer these in the completion report, in writing, per package:

1. Which rule IDs did this package implement? List them.
2. Which test IDs cover each of those rules? Confirm every one appears in the traceability
   matrix (test-plan §9) and that the matrix was updated if needed.
3. Coverage: what is the line and branch percentage? If not 100 %, the package is not done.
4. Mutation: what is the kill rate per file, and is every surviving mutant listed with a
   justification in `docs/plan/surviving-mutants.md`?
5. Did you add any `# pragma: no cover`, `skip`, `xfail`, or loosen any assertion? (Correct
   answer: no. Any yes must be justified to the reviewer before the package is submitted.)
6. Did you change any expected value in the spec's §12 oracle or in an existing test? (See
   I-3. Any yes requires the recomputation shown in the same commit and explicit reviewer
   sign-off.)
7. What did you *not* implement that a reader might expect, and why (deferred rule, other
   package, open question)?
8. Which entries, if any, did you add to `docs/plan/open-questions.md`?

A package report that cannot answer 1-4 concretely is not a completion report and the
package stays open.

---

## 4. Final verification protocol *(reviewing model — not delegable)*

Performed once all packages are merged, and again after any later change to a money path.
The reviewer does not trust the completion reports; the reviewer reproduces.

1. **Re-derive the oracle independently.** Recompute the spec §12 figures from the fixture
   files by hand, without running the implementation, and compare to §12 as written. Any
   disagreement means either the spec or the fixture is wrong — resolve before looking at
   code.
2. **Run every gate** in §3 from a clean checkout, including mutation testing. Confirm the
   numbers in the completion reports match what the commands actually print.
3. **Traceability sweep.** For every rule ID in the spec, confirm the matrix names a test,
   and open at least one test per section to confirm it asserts the rule rather than merely
   touching the code path.
4. **Error-path sweep.** For every `raise` in `src/fina/`, confirm a test triggers it and
   asserts the structured fields (R-1.23).
5. **Float sweep.** Independently grep for `float`, `round(`, `%` formatting of money, and
   confirm G-6's AST check would catch each pattern found.
6. **Determinism and idempotency.** Two clean runs, byte-identical manifests; then re-run
   with input files renamed (content identical) and confirm only the recorded file names
   differ.
7. **Adversarial read of the highest-risk rules.** At minimum R-6.2a (phantom dividend
   shares), R-2.6/R-2.7 (fee additive, tax not), R-1.9 (booking date beats UTC timestamp),
   R-3.4/R-3.6 (classification and its stability warning), R-8.2 (zero tolerance). For each,
   construct a hostile input by hand and confirm the system behaves as specified.
8. **Honesty sweep.** Confirm every cash-only figure carries its qualifier (R-9.4/R-10.5),
   every unverified balance carries its warning (R-8.4), and no deferred item (D1..D5) is
   silently presented as done.
9. **Open questions.** Confirm every entry in `docs/plan/open-questions.md` is either
   answered by the user or explicitly still open in the report to the user — none quietly
   resolved by an implementer.

Only after all nine steps is the slice reportable as complete, and the report must state
what remains deferred (D1..D5) and what confidence each figure carries.
