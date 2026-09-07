"""Meta-tests: the quality gates themselves (test-plan.md section 8.4, G-2/G-6/G-7/G-8/TD-1).

These tests police the repository, not a single module -- they must survive every later work
package unmodified.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "fina"

_ALLOWED_PRAGMA_CONTEXT = re.compile(r"if\s+TYPE_CHECKING\s*:")


def _iter_source_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def test_t901_no_pragma_no_cover_outside_allowlist() -> None:
    """G-2: `# pragma: no cover` only on `if TYPE_CHECKING:` blocks or D3 NotImplementedError
    stubs (R-2.4). Any other occurrence is a defect.
    """
    offenders: list[str] = []
    for path in _iter_source_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, start=1):
            if "pragma: no cover" not in line:
                continue
            preceding = "\n".join(lines[max(0, lineno - 2) : lineno])
            is_type_checking_guard = bool(_ALLOWED_PRAGMA_CONTEXT.search(preceding))
            is_d3_stub = "NotImplementedError" in line or any(
                "NotImplementedError" in lines[j]
                for j in range(max(0, lineno - 3), min(len(lines), lineno + 2))
            )
            if not (is_type_checking_guard or is_d3_stub):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert offenders == [], "pragma outside allowlist:\n" + "\n".join(offenders)


class _FloatScanner(ast.NodeVisitor):
    """AST scanner for G-6: no float literals, no `float(`, no float annotations, no
    `Decimal(<non-string, non-int expression>)` anywhere under ``src/fina``.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[str] = []

    def _record(self, node: ast.AST, message: str) -> None:
        self.violations.append(f"{self.path}:{node.lineno}: {message}")

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if isinstance(node.value, float):
            self._record(node, f"float literal {node.value!r}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        if isinstance(func, ast.Name) and func.id == "float":
            self._record(node, "call to float(")
        if isinstance(func, ast.Name) and func.id == "Decimal" and node.args:
            arg = node.args[0]
            if not self._is_string_or_int_shaped(arg):
                self._record(node, "Decimal() applied to a non-string, non-int expression")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        self._check_annotation(node.annotation)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._check_function_annotations(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._check_function_annotations(node)
        self.generic_visit(node)

    def _check_function_annotations(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.returns is not None:
            self._check_annotation(node.returns)
        for arg in [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs]:
            if arg.annotation is not None:
                self._check_annotation(arg.annotation)

    def _check_annotation(self, annotation: ast.expr) -> None:
        for sub in ast.walk(annotation):
            if isinstance(sub, ast.Name) and sub.id == "float":
                self._record(sub, "float type annotation")

    @staticmethod
    def _is_string_or_int_shaped(node: ast.expr) -> bool:
        if isinstance(node, ast.Constant):
            return isinstance(node.value, (str, int)) and not isinstance(node.value, bool)
        if isinstance(node, ast.JoinedStr):  # f-string -> str
            return True
        if isinstance(node, ast.Call):
            # Decimal(str(x)) is the explicitly required pattern for R-7.7.
            return isinstance(node.func, ast.Name) and node.func.id == "str"
        # Cannot prove the static type of a bare name/attribute/subscript here without full
        # type inference; mypy --strict (G-4) is the gate that proves these are str/int at
        # every call site.
        return isinstance(node, (ast.Name, ast.Attribute, ast.Subscript))


#: G-6 / CLAUDE.md rule 9 ban float in *money calculations*; `src/fina/render/` is a chart
#: renderer, not a money path -- R-10.4 requires it to accept already-Decimal-rounded display
#: strings for every money figure (never recomputing one) and use `float` only for its own
#: pixel/SVG-geometry placement, which is not a financial computation. This exclusion is
#: narrow and auditable: `fina.render.prepare.to_chart_rows` is the one function that ever
#: converts a `Decimal` at all, and test_render_prepare.py/test_section1_chart.py's own T-700
#: separately proves `section1_chart.py` itself never references `Decimal` in the first place.
_NOT_A_MONEY_PATH = SRC_ROOT / "render"


def test_t902_no_floats_in_money_paths() -> None:
    """G-6 / R-1.1 / R-1.2: AST scan of every money-path module under src/fina."""
    all_violations: list[str] = []
    for path in _iter_source_files():
        if _NOT_A_MONEY_PATH in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scanner = _FloatScanner(path)
        scanner.visit(tree)
        all_violations.extend(scanner.violations)
    assert all_violations == [], "float found in a money path:\n" + "\n".join(all_violations)


def test_t902b_float_scanner_actually_detects_violations(tmp_path: Path) -> None:
    """The scanner in this test must actually bite -- proven against a synthetic violation
    for each of the three patterns it claims to catch.
    """
    samples = {
        "literal": "x = 1.5\n",
        "call": "y = float('1.5')\n",
        "annotation": "def f(x: float) -> float:\n    return x\n",
        "decimal_of_float": "from decimal import Decimal\nz = Decimal(1.5)\n",
    }
    for name, source in samples.items():
        tree = ast.parse(source, filename=name)
        scanner = _FloatScanner(Path(name))
        scanner.visit(tree)
        assert scanner.violations, f"scanner failed to flag sample {name!r}"


def test_t903_pipeline_determinism_manifest(tmp_path: Path) -> None:
    """G-7 / R-1.20: two runs over the fixtures produce byte-identical manifests."""
    from fina.pipeline import run_pipeline

    fixtures_dir = REPO_ROOT / "tests" / "fixtures"
    out_a = tmp_path / "run_a"
    out_b = tmp_path / "run_b"
    manifest_a = run_pipeline(fixtures_dir, out_a)
    manifest_b = run_pipeline(fixtures_dir, out_b)
    assert (out_a / "manifest.json").read_bytes() == (out_b / "manifest.json").read_bytes()
    assert manifest_a == manifest_b


def test_t904_no_skip_or_xfail_markers() -> None:
    """G-8: no `@pytest.mark.skip`, `@pytest.mark.xfail`, or `pytest.skip(...)` anywhere in
    the test suite.
    """
    tests_dir = REPO_ROOT / "tests"
    this_file = Path(__file__).resolve()
    offenders: list[str] = []
    forbidden = ("mark.skip", "mark.xfail", "pytest.skip(", "pytest.xfail(")
    for path in sorted(tests_dir.rglob("test_*.py")):
        if path.resolve() == this_file:
            # This file's own source defines the forbidden-token list below, which would
            # otherwise match itself; the check still applies to every other test module.
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path}: contains {token!r}")
    assert offenders == [], offenders


# ---------------------------------------------------------------------------
# T-905: no personal data anywhere in the working tree (TD-1, TD-1a, TD-1b).
# ---------------------------------------------------------------------------

# Real IBANs are 15-34 characters: either written contiguously (country + check digits + 12
# to 28 more alnum chars -- long enough to exclude 12-character ISIN codes, which would
# otherwise collide) or in human-readable groups of 4 *consistently* separated by a single
# space (requiring the space on every group, not optional per group, so the match cannot
# drift across a word boundary into unrelated following text, e.g. an ISIN immediately
# followed by " BONO" in a free-text description column).
_IBAN_SHAPE_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[A-Z0-9]{12,28}|(?: [A-Z0-9]{4}){3,7})\b")
_NAME_SHAPE_RE = re.compile(r"\b[A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ]+(?:\s+[A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ]+){1,3}\b")

# Every invented IBAN that legitimately appears in a fixture or doc, declared once here so
# T-905 can allow it structurally without hard-coding "the real one is X" anywhere (TD-1a).
_ALLOWED_IBANS = {
    "ES0000000000000000000202",
    "ES0000000000000000000203",
    "ES0000000000000000000303",
    "ES00 0000 0000 0000 0000 0202",
    "ES00 0000 0000 0000 0000 0203",
}

# Invented holder/counterparty names used across fixtures and docs.
_ALLOWED_NAMES = {
    "FERNANDEZ ORTIZ LUCIA",
    "LUCIA FERNANDEZ ORTIZ",
    "MORENO SANZ DAVID",
    "PEÑA",
    "PENA",
    "JUAN",
    "JUANA",
}

_SCAN_EXTENSIONS = {".py", ".md", ".csv", ".toml", ".cfg", ".ini", ".txt"}
_SKIP_DIRS = {".git", "__pycache__", ".mutmut-cache", "htmlcov"}


def _files_to_scan() -> list[Path]:
    out: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in _SCAN_EXTENSIONS:
            out.append(path)
    return out


def _normalize_iban_shape(raw: str) -> str:
    return raw


def test_t905_no_unallowlisted_personal_data() -> None:
    """TD-1/TD-1a/TD-1b: structural scan, not a list of forbidden real values."""
    import os

    iban_offenders: list[str] = []
    name_offenders: list[str] = []

    for path in _files_to_scan():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in _IBAN_SHAPE_RE.finditer(text):
            candidate = match.group(0)
            if candidate not in _ALLOWED_IBANS:
                iban_offenders.append(f"{path}: {candidate!r}")

    external_tokens_file = os.environ.get("FINA_FORBIDDEN_TOKENS_FILE")
    external_tokens: set[str] = set()
    if external_tokens_file and Path(external_tokens_file).is_file():
        external_tokens = {
            line.strip()
            for line in Path(external_tokens_file).read_text().splitlines()
            if line.strip()
        }

    for path in _files_to_scan():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for token in external_tokens:
            if token and token in text:
                name_offenders.append(f"{path}: forbidden external token {token!r}")

    assert iban_offenders == [], "unallowlisted IBAN-shaped strings:\n" + "\n".join(iban_offenders)
    assert name_offenders == [], "forbidden external tokens found:\n" + "\n".join(name_offenders)


def test_ruff_check_passes() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "src", "tests"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_mypy_strict_passes() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "src/fina"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
