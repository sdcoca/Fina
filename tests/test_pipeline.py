"""Tests for fina.pipeline (WP-9): R-11.1, R-11.2, R-11.5."""

from __future__ import annotations

import json
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from builders import BANK_XLSX, BROKER_CSV
from fina.errors import DuplicateSourceError, ParseError, ReconciliationError
from fina.io_utils import sha256_of_file
from fina.pipeline import PipelineResult, manifest_dict, run_pipeline
from fina.section1 import Section1Period


def _copy_both_fixtures(tmp_path: Path) -> Path:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    shutil.copyfile(BANK_XLSX, input_dir / "banco_ejemplo.xlsx")
    shutil.copyfile(BROKER_CSV, input_dir / "broker_ejemplo.csv")
    return input_dir


# ---------------------------------------------------------------------------
# T-500 / R-11.1/R-11.5: end to end over both fixtures
# ---------------------------------------------------------------------------


def test_t500_end_to_end_over_both_fixtures() -> None:
    input_dir = BANK_XLSX.parent
    # Both canonical fixtures already live in the same fixtures directory.
    result = run_pipeline(input_dir)
    assert len(result.input_files) == 2
    names = {f.name for f in result.input_files}
    assert names == {"banco_ejemplo.xlsx", "broker_ejemplo.csv"}
    assert len(result.owned_accounts) == 2
    assert len(result.entries) == 7 + 18
    assert len(result.series) > 0
    assert result.tool_version


def test_discover_files_skips_dotfiles_and_subdirectories(tmp_path: Path) -> None:
    from fina.pipeline import _discover_files

    input_dir = _copy_both_fixtures(tmp_path)
    (input_dir / ".DS_Store").write_text("not a real export\n")
    (input_dir / "a_subdirectory").mkdir()

    discovered = {p.name for p in _discover_files(input_dir)}
    assert discovered == {"banco_ejemplo.xlsx", "broker_ejemplo.csv"}


def test_discover_files_is_sorted_by_name() -> None:
    from fina.pipeline import _discover_files

    assert [p.name for p in _discover_files(BANK_XLSX.parent)] == [
        "banco_ejemplo.xlsx",
        "broker_ejemplo.csv",
    ]


def test_header_balances_for_the_bank_adapter_is_populated() -> None:
    from fina.pipeline import _header_balances_for

    balances = _header_balances_for("bank_es_xlsx", BANK_XLSX)
    assert balances == {("bank_es", "banco_ejemplo.xlsx"): Decimal("6183.75")}


def test_header_balances_for_a_non_bank_adapter_is_empty() -> None:
    from fina.pipeline import _header_balances_for

    assert _header_balances_for("trade_republic_broker_csv", BROKER_CSV) == {}


def test_run_pipeline_genuinely_wires_header_balances_into_reconcile(tmp_path: Path) -> None:
    """An end-to-end proof that `run_pipeline` really does thread the real adapter name and
    the real `header_balances` mapping through to `reconcile` (not `None`/dropped at either
    call site): corrupts *only* the header's own stated balance (R-7.2), leaving every row's
    declared_balance internally consistent (R-8.2's chain still closes) -- so a
    `ReconciliationError` here can only come from R-8.5's header cross-check actually running.
    """
    from openpyxl.worksheet.worksheet import Worksheet

    from builders import bank_xlsx_with

    def mutate(ws: Worksheet) -> None:
        ws["D4"] = "1,00€ EUR"  # header balance, unrelated to any row's declared_balance

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    bank_xlsx_with(input_dir, mutate, filename="banco_badheader.xlsx")

    with pytest.raises(ReconciliationError) as exc_info:
        run_pipeline(input_dir)
    assert exc_info.value.expected == Decimal("1.00")
    assert exc_info.value.declared == Decimal("6183.75")


def test_write_manifest_exact_file_contents(tmp_path: Path) -> None:
    from fina.pipeline import write_manifest

    result = run_pipeline(BANK_XLSX.parent)
    out_dir = tmp_path / "nested" / "out_dir"  # parent does not exist yet -> needs parents=True

    manifest_path = write_manifest(result, out_dir)

    assert manifest_path == out_dir / "manifest.json"
    raw = manifest_path.read_bytes()
    text = raw.decode("utf-8")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")  # exactly one trailing newline, not the literal r"\n"
    expected = json.dumps(manifest_dict(result), indent=2) + "\n"
    assert text == expected
    # indent=2, not 3: the second-level key line is indented by exactly two spaces.
    assert '\n  "tool_version"' in text


def test_write_manifest_does_not_raise_when_out_dir_already_exists(tmp_path: Path) -> None:
    from fina.pipeline import write_manifest

    result = run_pipeline(BANK_XLSX.parent)
    out_dir = tmp_path / "out"
    out_dir.mkdir()  # already exists -> exist_ok=True is required, not False/None

    write_manifest(result, out_dir)  # must not raise FileExistsError


def test_t500_input_file_sha256_matches_the_real_file_hash(tmp_path: Path) -> None:
    input_dir = _copy_both_fixtures(tmp_path)
    result = run_pipeline(input_dir)
    by_name = {f.name: f.sha256 for f in result.input_files}
    assert by_name["banco_ejemplo.xlsx"] == sha256_of_file(input_dir / "banco_ejemplo.xlsx")
    assert by_name["broker_ejemplo.csv"] == sha256_of_file(input_dir / "broker_ejemplo.csv")


# ---------------------------------------------------------------------------
# T-501 / R-11.2: adapter selected by shape, not filename
# ---------------------------------------------------------------------------


def test_t501_adapter_selected_by_shape_not_filename(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    # A broker CSV's bytes, saved under a misleading name that looks like a bank export.
    misleading_path = input_dir / "definitely_a_bank_statement.xlsx"
    misleading_path.write_bytes(BROKER_CSV.read_bytes())

    result = run_pipeline(input_dir)
    assert len(result.input_files) == 1
    assert result.input_files[0].adapter == "trade_republic_broker_csv"
    assert len(result.entries) == 18


# ---------------------------------------------------------------------------
# T-502 / R-11.2: unrecognized shape raises listing adapters tried
# ---------------------------------------------------------------------------


def test_t502_unrecognized_shape_raises_listing_adapters_tried(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "garbage.txt").write_text("this is not any known export format\n")

    with pytest.raises(ParseError) as exc_info:
        run_pipeline(input_dir)
    err = exc_info.value
    assert err.source_file == "garbage.txt"
    assert err.source_row == 1
    assert err.column == "<file shape>"
    assert err.raw_value == "<unrecognized>"
    assert err.expected == (
        "a shape matching one of the known adapters: trade_republic_broker_csv, bank_es_xlsx"
    )


# ---------------------------------------------------------------------------
# T-503 / R-2.15: duplicate file content under two names
# ---------------------------------------------------------------------------


def test_t503_duplicate_file_content_under_two_names_raises(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    shutil.copyfile(BROKER_CSV, input_dir / "broker_a.csv")
    shutil.copyfile(BROKER_CSV, input_dir / "broker_b.csv")

    with pytest.raises(DuplicateSourceError) as exc_info:
        run_pipeline(input_dir)
    assert set(exc_info.value.files or ()) == {"broker_a.csv", "broker_b.csv"}


# ---------------------------------------------------------------------------
# T-507 / R-11.5: manifest contents
# ---------------------------------------------------------------------------


def test_t507_manifest_contains_every_required_field(tmp_path: Path) -> None:
    input_dir = _copy_both_fixtures(tmp_path)
    result = run_pipeline(input_dir)
    manifest = manifest_dict(result)

    assert manifest["tool_version"] == result.tool_version
    assert {f["name"] for f in manifest["input_files"]} == {
        "banco_ejemplo.xlsx",
        "broker_ejemplo.csv",
    }
    for f in manifest["input_files"]:
        assert set(f) == {"name", "sha256", "adapter"}
        assert f["sha256"] == sha256_of_file(input_dir / f["name"])

    assert len(manifest["owned_accounts"]) == 2
    for account in manifest["owned_accounts"]:
        assert set(account) == {
            "institution",
            "iban_or_account",
            "holder_name",
            "declared_in_file",
            "as_of_date",
        }

    assert isinstance(manifest["warnings"], list)
    for warning in manifest["warnings"]:
        assert set(warning) == {"message", "source_file", "source_row"}

    assert len(manifest["section1_series"]) == len(result.series)
    for period in manifest["section1_series"]:
        assert set(period) == {
            "month",
            "as_of",
            "is_partial",
            "real_net_worth",
            "completeness",
            "savings_flow",
            "savings_only",
            "gap",
        }
        # Every money figure is a plain decimal string, never a float artifact.
        assert isinstance(period["real_net_worth"], str)


def test_manifest_series_field_values_are_exact_and_not_swapped(tmp_path: Path) -> None:
    """Four genuinely distinct `Section1Period` figures, each checked in its own manifest
    key -- catches any of the four being dropped to `str(None)` or swapped with another.
    """
    period = Section1Period(
        month=date(2027, 3, 1),
        as_of=date(2027, 3, 31),
        is_partial=False,
        real_net_worth=Decimal("111.11"),
        completeness="cash_only",
        savings_flow=Decimal("444.44"),
        savings_only=Decimal("222.22"),
        gap=Decimal("333.33"),
    )
    result = PipelineResult(
        input_files=(),
        owned_accounts=(),
        entries=(),
        warnings=(),
        series=(period,),
        tool_version="0.0.0-test",
    )
    (entry,) = manifest_dict(result)["section1_series"]
    assert entry["real_net_worth"] == "111.11"
    assert entry["savings_only"] == "222.22"
    assert entry["gap"] == "333.33"
    assert entry["savings_flow"] == "444.44"


def test_manifest_is_json_serializable() -> None:
    result = run_pipeline(BANK_XLSX.parent)
    manifest = manifest_dict(result)
    json.dumps(manifest)  # must not raise


# ---------------------------------------------------------------------------
# T-508 / R-1.20: two runs produce byte-identical manifests
# ---------------------------------------------------------------------------


def test_t508_two_runs_produce_byte_identical_manifests(tmp_path: Path) -> None:
    input_dir = _copy_both_fixtures(tmp_path)
    result_a = run_pipeline(input_dir)
    result_b = run_pipeline(input_dir)
    json_a = json.dumps(manifest_dict(result_a), indent=2)
    json_b = json.dumps(manifest_dict(result_b), indent=2)
    assert json_a == json_b
