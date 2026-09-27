"""R-3.8 (revised 2026-09-27): the account-names file adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from builders import BANK_XLSX, BROKER_CSV
from fina.adapters import own_accounts_json
from fina.errors import ParseError
from fina.models import AdapterResult


def write_names(tmp_path: Path, name: str = "cuentas.json", **top: Any) -> Path:
    doc = {"format": "fina-own-accounts", "version": 3, **top}
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def old_decision() -> dict[str, Any]:
    return {
        "iban": "ES00 0000 0000 0000 0000 0202",
        "holder_name": "LUCIA FERNANDEZ ORTIZ",
        "owned": True,
        "decided_on": "2026-09-25",
    }


def test_sniff_recognizes_only_its_own_format(tmp_path: Path) -> None:
    assert own_accounts_json.sniff(write_names(tmp_path))
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"format": "something-else"}), encoding="utf-8")
    assert not own_accounts_json.sniff(other)
    a_list = tmp_path / "list.json"
    a_list.write_text("[]", encoding="utf-8")
    assert not own_accounts_json.sniff(a_list)
    assert not own_accounts_json.sniff(BROKER_CSV)
    assert not own_accounts_json.sniff(BANK_XLSX)


def test_t313_the_names_file_declares_nothing(tmp_path: Path) -> None:
    aliases = {"ES0000000000000000000202": "Nómina", "trade_republic": "Broker"}
    result = own_accounts_json.parse(write_names(tmp_path, aliases=aliases))
    assert result == AdapterResult(entries=(), accounts=(), warnings=())
    assert own_accounts_json.parse(write_names(tmp_path)) == result  # no aliases at all


@pytest.mark.parametrize("version", [1, 2])
def test_t313_old_confirmation_files_are_read_and_their_decisions_ignored(
    tmp_path: Path, version: int
) -> None:
    """Retired decisions (even malformed ones) no longer mean anything: nothing is declared."""
    path = write_names(tmp_path, version=version, accounts=[old_decision(), "garbage"])
    result = own_accounts_json.parse(path)
    assert (result.accounts, result.entries, result.warnings) == ((), (), ())


def error_fields(err: ParseError) -> tuple[str, int, str, str, str]:
    return (err.source_file, err.source_row, err.column, err.raw_value, err.expected)


@pytest.mark.parametrize(
    ("top", "column", "raw", "expected"),
    [
        ({"version": 4}, "version", "4", "version 1, 2 or 3"),
        ({"version": 0}, "version", "0", "version 1, 2 or 3"),
        ({"version": "3"}, "version", '"3"', "version 1, 2 or 3"),
        ({"aliases": ["x"]}, "aliases", '["x"]', "an object of non-empty names"),
        ({"aliases": {"ES00": " "}}, "aliases", '{"ES00": " "}', "an object of non-empty names"),
        (
            {"aliases": {" ": "Ahorro"}},
            "aliases",
            '{" ": "Ahorro"}',
            "an object of non-empty names",
        ),
        ({"aliases": {"ES00": 5}}, "aliases", '{"ES00": 5}', "an object of non-empty names"),
        ({"aliases": {"ñ": None}}, "aliases", '{"ñ": null}', "an object of non-empty names"),
    ],
)
def test_bad_fields_raise_on_row_zero(
    tmp_path: Path, top: dict[str, Any], column: str, raw: str, expected: str
) -> None:
    """Every field of the error is pinned: file, row 0 (the document), column, the offending
    value as JSON (non-ASCII kept readable) and what was expected."""
    with pytest.raises(ParseError) as exc_info:
        own_accounts_json.parse(write_names(tmp_path, **top))
    assert error_fields(exc_info.value) == ("cuentas.json", 0, column, raw, expected)


def test_a_missing_version_raises(tmp_path: Path) -> None:
    path = tmp_path / "cuentas.json"
    path.write_text(json.dumps({"format": "fina-own-accounts"}), encoding="utf-8")
    with pytest.raises(ParseError) as exc_info:
        own_accounts_json.parse(path)
    assert error_fields(exc_info.value) == (
        "cuentas.json",
        0,
        "version",
        "null",
        "version 1, 2 or 3",
    )
