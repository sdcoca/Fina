"""Adapter: the own-accounts confirmation file (R-3.8).

A small JSON document in which the user states, account by account, whether an IBAN that
appears on their statements under their own name is theirs. It carries no movements: it only
declares ownership (``owned: true`` -> an ``AccountDeclaration``) or its absence
(``owned: false`` -> ``AdapterResult.not_owned``). Being an input file like any statement, the
claim stays traceable to a document the user can inspect (``docs/technical-decisions.md`` §5,
as amended).

Shape::

    {"format": "fina-own-accounts", "version": 1,
     "accounts": [{"iban": "...", "holder_name": "...", "owned": true, "decided_on": "YYYY-MM-DD"}]}
"""

from __future__ import annotations

import json
from datetime import date as _date
from pathlib import Path
from typing import Any

from fina.errors import ParseError
from fina.models import USER_CONFIRMED_INSTITUTION, AccountDeclaration, AdapterResult
from fina.money import normalize_iban

FORMAT = "fina-own-accounts"
VERSION = 1


def _load(file_path: Path) -> Any:
    return json.loads(file_path.read_bytes().decode("utf-8"))


def sniff(file_path: Path) -> bool:
    """R-11.2: shape only -- a JSON object whose ``format`` is this file's own marker."""
    try:
        data = _load(file_path)
    except Exception:
        return False
    return isinstance(data, dict) and data.get("format") == FORMAT


def _fail(source_file: str, row: int, column: str, raw: object, expected: str) -> ParseError:
    return ParseError(
        source_file=source_file,
        source_row=row,
        column=column,
        raw_value=json.dumps(raw, ensure_ascii=False),
        expected=expected,
    )


def _text_field(item: dict[str, Any], key: str, source_file: str, row: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _fail(source_file, row, key, value, "a non-empty string")
    return value.strip()


def parse(file_path: Path) -> AdapterResult:
    """R-3.8. `source_row` in any `ParseError` is the 1-based position in ``accounts``
    (row 0 is the document itself: format, version, or ``accounts`` not being a list)."""
    source_file = file_path.name
    data = _load(file_path)
    if data.get("version") != VERSION:
        raise _fail(source_file, 0, "version", data.get("version"), f"version {VERSION}")
    items = data.get("accounts")
    if not isinstance(items, list):
        raise _fail(source_file, 0, "accounts", items, "a list of account decisions")

    declarations: list[AccountDeclaration] = []
    not_owned: list[str] = []
    seen: set[str] = set()
    for row, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise _fail(source_file, row, "account", item, "an object")
        iban = normalize_iban(_text_field(item, "iban", source_file, row))
        if iban in seen:
            raise _fail(source_file, row, "iban", iban, "each IBAN decided at most once")
        seen.add(iban)
        holder_name = _text_field(item, "holder_name", source_file, row)
        decided_text = _text_field(item, "decided_on", source_file, row)
        try:
            decided_on = _date.fromisoformat(decided_text)
        except ValueError:
            raise _fail(source_file, row, "decided_on", decided_text, "a YYYY-MM-DD date") from None
        owned = item.get("owned")
        if not isinstance(owned, bool):
            raise _fail(source_file, row, "owned", owned, "true or false")
        if owned:
            declarations.append(
                AccountDeclaration(
                    institution=USER_CONFIRMED_INSTITUTION,
                    iban_or_account=iban,
                    holder_name=holder_name,
                    declared_in_file=source_file,
                    as_of_date=decided_on,
                )
            )
        else:
            not_owned.append(iban)
    return AdapterResult(
        entries=(),
        accounts=tuple(declarations),
        warnings=(),
        not_owned=tuple(not_owned),
    )
