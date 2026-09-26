"""R-3.8: the own-accounts confirmation file adapter."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from builders import BANK_XLSX, BROKER_CSV
from fina.adapters import own_accounts_json
from fina.errors import ParseError
from fina.models import USER_CONFIRMED_INSTITUTION, AccountDeclaration


def write_confirmations(
    tmp_path: Path, accounts: list[Any], name: str = "cuentas.json", **top: Any
) -> Path:
    doc = {"format": "fina-own-accounts", "version": 1, "accounts": accounts, **top}
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def decision(**overrides: Any) -> dict[str, Any]:
    base = {
        "iban": "ES00 0000 0000 0000 0000 0202",
        "holder_name": "LUCIA FERNANDEZ ORTIZ",
        "owned": True,
        "decided_on": "2026-09-25",
    }
    base.update(overrides)
    return base


def test_sniff_recognizes_only_its_own_format(tmp_path: Path) -> None:
    assert own_accounts_json.sniff(write_confirmations(tmp_path, []))
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"format": "something-else"}), encoding="utf-8")
    assert not own_accounts_json.sniff(other)
    a_list = tmp_path / "list.json"
    a_list.write_text("[]", encoding="utf-8")
    assert not own_accounts_json.sniff(a_list)
    assert not own_accounts_json.sniff(BROKER_CSV)
    assert not own_accounts_json.sniff(BANK_XLSX)


def test_owned_decision_becomes_a_user_confirmed_declaration(tmp_path: Path) -> None:
    result = own_accounts_json.parse(write_confirmations(tmp_path, [decision()]))
    assert result.accounts == (
        AccountDeclaration(
            institution=USER_CONFIRMED_INSTITUTION,
            iban_or_account="ES0000000000000000000202",
            holder_name="LUCIA FERNANDEZ ORTIZ",
            declared_in_file="cuentas.json",
            as_of_date=date(2026, 9, 25),
        ),
    )
    assert result.entries == ()
    assert result.warnings == ()
    assert result.not_owned == ()


def test_not_owned_decision_goes_to_not_owned_normalized(tmp_path: Path) -> None:
    path = write_confirmations(
        tmp_path,
        [decision(), decision(iban=" es00 0000 0000 0000 0000 0203 ", owned=False)],
    )
    result = own_accounts_json.parse(path)
    assert [a.iban_or_account for a in result.accounts] == ["ES0000000000000000000202"]
    assert result.not_owned == ("ES0000000000000000000203",)


def test_empty_file_declares_nothing(tmp_path: Path) -> None:
    result = own_accounts_json.parse(write_confirmations(tmp_path, []))
    assert (result.accounts, result.not_owned) == ((), ())


def error_fields(err: ParseError) -> tuple[str, int, str, str, str]:
    return (err.source_file, err.source_row, err.column, err.raw_value, err.expected)


@pytest.mark.parametrize(
    ("top", "column", "raw", "expected"),
    [
        ({"version": 2}, "version", "2", "version 1"),
        ({"accounts": {"x": "ñ"}}, "accounts", '{"x": "ñ"}', "a list of account decisions"),
    ],
)
def test_bad_document_level_fields_raise_on_row_zero(
    tmp_path: Path, top: dict[str, Any], column: str, raw: str, expected: str
) -> None:
    fields = dict(top)  # never mutate the shared parametrize value
    accounts = fields.pop("accounts", [])
    with pytest.raises(ParseError) as exc_info:
        own_accounts_json.parse(write_confirmations(tmp_path, accounts, **fields))
    assert error_fields(exc_info.value) == ("cuentas.json", 0, column, raw, expected)


@pytest.mark.parametrize(
    ("item", "column", "raw", "expected"),
    [
        ("not-an-object", "account", '"not-an-object"', "an object"),
        (decision(iban=""), "iban", '""', "a non-empty string"),
        (decision(iban=5), "iban", "5", "a non-empty string"),
        (decision(holder_name="  "), "holder_name", '"  "', "a non-empty string"),
        (decision(decided_on=""), "decided_on", '""', "a non-empty string"),
        (decision(decided_on="25/09/2026 ñ"), "decided_on", '"25/09/2026 ñ"', "a YYYY-MM-DD date"),
        (decision(owned="sí"), "owned", '"sí"', "true or false"),
        (decision(owned=None), "owned", "null", "true or false"),
    ],
)
def test_bad_decision_fields_raise_with_their_position(
    tmp_path: Path, item: Any, column: str, raw: str, expected: str
) -> None:
    """Every field of the error is pinned: file, 1-based position, column, the offending value
    as JSON (non-ASCII kept readable) and what was expected."""
    path = write_confirmations(tmp_path, [decision(iban="ES0000000000000000000203"), item])
    with pytest.raises(ParseError) as exc_info:
        own_accounts_json.parse(path)
    assert error_fields(exc_info.value) == ("cuentas.json", 2, column, raw, expected)


def test_missing_field_raises(tmp_path: Path) -> None:
    item = decision()
    del item["holder_name"]
    with pytest.raises(ParseError) as exc_info:
        own_accounts_json.parse(write_confirmations(tmp_path, [item]))
    assert error_fields(exc_info.value) == (
        "cuentas.json",
        1,
        "holder_name",
        "null",
        "a non-empty string",
    )


def test_the_same_iban_decided_twice_raises(tmp_path: Path) -> None:
    """Two decisions for one account (even spelled differently) are ambiguous: refuse."""
    path = write_confirmations(
        tmp_path, [decision(), decision(iban="ES0000000000000000000202", owned=False)]
    )
    with pytest.raises(ParseError) as exc_info:
        own_accounts_json.parse(path)
    assert error_fields(exc_info.value) == (
        "cuentas.json",
        2,
        "iban",
        '"ES0000000000000000000202"',
        "each IBAN decided at most once",
    )
