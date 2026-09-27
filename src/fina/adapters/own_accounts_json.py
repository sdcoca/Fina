"""Adapter: the account-names file (R-3.8).

A small JSON document holding the names the user gave their accounts in the app. It carries
no movements and declares no account: the engine only validates it (a malformed file must
fail loudly, R-1.24) so a run that includes it is never rejected as an unknown shape
(R-11.2). The app reads the names to label its account cards.

Shape::

    {"format": "fina-own-accounts", "version": 3,
     "aliases": {"<IBAN, or institution for an account without one>": "<name the user gave it>"}}

Versions 1 and 2 (the retired own-accounts confirmation file, WP-19/WP-22b) are still read:
their ``accounts`` decisions are ignored -- no transfer is recognized as internal any more
(R-3.4, revised 2026-09-27) -- and version 2's ``aliases`` are kept.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fina.errors import ParseError
from fina.models import AdapterResult

FORMAT = "fina-own-accounts"
VERSION = 3
#: Every version this adapter still reads.
VERSIONS = (1, 2, 3)


def _load(file_path: Path) -> Any:
    return json.loads(file_path.read_bytes().decode("utf-8"))


def sniff(file_path: Path) -> bool:
    """R-11.2: shape only -- a JSON object whose ``format`` is this file's own marker."""
    try:
        data = _load(file_path)
    except Exception:
        return False
    return isinstance(data, dict) and data.get("format") == FORMAT


def _fail(source_file: str, column: str, raw: object, expected: str) -> ParseError:
    return ParseError(
        source_file=source_file,
        source_row=0,
        column=column,
        raw_value=json.dumps(raw, ensure_ascii=False),
        expected=expected,
    )


def parse(file_path: Path) -> AdapterResult:
    """R-3.8. `source_row` in any `ParseError` is 0: the document itself."""
    source_file = file_path.name
    data = _load(file_path)
    if data.get("version") not in VERSIONS:
        raise _fail(source_file, "version", data.get("version"), "version 1, 2 or 3")
    aliases = data.get("aliases", {})
    if not isinstance(aliases, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip()
        for k, v in aliases.items()
    ):
        raise _fail(source_file, "aliases", aliases, "an object of non-empty names")
    return AdapterResult(entries=(), accounts=(), warnings=())
