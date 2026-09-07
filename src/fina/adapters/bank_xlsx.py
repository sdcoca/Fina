"""Adapter: Spanish retail-bank XLSX export.

Implements: R-7.1..R-7.16.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from fina.errors import ParseError, UnknownMovementError, UnsupportedCurrencyError
from fina.models import (
    AccountDeclaration,
    AdapterResult,
    LedgerEntry,
    MovementType,
    Warning,
    check_zero_amount_warnings,
    compute_cash_effect,
    compute_entry_id,
)
from fina.money import (
    fold_vowel_accents,
    normalize_iban,
    parse_spanish_amount,
    parse_spanish_date,
)

INSTITUTION = "bank_es"
ACCOUNT = "current_account"

_MOVEMENTS_HEADER = (
    "FECHA OPERACION",
    "FECHA VALOR",
    "CONCEPTO",
    "IMPORTE",
    "SALDO",
    "DIVISA",
)


def _normalize_label(raw: object) -> str:
    text = "" if raw is None else str(raw)
    return fold_vowel_accents(text.strip().upper())


@dataclass(frozen=True)
class HeaderBlock:
    """The account metadata block at the top of the export (R-7.2).

    Exposed as a standalone, independently callable step (not only as an internal detail of
    :func:`parse`) so a later package needing the header-declared balance for a same-file
    cross-check (R-8.5) can read it directly from the file rather than needing a new field
    threaded through ``AccountDeclaration`` -- which R-2.8 fixes as ``institution``,
    ``iban_or_account``, ``holder_name``, ``declared_in_file``, ``as_of_date`` only.
    """

    iban: str
    holder_name: str
    balance: Decimal
    export_date: _date


def _find_label_value(ws: Worksheet, label: str, *, source_file: str, max_row: int) -> str:
    """Find a cell whose normalized text equals ``label`` and return the associated value's
    text (R-7.2). This format's own header block places the value directly below the label
    (verified against the fixture); below-right and right are also accepted, in that order,
    for robustness against other real layouts R-7.2's prose describes but this fixture does
    not exercise.

    Search is bounded to ``max_row`` (the header block, i.e. above the movements table):
    the movements table's own column header row repeats the word "Saldo" (R-7.4's `Importe,
    Saldo, Divisa`), which would otherwise be found as a false match once the real label is
    removed or absent.
    """
    target = _normalize_label(label)
    for row in ws.iter_rows(max_row=max_row):
        for cell in row:
            if _normalize_label(cell.value) != target:
                continue
            # A cell yielded by iter_rows() always has concrete coordinates; cast rather than
            # branch on it, since openpyxl's own type stubs are conservative here (`int |
            # None`) but no real cell from this iterator ever has a None row/column.
            cell_row = cast(int, cell.row)
            cell_col = cast(int, cell.column)
            candidates = [
                ws.cell(row=cell_row + 1, column=cell_col),
                ws.cell(row=cell_row + 1, column=cell_col + 1),
                ws.cell(row=cell_row, column=cell_col + 1),
            ]
            for candidate in candidates:
                if candidate.value not in (None, ""):
                    return str(candidate.value)
    raise ParseError(
        source_file=source_file,
        source_row=1,
        column=label,
        raw_value="<absent>",
        expected=f"a {label!r} label cell somewhere in the header block",
    )


def _parse_header_date(raw: str, *, source_file: str) -> _date:
    # Observed form: "10/03/2027 | 09:00:00" -- date and time separated by a pipe.
    date_part = raw.split("|", 1)[0].strip()
    return parse_spanish_date(date_part, source_file=source_file, source_row=1, column="FECHA")


def _read_header_block_from_sheet(ws: Worksheet, *, source_file: str) -> tuple[HeaderBlock, int]:
    """Shared by :func:`read_header_block` (opens its own workbook) and :func:`parse` (which
    already has ``ws`` open) so the movements-header search happens exactly once per parse,
    not twice against two independently-opened copies of the same file.
    """
    movements_header_row = _find_movements_header_row(ws, source_file=source_file)
    boundary = movements_header_row - 1
    iban_raw = _find_label_value(ws, "CUENTA", source_file=source_file, max_row=boundary)
    holder = _find_label_value(ws, "TITULAR", source_file=source_file, max_row=boundary)
    balance_raw = _find_label_value(ws, "SALDO", source_file=source_file, max_row=boundary)
    fecha_raw = _find_label_value(ws, "FECHA", source_file=source_file, max_row=boundary)
    balance = parse_spanish_amount(
        balance_raw, source_file=source_file, source_row=1, column="SALDO"
    )
    export_date = _parse_header_date(fecha_raw, source_file=source_file)
    header = HeaderBlock(
        iban=normalize_iban(iban_raw),
        holder_name=holder,
        balance=balance,
        export_date=export_date,
    )
    return header, movements_header_row


def read_header_block(path: Path) -> HeaderBlock:
    """Read the account metadata block (R-7.2, R-7.3)."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    header, _movements_header_row = _read_header_block_from_sheet(ws, source_file=path.name)
    return header


def _is_blank_cells(values: Sequence[object]) -> bool:
    return all(v is None or (isinstance(v, str) and v.strip() == "") for v in values)


def _find_movements_header_row(ws: Worksheet, *, source_file: str) -> int:
    for row in ws.iter_rows():
        cells = [c.value for c in row[:6]]
        if [_normalize_label(v) for v in cells] == list(_MOVEMENTS_HEADER):
            return cast(int, row[0].row)
    raise ParseError(
        source_file=source_file,
        source_row=1,
        column="<movements header>",
        raw_value="<absent>",
        expected="a row with 'Fecha operación, Fecha valor, Concepto, Importe, Saldo, Divisa'",
    )


def _cell_to_amount(
    value: object, *, source_file: str, source_row: int, column: str
) -> tuple[Decimal, Warning | None]:
    if isinstance(value, str):
        return (
            parse_spanish_amount(
                value, source_file=source_file, source_row=source_row, column=column
            ),
            None,
        )
    if value is None:
        raise ParseError(
            source_file=source_file,
            source_row=source_row,
            column=column,
            raw_value="",
            expected="a Spanish amount string or a numeric cell",
        )
    # A numeric (non-string) cell: R-7.7 -- exactness cannot be proven from the document, so
    # this must be declared via a warning even though the value itself is used as-is.
    warning = Warning(
        message=(
            f"{source_file}:{source_row}: column {column!r} is a numeric cell, not text; "
            "its exactness cannot be verified against the source document"
        ),
        source_file=source_file,
        source_row=source_row,
    )
    return Decimal(str(value)), warning


def _cell_to_date(value: object, *, source_file: str, source_row: int, column: str) -> _date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    if isinstance(value, str):
        return parse_spanish_date(
            value, source_file=source_file, source_row=source_row, column=column
        )
    raise ParseError(
        source_file=source_file,
        source_row=source_row,
        column=column,
        raw_value=str(value),
        expected="a DD/MM/YYYY date string or a date/datetime cell",
    )


# R-7.10: ordered, first-match-wins rules over the normalized Concepto.
_RULE_TRANSFERENCIA_DE = re.compile(r"^TRANSFERENCIA DE (?P<name>.+?)(?:,\s*CONCEPTO\b.*)?$")
_RULE_TRANSFERENCIA_A = re.compile(r"^TRANSFERENCIA A (?P<name>.+?)(?:,\s*CONCEPTO\b.*)?$")
_RULE_NOMINA = re.compile(r"^(?:NOMINA|ABONO NOMINA)")


@dataclass(frozen=True)
class _ConceptMatch:
    movement_type: MovementType
    counterparty_name: str | None


def _match_concept(concepto: str, *, source_file: str, source_row: int) -> _ConceptMatch:
    trimmed = concepto.strip()
    normalized = fold_vowel_accents(trimmed.upper())

    if normalized.startswith("PAGO MOVIL EN "):
        return _ConceptMatch(MovementType.EXPENSE, None)
    if normalized.startswith("TRANSACCION CONTACTLESS EN "):
        return _ConceptMatch(MovementType.EXPENSE, None)
    if normalized.startswith("COMPRA ") and "TARJETA" in normalized:
        return _ConceptMatch(MovementType.EXPENSE, None)
    if normalized.startswith("RECIBO "):
        return _ConceptMatch(MovementType.EXPENSE, None)
    if normalized.startswith("LIQUIDACION PERIODICA PRESTAMO"):
        return _ConceptMatch(MovementType.EXPENSE, None)
    if normalized.startswith("LIQUIDACION DE LAS TARJETAS DE CREDITO"):
        return _ConceptMatch(MovementType.EXPENSE, None)
    match_de = _RULE_TRANSFERENCIA_DE.match(normalized)
    if match_de is not None:
        start, end = match_de.span("name")
        return _ConceptMatch(MovementType.EXTERNAL_DEPOSIT, trimmed[start:end])
    match_a = _RULE_TRANSFERENCIA_A.match(normalized)
    if match_a is not None:
        start, end = match_a.span("name")
        return _ConceptMatch(MovementType.EXTERNAL_WITHDRAWAL, trimmed[start:end])
    if _RULE_NOMINA.match(normalized):
        return _ConceptMatch(MovementType.PAYROLL_INCOME, None)

    raise UnknownMovementError(source_file=source_file, source_row=source_row, observed=concepto)


@dataclass(frozen=True)
class _ParsedRow:
    source_row: int
    file_sequence: int
    raw: dict[str, str]
    date: _date
    value_date: _date
    movement_type: MovementType
    amount_eur: Decimal
    declared_balance: Decimal
    counterparty_name: str | None


def sniff(file_path: Path) -> bool:
    """R-11.2: does this file look like a Spanish retail-bank XLSX export, by header shape
    alone -- never by filename or extension? Used by the pipeline to pick an adapter; any
    failure to open or parse the file as this shape simply means "not this shape", not an
    error to propagate.
    """
    try:
        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb.worksheets[0]
        _read_header_block_from_sheet(ws, source_file=file_path.name)
    except Exception:
        return False
    return True


def parse(file_path: Path) -> AdapterResult:
    """R-5.1: parse a Spanish retail-bank XLSX export into an ``AdapterResult``."""
    source_file = file_path.name
    warnings: list[Warning] = []
    wb = openpyxl.load_workbook(file_path, data_only=True)
    if len(wb.worksheets) > 1:
        warnings.append(
            Warning(
                message=f"{source_file}: workbook has {len(wb.worksheets)} sheets; only the "
                "first is read",
                source_file=source_file,
                source_row=None,
            )
        )
    ws = wb.worksheets[0]

    header, header_row_idx = _read_header_block_from_sheet(ws, source_file=source_file)

    parsed_rows: list[_ParsedRow] = []
    row_idx = header_row_idx + 1
    max_row = ws.max_row
    while row_idx <= max_row:
        cells = [ws.cell(row=row_idx, column=c).value for c in range(1, 7)]
        if _is_blank_cells(cells):
            # R-7.5: a fully-blank row always stops parsing here, whether it precedes a
            # footer/disclaimer (nothing meaningful lost) or sits inside the table with more
            # data rows physically below it (that data is deliberately NOT silently
            # skipped-over and re-joined -- it is dropped, and this warning is how that
            # becomes visible instead of silent).
            warnings.append(
                Warning(
                    message=(
                        f"{source_file}:{row_idx}: blank row stopped table parsing; any "
                        "content below this row (footer, disclaimer, or further data) is "
                        "ignored"
                    ),
                    source_file=source_file,
                    source_row=row_idx,
                )
            )
            break

        (
            fecha_operacion,
            fecha_valor,
            concepto,
            importe,
            saldo,
            divisa,
        ) = cells

        divisa_text = "" if divisa is None else str(divisa).strip()
        if divisa_text != "EUR":
            raise UnsupportedCurrencyError(
                source_file=source_file, source_row=row_idx, currency=divisa_text
            )

        fecha = _cell_to_date(
            fecha_operacion, source_file=source_file, source_row=row_idx, column="Fecha operación"
        )
        valor = _cell_to_date(
            fecha_valor, source_file=source_file, source_row=row_idx, column="Fecha valor"
        )
        amount_eur, amount_warning = _cell_to_amount(
            importe, source_file=source_file, source_row=row_idx, column="Importe"
        )
        if amount_warning is not None:
            warnings.append(amount_warning)
        balance, balance_warning = _cell_to_amount(
            saldo, source_file=source_file, source_row=row_idx, column="Saldo"
        )
        if balance_warning is not None:
            warnings.append(balance_warning)

        if concepto is None:
            raise ParseError(
                source_file=source_file,
                source_row=row_idx,
                column="Concepto",
                raw_value="",
                expected="a non-empty Concepto string",
            )
        match = _match_concept(str(concepto), source_file=source_file, source_row=row_idx)

        # fecha_operacion/fecha_valor/importe/saldo are already guaranteed non-None here:
        # _cell_to_date/_cell_to_amount above raise ParseError before this point if any of
        # them were None, so (unlike `divisa`, genuinely checked for the first time on the
        # next lines) no None-guard is needed -- one would be dead code no input could reach.
        raw = {
            "Fecha operación": str(fecha_operacion),
            "Fecha valor": str(fecha_valor),
            "Concepto": str(concepto),
            "Importe": str(importe),
            "Saldo": str(saldo),
            "Divisa": divisa_text,
        }

        parsed_rows.append(
            _ParsedRow(
                source_row=row_idx,
                file_sequence=-row_idx,  # R-7.5a: this export lists rows newest-first.
                raw=raw,
                date=fecha,
                value_date=valor,
                movement_type=match.movement_type,
                amount_eur=amount_eur,
                declared_balance=balance,
                counterparty_name=match.counterparty_name,
            )
        )
        row_idx += 1

    # R-7.15/R-1.22: sort by (date, file_sequence) before this adapter's own output is used
    # for anything balance-related. file_sequence (not source_row) is the required tiebreak:
    # a raw ascending source_row would put the newest of two same-date rows first, which is
    # the wrong physical/chronological order for this newest-first export (see R-1.22's
    # rationale and docs/technical-decisions.md §4 for the real same-date tie this fixes).
    parsed_rows.sort(key=lambda r: (r.date, r.file_sequence))

    # R-7.13: the bank export provides no counterparty IBAN or transaction_id, so R-2.14's
    # duplicate-transaction_id check is inapplicable here (unlike the broker CSV adapter).
    entries = tuple(_build_entry(p, source_file) for p in parsed_rows)

    warnings.extend(check_zero_amount_warnings(source_file, entries))

    account = AccountDeclaration(
        institution=INSTITUTION,
        iban_or_account=header.iban,
        holder_name=header.holder_name,
        declared_in_file=source_file,
        as_of_date=header.export_date,
    )

    return AdapterResult(entries=entries, accounts=(account,), warnings=tuple(warnings))


def _build_entry(row: _ParsedRow, source_file: str) -> LedgerEntry:
    cash_effect = compute_cash_effect(row.movement_type, row.amount_eur, None)
    entry_id = compute_entry_id(
        transaction_id=None,
        institution=INSTITUTION,
        account=ACCOUNT,
        source_file=source_file,
        source_row=row.source_row,
    )
    # R-2.3: EXPENSE and PAYROLL_INCOME are always external -- they are not transfers to
    # another account this user might own, so they are not subject to the R-3.4 second pass.
    # EXTERNAL_DEPOSIT/EXTERNAL_WITHDRAWAL are transfer-shaped and stay undetermined (None)
    # until that second pass runs (R-5.2: an adapter never performs cross-file
    # classification).
    is_external_flow: bool | None = None
    if row.movement_type in (MovementType.EXPENSE, MovementType.PAYROLL_INCOME):
        is_external_flow = True
    return LedgerEntry(
        entry_id=entry_id,
        date=row.date,
        value_date=row.value_date,
        source_timestamp=None,
        institution=INSTITUTION,
        account=ACCOUNT,
        movement_type=row.movement_type,
        asset=None,
        asset_class=None,
        quantity=None,
        unit_price=None,
        currency="EUR",
        amount_eur=row.amount_eur,
        fee_eur=None,
        tax_eur=None,
        original_amount=None,
        original_currency=None,
        fx_rate=None,
        cash_effect_eur=cash_effect,
        declared_balance=row.declared_balance,
        counterparty_name=row.counterparty_name,
        counterparty_iban=None,
        is_external_flow=is_external_flow,
        status="actual",
        source_file=source_file,
        source_row=row.source_row,
        file_sequence=row.file_sequence,
        raw=row.raw,
    )
