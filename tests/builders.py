"""Test-data builders (test-plan.md TD-3).

Edge-case inputs are generated here by mutating the two canonical fixtures in a temp
directory, never by hand-maintaining near-duplicate fixture files. Every helper returns a
``pathlib.Path`` to a freshly written file.
"""

from __future__ import annotations

import csv
import shutil
from collections.abc import Callable
from pathlib import Path

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

FIXTURES_DIR = Path(__file__).parent / "fixtures"
BROKER_CSV = FIXTURES_DIR / "broker_ejemplo.csv"
BANK_XLSX = FIXTURES_DIR / "banco_ejemplo.xlsx"


# ---------------------------------------------------------------------------
# Broker CSV
# ---------------------------------------------------------------------------


def load_broker_rows(path: Path = BROKER_CSV) -> tuple[list[str], list[dict[str, str]]]:
    """Return ``(header, rows)`` for a broker CSV, preserving column order."""
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return header, rows


def write_broker_csv(
    dest: Path,
    header: list[str],
    rows: list[dict[str, str]],
    *,
    newline: str = "\n",
    encoding: str = "utf-8",
) -> Path:
    """Write rows back out in the same RFC 4180 (quote-all) shape as the canonical fixture."""
    with dest.open("w", newline="", encoding=encoding) as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_ALL, lineterminator=newline)
        writer.writerow(header)
        for row in rows:
            writer.writerow([row.get(col, "") for col in header])
    return dest


def broker_csv_with(
    tmp_path: Path,
    *,
    mutate_header: list[str] | None = None,
    mutate_rows: list[dict[str, str]] | None = None,
    filename: str = "broker_mutated.csv",
    newline: str = "\n",
    encoding: str = "utf-8",
) -> Path:
    """Write a copy of the broker fixture with an optionally replaced header and/or rows."""
    header, rows = load_broker_rows()
    if mutate_header is not None:
        header = mutate_header
    if mutate_rows is not None:
        rows = mutate_rows
    return write_broker_csv(tmp_path / filename, header, rows, newline=newline, encoding=encoding)


def copy_broker_csv_raw(tmp_path: Path, filename: str = "broker_copy.csv") -> Path:
    """A byte-identical copy of the canonical broker fixture (for duplicate-content tests)."""
    dest = tmp_path / filename
    shutil.copyfile(BROKER_CSV, dest)
    return dest


# ---------------------------------------------------------------------------
# Bank XLSX
# ---------------------------------------------------------------------------


def load_bank_workbook(path: Path = BANK_XLSX) -> openpyxl.Workbook:
    return openpyxl.load_workbook(path)


def bank_xlsx_with(
    tmp_path: Path,
    mutate: Callable[[Worksheet], None],
    filename: str = "banco_mutated.xlsx",
) -> Path:
    """Write a copy of the bank fixture after applying ``mutate`` to its active worksheet."""
    wb = load_bank_workbook()
    ws = wb.active
    mutate(ws)
    dest = tmp_path / filename
    wb.save(dest)
    return dest


def copy_bank_xlsx_raw(tmp_path: Path, filename: str = "banco_copy.xlsx") -> Path:
    dest = tmp_path / filename
    shutil.copyfile(BANK_XLSX, dest)
    return dest
