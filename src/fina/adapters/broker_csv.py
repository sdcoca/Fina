"""Adapter: Trade Republic broker CSV.

Implements: R-5.1, R-5.2, R-5.3, R-5.4, R-6.1..R-6.17.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fina.errors import (
    MigrationPairError,
    ParseError,
    UnknownMovementError,
    UnsupportedCurrencyError,
)
from fina.io_utils import decode_text_with_fallback, empty_table_warning, read_csv_table
from fina.models import (
    AccountDeclaration,
    AdapterResult,
    LedgerEntry,
    MovementType,
    Warning,
    check_duplicate_transaction_ids,
    check_zero_amount_warnings,
    compute_cash_effect,
    compute_entry_id,
)
from fina.money import normalize_iban

INSTITUTION = "trade_republic"

#: R-6.1: the header must contain exactly these 23 columns, in any order.
REQUIRED_COLUMNS: tuple[str, ...] = (
    "datetime",
    "date",
    "account_type",
    "category",
    "type",
    "asset_class",
    "name",
    "symbol",
    "shares",
    "price",
    "amount",
    "fee",
    "tax",
    "currency",
    "original_amount",
    "original_currency",
    "fx_rate",
    "description",
    "transaction_id",
    "counterparty_name",
    "counterparty_iban",
    "payment_reference",
    "mcc_code",
)

_REQUIRED_COLUMN_SET = frozenset(REQUIRED_COLUMNS)

#: R-6.5: (category, type) -> movement_type, the adapter's pre-classification default.
_MOVEMENT_MAP: dict[tuple[str, str], MovementType] = {
    ("CASH", "CUSTOMER_INBOUND"): MovementType.EXTERNAL_DEPOSIT,
    ("CASH", "TRANSFER_INBOUND"): MovementType.EXTERNAL_DEPOSIT,
    ("CASH", "TRANSFER_INSTANT_INBOUND"): MovementType.EXTERNAL_DEPOSIT,
    ("CASH", "TRANSFER_OUTBOUND"): MovementType.EXTERNAL_WITHDRAWAL,
    ("CASH", "TRANSFER_INSTANT_OUTBOUND"): MovementType.EXTERNAL_WITHDRAWAL,
    ("CASH", "INTEREST_PAYMENT"): MovementType.INTEREST,
    ("CASH", "DIVIDEND"): MovementType.DIVIDEND,
    ("TRADING", "BUY"): MovementType.BUY,
    ("TRADING", "SELL"): MovementType.SELL,
    ("DELIVERY", "MIGRATION"): MovementType.TECHNICAL_ADJUSTMENT,
}

#: R-6.4: account is "positions" for these categories, "cash" otherwise.
_POSITIONS_CATEGORIES = frozenset({"TRADING", "DELIVERY"})


@dataclass(frozen=True)
class _ParsedRow:
    """Intermediate per-row state, before MIGRATION-group validation (R-6.10..R-6.13)."""

    source_row: int
    raw: dict[str, str]
    date: _date
    source_timestamp: datetime
    # `category` is deliberately not stored here: once movement_type/account/quantity/
    # is_migration are derived from it below, nothing downstream reads it again -- storing
    # it anyway would be a dead field no test could ever observe a mutation of.
    type_: str
    movement_type: MovementType
    account: str
    asset: str | None
    asset_class: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    currency: str
    amount_eur: Decimal | None
    fee_eur: Decimal | None
    tax_eur: Decimal | None
    original_amount: Decimal | None
    original_currency: str | None
    fx_rate: Decimal | None
    counterparty_name: str | None
    counterparty_iban: str | None
    transaction_id: str | None
    name_field: str | None


def parse(file_path: Path) -> AdapterResult:
    """R-5.1: parse a Trade Republic broker CSV export into an ``AdapterResult``."""
    source_file = file_path.name
    text, decode_warnings = decode_text_with_fallback(
        file_path.read_bytes(), source_file=source_file
    )
    header, data_rows = read_csv_table(text)
    warnings: list[Warning] = list(decode_warnings)

    if not data_rows:
        warnings.append(empty_table_warning(source_file))
        return AdapterResult(entries=(), accounts=(), warnings=tuple(warnings))

    warnings.extend(_validate_header(header, source_file))

    row_dicts = [
        (row_num, _row_to_fields(header, row, row_num, source_file)) for row_num, row in data_rows
    ]

    id_rows = [
        (fields["transaction_id"], row_num)
        for row_num, fields in row_dicts
        if fields["transaction_id"].strip()
    ]
    check_duplicate_transaction_ids(id_rows)

    parsed_rows = [_parse_row(fields, row_num, source_file) for row_num, fields in row_dicts]

    _validate_migration_groups(parsed_rows, source_file)

    entries = [_build_entry(p, source_file) for p in parsed_rows]

    warnings.extend(check_zero_amount_warnings(source_file, entries))

    account, declaration_warning = _extract_account_declaration(parsed_rows, source_file)
    accounts = (account,) if account is not None else ()
    if declaration_warning is not None:
        warnings.append(declaration_warning)

    return AdapterResult(entries=tuple(entries), accounts=accounts, warnings=tuple(warnings))


def _validate_header(header: list[str], source_file: str) -> list[Warning]:
    header_set = set(header)
    for column in REQUIRED_COLUMNS:
        if column not in header_set:
            raise ParseError(
                source_file=source_file,
                source_row=1,
                column=column,
                raw_value="<absent from header>",
                expected=f"column {column!r} present in the header row",
            )
    return [
        Warning(
            message=f"{source_file}: unrecognized column {column!r} preserved in raw",
            source_file=source_file,
            source_row=1,
        )
        for column in header
        if column not in _REQUIRED_COLUMN_SET
    ]


def _row_to_fields(
    header: list[str], row: list[str], source_row: int, source_file: str
) -> dict[str, str]:
    """Build the column -> value mapping for one row, requiring it to match the header's
    column count exactly -- a row with a different number of cells than the header is
    malformed and must be surfaced, not silently zipped short (R-1.23 ParseError).
    """
    if len(row) != len(header):
        raise ParseError(
            source_file=source_file,
            source_row=source_row,
            column="<row>",
            raw_value=str(row),
            expected=f"{len(header)} columns matching the header, got {len(row)}",
        )
    return dict(zip(header, row, strict=True))


def _optional_text(fields: dict[str, str], column: str) -> str | None:
    value = fields[column].strip()
    return value if value else None


def _parse_optional_decimal(
    fields: dict[str, str], column: str, *, source_file: str, source_row: int
) -> Decimal | None:
    raw = fields[column].strip()
    if raw == "":
        return None
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ParseError(
            source_file=source_file,
            source_row=source_row,
            column=column,
            raw_value=raw,
            expected="a decimal number",
        ) from exc


def _parse_date(raw: str, *, source_file: str, source_row: int, column: str) -> _date:
    try:
        return _date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ParseError(
            source_file=source_file,
            source_row=source_row,
            column=column,
            raw_value=raw,
            expected="an ISO 8601 date (YYYY-MM-DD)",
        ) from exc


def _parse_datetime(raw: str, *, source_file: str, source_row: int, column: str) -> datetime:
    try:
        return datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ParseError(
            source_file=source_file,
            source_row=source_row,
            column=column,
            raw_value=raw,
            expected="an ISO 8601 timestamp",
        ) from exc


def _parse_row(fields: dict[str, str], source_row: int, source_file: str) -> _ParsedRow:
    category = fields["category"].strip()
    type_ = fields["type"].strip()
    movement_type = _MOVEMENT_MAP.get((category, type_))
    if movement_type is None:
        raise UnknownMovementError(
            source_file=source_file,
            source_row=source_row,
            observed=f"category={category!r}, type={type_!r}",
        )

    currency = fields["currency"].strip()
    if currency != "EUR":
        raise UnsupportedCurrencyError(
            source_file=source_file, source_row=source_row, currency=currency
        )

    row_date = _parse_date(
        fields["date"], source_file=source_file, source_row=source_row, column="date"
    )
    source_timestamp = _parse_datetime(
        fields["datetime"],
        source_file=source_file,
        source_row=source_row,
        column="datetime",
    )

    # R-6.2a: shares -> quantity only for TRADING/DELIVERY rows; a CASH-category row (e.g. a
    # DIVIDEND) keeps its `shares` value in `raw` only, never as `quantity` -- the single most
    # dangerous field mapping in this adapter (phantom shares on the dividend date).
    quantity = (
        _parse_optional_decimal(fields, "shares", source_file=source_file, source_row=source_row)
        if category in _POSITIONS_CATEGORIES
        else None
    )

    account = "positions" if category in _POSITIONS_CATEGORIES else "cash"

    is_migration = category == "DELIVERY" and type_ == "MIGRATION"
    amount_eur = _parse_optional_decimal(
        fields, "amount", source_file=source_file, source_row=source_row
    )
    if amount_eur is None and not is_migration:
        raise ParseError(
            source_file=source_file,
            source_row=source_row,
            column="amount",
            raw_value=fields["amount"],
            expected="a decimal amount",
        )

    counterparty_iban_raw = _optional_text(fields, "counterparty_iban")

    return _ParsedRow(
        source_row=source_row,
        raw=dict(fields),
        date=row_date,
        source_timestamp=source_timestamp,
        type_=type_,
        movement_type=movement_type,
        account=account,
        asset=_optional_text(fields, "symbol"),
        asset_class=_optional_text(fields, "asset_class"),
        quantity=quantity,
        unit_price=_parse_optional_decimal(
            fields, "price", source_file=source_file, source_row=source_row
        ),
        currency=currency,
        amount_eur=amount_eur,
        fee_eur=_parse_optional_decimal(
            fields, "fee", source_file=source_file, source_row=source_row
        ),
        tax_eur=_parse_optional_decimal(
            fields, "tax", source_file=source_file, source_row=source_row
        ),
        original_amount=_parse_optional_decimal(
            fields, "original_amount", source_file=source_file, source_row=source_row
        ),
        original_currency=_optional_text(fields, "original_currency"),
        fx_rate=_parse_optional_decimal(
            fields, "fx_rate", source_file=source_file, source_row=source_row
        ),
        counterparty_name=_optional_text(fields, "counterparty_name"),
        counterparty_iban=(
            normalize_iban(counterparty_iban_raw) if counterparty_iban_raw is not None else None
        ),
        transaction_id=_optional_text(fields, "transaction_id"),
        name_field=_optional_text(fields, "name"),
    )


def _validate_migration_groups(rows: list[_ParsedRow], source_file: str) -> None:
    """R-6.10..R-6.13: DELIVERY/MIGRATION rows must come in valid pairs."""
    groups: dict[tuple[_date, str | None], list[_ParsedRow]] = {}
    for row in rows:
        if row.movement_type is MovementType.TECHNICAL_ADJUSTMENT:
            groups.setdefault((row.date, row.asset), []).append(row)

    for (group_date, asset), group_rows in groups.items():
        group_row_numbers = tuple(r.source_row for r in group_rows)
        if len(group_rows) != 2:
            raise MigrationPairError(
                source_file=source_file,
                date=group_date,
                asset=asset or "",
                rows=group_row_numbers,
            )
        first, second = group_rows
        quantities_ok = (
            first.quantity is not None
            and second.quantity is not None
            and first.quantity == -second.quantity
        )
        prices_ok = first.unit_price == second.unit_price
        if not (quantities_ok and prices_ok):
            raise MigrationPairError(
                source_file=source_file,
                date=group_date,
                asset=asset or "",
                rows=group_row_numbers,
            )
        for row in group_rows:
            if row.amount_eur is not None and row.amount_eur != 0:
                raise MigrationPairError(
                    source_file=source_file,
                    date=group_date,
                    asset=asset or "",
                    rows=group_row_numbers,
                )


def _build_entry(row: _ParsedRow, source_file: str) -> LedgerEntry:
    amount_eur = Decimal("0") if row.amount_eur is None else row.amount_eur
    cash_effect = compute_cash_effect(row.movement_type, amount_eur, row.fee_eur)
    entry_id = compute_entry_id(
        transaction_id=row.transaction_id,
        institution=INSTITUTION,
        account=row.account,
        source_file=source_file,
        source_row=row.source_row,
    )
    return LedgerEntry(
        entry_id=entry_id,
        date=row.date,
        value_date=None,
        source_timestamp=row.source_timestamp,
        institution=INSTITUTION,
        account=row.account,
        movement_type=row.movement_type,
        asset=row.asset,
        asset_class=row.asset_class,
        quantity=row.quantity,
        unit_price=row.unit_price,
        currency=row.currency,
        amount_eur=amount_eur,
        fee_eur=row.fee_eur,
        tax_eur=row.tax_eur,
        original_amount=row.original_amount,
        original_currency=row.original_currency,
        fx_rate=row.fx_rate,
        cash_effect_eur=cash_effect,
        declared_balance=None,
        counterparty_name=row.counterparty_name,
        counterparty_iban=row.counterparty_iban,
        is_external_flow=None,
        status="actual",
        source_file=source_file,
        source_row=row.source_row,
        raw=row.raw,
    )


def _extract_account_declaration(
    rows: list[_ParsedRow], source_file: str
) -> tuple[AccountDeclaration | None, Warning | None]:
    """R-6.16 / R-6.17."""
    candidates = [
        row
        for row in rows
        if row.type_ == "CUSTOMER_INBOUND" and row.name_field == row.counterparty_name
    ]
    if not candidates:
        return None, Warning(
            message=(
                f"{source_file}: no self-referencing CUSTOMER_INBOUND row found; the broker "
                "account cannot be declared as owned from this file alone"
            ),
            source_file=source_file,
            source_row=None,
        )
    first = min(candidates, key=lambda r: r.source_row)
    as_of_date = max(row.date for row in rows)
    return (
        AccountDeclaration(
            institution=INSTITUTION,
            iban_or_account=None,
            holder_name=first.name_field or "",
            declared_in_file=source_file,
            as_of_date=as_of_date,
        ),
        None,
    )
