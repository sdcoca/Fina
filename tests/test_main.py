"""Tests for fina.__main__: `python -m fina` really runs the CLI (R-11.1's "one command")."""

from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from builders import BANK_XLSX, BROKER_CSV


def _copy_both_fixtures(tmp_path: Path) -> Path:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    shutil.copyfile(BANK_XLSX, input_dir / "banco_ejemplo.xlsx")
    shutil.copyfile(BROKER_CSV, input_dir / "broker_ejemplo.csv")
    return input_dir


def test_dunder_main_invokes_cli_main_and_exits_with_its_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runs `fina/__main__.py`'s own module-level code in-process (so coverage actually sees
    it exercised, unlike a subprocess) via the same mechanism `python -m fina` uses.
    """
    input_dir = _copy_both_fixtures(tmp_path)
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        sys, "argv", ["fina", "build", "--input", str(input_dir), "--out", str(out_dir)]
    )
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("fina", run_name="__main__")
    assert exc_info.value.code == 0
    assert (out_dir / "manifest.json").exists()


def test_dunder_main_propagates_a_nonzero_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_input = tmp_path / "empty_input"
    empty_input.mkdir()
    (empty_input / "garbage.txt").write_text("not a known export shape\n")
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        sys, "argv", ["fina", "build", "--input", str(empty_input), "--out", str(out_dir)]
    )
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("fina", run_name="__main__")
    assert exc_info.value.code != 0
    assert not out_dir.exists()


def test_importing_dunder_main_normally_does_not_invoke_the_cli() -> None:
    """A plain `import fina.__main__` (as opposed to running it as `__main__`, above) must
    not call `main()` at all -- the `if __name__ == "__main__":` guard's other branch.
    """
    module = runpy.run_module("fina.__main__", run_name="fina.__main__")
    assert "main" in module  # the imported name is present...
    # ...but sys.exit was never called, since we got here without a SystemExit at all.


def test_python_dash_m_fina_build_works_as_a_real_subprocess_command(tmp_path: Path) -> None:
    """A genuine end-to-end smoke test of the actual `python -m fina` invocation documented
    in implementation-plan.md §3 -- separate from the in-process test above, which exists so
    this module's own lines register as covered.
    """
    input_dir = _copy_both_fixtures(tmp_path)
    out_dir = tmp_path / "out"
    src_root = Path(__file__).resolve().parent.parent / "src"

    result = subprocess.run(
        [sys.executable, "-m", "fina", "build", "--input", str(input_dir), "--out", str(out_dir)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(src_root)},
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert (out_dir / "manifest.json").exists()
