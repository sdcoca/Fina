"""Tests for fina.adapters.broker_csv (WP-4): R-5.1..R-5.4, R-6.1..R-6.17."""

from __future__ import annotations

import datetime as datetime_module
import itertools
from decimal import Decimal
from pathlib import Path

import pytest

from builders import BROKER_CSV, broker_csv_with, load_broker_rows
from fina.adapters import broker_csv
from fina.errors import (
    DuplicateSourceError,
    MigrationPairError,
    ParseError,
    UnknownMovementError,
    UnsupportedCurrencyError,
)
from fina.models import AdapterResult, MovementType

FIXTURE_HOLDER = "FERNANDEZ ORTIZ LUCIA"

_tx_id_counter = itertools.count(1)


def _base_row(**overrides: str) -> dict[str, str]:
    # Every call gets a fresh transaction_id by default (R-2.14 forbids duplicates within a
    # file) unless the caller explicitly wants to test that rule.
    row = {
        "datetime": "2023-01-01T00:00:00.000000Z",
        "date": "2023-01-01",
        "account_type": "DEFAULT",
        "category": "CASH",
        "type": "CUSTOMER_INBOUND",
        "asset_class": "",
        "name": FIXTURE_HOLDER,
        "symbol": "",
        "shares": "",
        "price": "",
        "amount": "100.00",
        "fee": "",
        "tax": "",
        "currency": "EUR",
        "original_amount": "",
        "original_currency": "",
        "fx_rate": "",
        "description": "",
        "transaction_id": f"tx-{next(_tx_id_counter)}",
        "counterparty_name": FIXTURE_HOLDER,
        "counterparty_iban": "",
        "payment_reference": "",
        "mcc_code": "",
    }
    row.update(overrides)
    return row


def _parse(tmp_path: Path, rows: list[dict[str, str]]) -> AdapterResult:
    path = broker_csv_with(tmp_path, mutate_rows=rows)
    return broker_csv.parse(path)


# ---------------------------------------------------------------------------
# §12.2 oracle: the canonical fixture end to end
# ---------------------------------------------------------------------------


def test_t100_canonical_fixture_yields_18_entries() -> None:
    result = broker_csv.parse(BROKER_CSV)
    assert len(result.entries) == 18


def test_oracle_final_cash_and_holdings() -> None:
    result = broker_csv.parse(BROKER_CSV)
    running = sum((e.cash_effect_eur for e in result.entries), start=Decimal("0"))
    assert running == Decimal("21937.82")


def test_t101_row1_customer_inbound_field_by_field() -> None:
    result = broker_csv.parse(BROKER_CSV)
    row1 = result.entries[0]
    assert row1.movement_type is MovementType.EXTERNAL_DEPOSIT
    assert row1.amount_eur == 8000
    assert row1.cash_effect_eur == 8000
    assert row1.institution == "trade_republic"
    assert row1.account == "cash"
    assert row1.counterparty_name == FIXTURE_HOLDER
    assert row1.counterparty_iban == "ES0000000000000000000202"
    assert row1.source_row == 2
    assert row1.entry_id == "e1000000-0000-0000-0000-000000000001"
    assert row1.is_external_flow is None


def test_t102_row4_buy_with_fee_field_by_field() -> None:
    result = broker_csv.parse(BROKER_CSV)
    row4 = result.entries[3]  # logical row 4: MSFT whole-share BUY (physical row 5)
    assert row4.movement_type is MovementType.BUY
    assert row4.asset == "US5949181045"
    assert row4.quantity == 10
    assert row4.unit_price == Decimal("310.500000")
    assert row4.amount_eur == -3105
    assert row4.fee_eur == -1
    assert row4.cash_effect_eur == -3106


def test_t103_row6_dividend_with_fx_metadata() -> None:
    result = broker_csv.parse(BROKER_CSV)
    row6 = result.entries[5]  # logical row 6: IBM dividend
    assert row6.movement_type is MovementType.DIVIDEND
    assert row6.original_amount == Decimal("17.50")
    assert row6.original_currency == "USD"
    assert row6.fx_rate == Decimal("0.874286")
    assert row6.tax_eur == Decimal("-2.30")
    assert row6.amount_eur == Decimal("15.300000")
    assert row6.cash_effect_eur == Decimal("15.300000")  # tax_eur never enters (R-2.7)


def test_t104_dividend_does_not_populate_quantity() -> None:
    result = broker_csv.parse(BROKER_CSV)
    row6 = result.entries[5]
    assert row6.quantity is None
    assert row6.raw["shares"] == "10.0000000000"  # present in raw, never mapped (R-6.2a)


def test_t105_account_positions_vs_cash(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        [
            _base_row(category="TRADING", type="BUY", shares="1", price="10", amount="-10"),
            _base_row(
                category="DELIVERY",
                type="MIGRATION",
                shares="1",
                price="10",
                amount="",
                date="2023-02-01",
                datetime="2023-02-01T00:00:00Z",
                symbol="X1",
            ),
            _base_row(
                category="DELIVERY",
                type="MIGRATION",
                shares="-1",
                price="10",
                amount="",
                date="2023-02-01",
                datetime="2023-02-01T00:00:01Z",
                symbol="X1",
            ),
            _base_row(category="CASH", type="INTEREST_PAYMENT", amount="1"),
        ],
    )
    assert result.entries[0].account == "positions"  # TRADING
    assert result.entries[1].account == "positions"  # DELIVERY
    assert result.entries[2].account == "positions"  # DELIVERY
    assert result.entries[3].account == "cash"  # CASH


# ---------------------------------------------------------------------------
# R-6.5 / R-6.6: movement mapping table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("category", "type_", "row_overrides", "expected"),
    [
        ("CASH", "CUSTOMER_INBOUND", {"amount": "10"}, MovementType.EXTERNAL_DEPOSIT),
        ("CASH", "TRANSFER_INBOUND", {"amount": "10"}, MovementType.EXTERNAL_DEPOSIT),
        (
            "CASH",
            "TRANSFER_INSTANT_INBOUND",
            {"amount": "10"},
            MovementType.EXTERNAL_DEPOSIT,
        ),
        ("CASH", "TRANSFER_OUTBOUND", {"amount": "-10"}, MovementType.EXTERNAL_WITHDRAWAL),
        (
            "CASH",
            "TRANSFER_INSTANT_OUTBOUND",
            {"amount": "-10"},
            MovementType.EXTERNAL_WITHDRAWAL,
        ),
        ("CASH", "INTEREST_PAYMENT", {"amount": "1.5"}, MovementType.INTEREST),
        ("CASH", "DIVIDEND", {"amount": "1.5"}, MovementType.DIVIDEND),
        (
            "TRADING",
            "BUY",
            {"shares": "10", "price": "1", "amount": "-10"},
            MovementType.BUY,
        ),
        (
            "TRADING",
            "SELL",
            {"shares": "-10", "price": "1", "amount": "10"},
            MovementType.SELL,
        ),
    ],
)
def test_t106_movement_mapping_pairs(
    tmp_path: Path,
    category: str,
    type_: str,
    row_overrides: dict[str, str],
    expected: MovementType,
) -> None:
    row = _base_row(category=category, type=type_, **row_overrides)
    result = _parse(tmp_path, [row])
    assert result.entries[0].movement_type is expected


def test_t106_migration_pair_maps_to_technical_adjustment(tmp_path: Path) -> None:
    rows = [
        _base_row(
            category="DELIVERY",
            type="MIGRATION",
            symbol="X1",
            shares="10",
            price="5",
            amount="",
            date="2023-02-01",
            datetime="2023-02-01T00:00:00Z",
        ),
        _base_row(
            category="DELIVERY",
            type="MIGRATION",
            symbol="X1",
            shares="-10",
            price="5",
            amount="",
            date="2023-02-01",
            datetime="2023-02-01T00:00:01Z",
        ),
    ]
    result = _parse(tmp_path, rows)
    assert all(e.movement_type is MovementType.TECHNICAL_ADJUSTMENT for e in result.entries)


def test_t107_unknown_pair_raises(tmp_path: Path) -> None:
    row = _base_row(category="CASH", type="SOMETHING_UNKNOWN")
    path = broker_csv_with(tmp_path, mutate_rows=[row], filename="unknownpair.csv")
    with pytest.raises(UnknownMovementError) as exc_info:
        broker_csv.parse(path)
    err = exc_info.value
    assert err.source_file == "unknownpair.csv"
    assert err.source_row == 2
    assert err.observed == "category='CASH', type='SOMETHING_UNKNOWN'"


def test_t108_delivery_with_non_migration_type_raises(tmp_path: Path) -> None:
    row = _base_row(category="DELIVERY", type="SOME_OTHER_TYPE", amount="")
    with pytest.raises(UnknownMovementError):
        _parse(tmp_path, [row])


def test_t109_customer_inbound_stays_pre_classified(tmp_path: Path) -> None:
    row = _base_row(category="CASH", type="CUSTOMER_INBOUND", amount="500")
    result = _parse(tmp_path, [row])
    entry = result.entries[0]
    assert entry.movement_type is MovementType.EXTERNAL_DEPOSIT
    assert entry.is_external_flow is None


def test_t110_split_trade_produces_two_entries() -> None:
    result = broker_csv.parse(BROKER_CSV)
    msft_entries = [e for e in result.entries if e.asset == "US5949181045"]
    assert len(msft_entries) == 2
    assert msft_entries[0].quantity == 10
    assert msft_entries[1].quantity == Decimal("0.1500000000")


def test_t111_empty_numeric_string_is_none_not_zero() -> None:
    result = broker_csv.parse(BROKER_CSV)
    msft_fractional = result.entries[4]
    assert msft_fractional.fee_eur is None


def test_full_field_propagation_on_a_plain_row(tmp_path: Path) -> None:
    """One row exercising every simple field pass-through in `_build_entry`, so a mutation
    that swaps any of them for `None` (or the wrong literal) is caught directly, rather than
    only through incidental oracle-fixture coincidences.
    """
    row = _base_row(
        category="TRADING",
        type="BUY",
        asset_class="STOCK",
        symbol="X1",
        shares="5",
        price="2.00",
        amount="-10.00",
        currency="EUR",
    )
    path = broker_csv_with(tmp_path, mutate_rows=[row], filename="distinctive_name.csv")
    result = broker_csv.parse(path)
    entry = result.entries[0]
    assert entry.status == "actual"
    assert entry.currency == "EUR"
    assert entry.asset_class == "STOCK"
    assert entry.source_file == "distinctive_name.csv"
    assert entry.source_row == 2


def test_cp1252_fallback_warning_names_the_actual_file(tmp_path: Path) -> None:
    header, rows = load_broker_rows()
    rows = [_base_row(category="CASH", type="INTEREST_PAYMENT", amount="1", name="año")]
    dest = tmp_path / "cp1252file.csv"
    import csv as csv_module

    with dest.open("w", newline="", encoding="cp1252") as fh:
        writer = csv_module.writer(fh, quoting=csv_module.QUOTE_ALL)
        writer.writerow(header)
        for row in rows:
            writer.writerow([row.get(col, "") for col in header])
    result = broker_csv.parse(dest)
    fallback_warnings = [w for w in result.warnings if "fell back to cp1252" in w.message]
    assert len(fallback_warnings) == 1
    assert fallback_warnings[0].source_file == "cp1252file.csv"


def test_zero_amount_migration_warning_names_the_actual_file() -> None:
    result = broker_csv.parse(BROKER_CSV)
    zero_warnings = [w for w in result.warnings if "amount_eur is exactly 0" in w.message]
    assert len(zero_warnings) == 2
    assert all(w.source_file == "broker_ejemplo.csv" for w in zero_warnings)


def test_entry_id_hash_path_uses_institution_account_file_and_row(tmp_path: Path) -> None:
    """When `transaction_id` is empty, `entry_id` falls back to a hash of institution,
    account, source_file and source_row (R-1.21) -- computed here independently so a
    mutation substituting any of the four inputs is caught.
    """
    import hashlib

    row = _base_row(
        category="CASH",
        type="INTEREST_PAYMENT",
        amount="1",
        transaction_id="",
    )
    path = broker_csv_with(tmp_path, mutate_rows=[row], filename="hashpath.csv")
    result = broker_csv.parse(path)
    entry = result.entries[0]
    digest_input = "\x1f".join(["trade_republic", "cash", "hashpath.csv", "2"])
    expected = "sha256:" + hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    assert entry.entry_id == expected


# ---------------------------------------------------------------------------
# R-6.1: header validation
# ---------------------------------------------------------------------------


def test_t112_missing_required_column_raises(tmp_path: Path) -> None:
    header, rows = load_broker_rows()
    header = [c for c in header if c != "mcc_code"]
    for row in rows:
        row.pop("mcc_code", None)
    path = broker_csv_with(
        tmp_path, mutate_header=header, mutate_rows=rows, filename="missingcol.csv"
    )
    with pytest.raises(ParseError) as exc_info:
        broker_csv.parse(path)
    err = exc_info.value
    assert err.source_file == "missingcol.csv"
    assert err.source_row == 1
    assert err.column == "mcc_code"
    assert err.raw_value == "<absent from header>"
    assert err.expected == "column 'mcc_code' present in the header row"


def test_t113_extra_unknown_column_warns_and_preserves_raw(tmp_path: Path) -> None:
    header, rows = load_broker_rows()
    header = [*header, "extra_col"]
    for row in rows:
        row["extra_col"] = "extra_value"
    path = broker_csv_with(
        tmp_path, mutate_header=header, mutate_rows=rows, filename="extracol.csv"
    )
    result = broker_csv.parse(path)
    matching = [w for w in result.warnings if "extra_col" in w.message]
    assert len(matching) == 1
    assert matching[0].message == "extracol.csv: unrecognized column 'extra_col' preserved in raw"
    assert matching[0].source_file == "extracol.csv"
    assert matching[0].source_row == 1
    assert result.entries[0].raw["extra_col"] == "extra_value"


# ---------------------------------------------------------------------------
# R-1.9 / R-1.10: date column authoritative over datetime timestamp
# ---------------------------------------------------------------------------


def test_t114_date_column_wins_over_differing_datetime(tmp_path: Path) -> None:
    row = _base_row(
        date="2025-07-18",
        datetime="2025-07-21T03:30:05.000000Z",
        category="CASH",
        type="INTEREST_PAYMENT",
        amount="1",
    )
    result = _parse(tmp_path, [row])
    entry = result.entries[0]
    assert entry.date == datetime_module.date(2025, 7, 18)
    assert entry.source_timestamp.date() == datetime_module.date(2025, 7, 21)


def test_t115_date_column_wins_across_utc_day_boundary(tmp_path: Path) -> None:
    row = _base_row(
        date="2025-11-25",
        datetime="2025-11-24T23:16:00.000000Z",
        category="CASH",
        type="INTEREST_PAYMENT",
        amount="1",
    )
    result = _parse(tmp_path, [row])
    entry = result.entries[0]
    assert entry.date == datetime_module.date(2025, 11, 25)
    assert entry.source_timestamp == datetime_module.datetime(
        2025, 11, 24, 23, 16, tzinfo=datetime_module.UTC
    )


# ---------------------------------------------------------------------------
# Malformed field values (defensive ParseError paths)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "row_kwargs"),
    [
        ("amount", {"category": "CASH", "type": "INTEREST_PAYMENT"}),
        ("price", {"category": "TRADING", "type": "BUY", "amount": "-10", "shares": "1"}),
        ("fee", {"category": "TRADING", "type": "BUY", "amount": "-10", "shares": "1"}),
        ("tax", {"category": "CASH", "type": "DIVIDEND"}),
        ("original_amount", {"category": "CASH", "type": "DIVIDEND"}),
        ("fx_rate", {"category": "CASH", "type": "DIVIDEND"}),
        ("shares", {"category": "TRADING", "type": "BUY", "amount": "-10", "price": "1"}),
    ],
)
def test_malformed_decimal_field_raises_parse_error(
    tmp_path: Path, column: str, row_kwargs: dict[str, str]
) -> None:
    row = _base_row(**row_kwargs, **{column: "not-a-number"})
    path = broker_csv_with(tmp_path, mutate_rows=[row], filename="baddecimal.csv")
    with pytest.raises(ParseError) as exc_info:
        broker_csv.parse(path)
    err = exc_info.value
    assert err.source_file == "baddecimal.csv"
    assert err.source_row == 2
    assert err.column == column
    assert err.raw_value == "not-a-number"
    assert err.expected == "a decimal number"


def test_malformed_date_raises_parse_error(tmp_path: Path) -> None:
    row = _base_row(category="CASH", type="INTEREST_PAYMENT", amount="1", date="not-a-date")
    path = broker_csv_with(tmp_path, mutate_rows=[row], filename="baddate.csv")
    with pytest.raises(ParseError) as exc_info:
        broker_csv.parse(path)
    err = exc_info.value
    assert err.source_file == "baddate.csv"
    assert err.source_row == 2
    assert err.column == "date"
    assert err.raw_value == "not-a-date"
    assert err.expected == "an ISO 8601 date (YYYY-MM-DD)"


def test_malformed_datetime_raises_parse_error(tmp_path: Path) -> None:
    row = _base_row(
        category="CASH",
        type="INTEREST_PAYMENT",
        amount="1",
        datetime="not-a-timestamp",
    )
    path = broker_csv_with(tmp_path, mutate_rows=[row], filename="badtime.csv")
    with pytest.raises(ParseError) as exc_info:
        broker_csv.parse(path)
    err = exc_info.value
    assert err.source_file == "badtime.csv"
    assert err.source_row == 2
    assert err.column == "datetime"
    assert err.raw_value == "not-a-timestamp"
    assert err.expected == "an ISO 8601 timestamp"


def test_ragged_row_shorter_than_header_raises_parse_error(tmp_path: Path) -> None:
    path = broker_csv_with(tmp_path, mutate_rows=[], filename="ragged.csv")
    # Append a raw, deliberately short line after the header (write_broker_csv always pads to
    # the header via row.get(col, ""), so we bypass it and append the ragged line directly).
    with path.open("a", encoding="utf-8", newline="") as fh:
        fh.write('"2023-01-01T00:00:00Z","2023-01-01"\n')
    with pytest.raises(ParseError) as exc_info:
        broker_csv.parse(path)
    err = exc_info.value
    assert err.source_file == "ragged.csv"
    assert err.source_row == 2
    assert err.column == "<row>"
    assert err.raw_value == str(["2023-01-01T00:00:00Z", "2023-01-01"])
    header, _rows = load_broker_rows()
    assert err.expected == f"{len(header)} columns matching the header, got 2"


def test_missing_amount_on_non_migration_row_raises_parse_error(tmp_path: Path) -> None:
    row = _base_row(category="CASH", type="INTEREST_PAYMENT", amount="")
    path = broker_csv_with(tmp_path, mutate_rows=[row], filename="noamount.csv")
    with pytest.raises(ParseError) as exc_info:
        broker_csv.parse(path)
    err = exc_info.value
    assert err.source_file == "noamount.csv"
    assert err.source_row == 2
    assert err.column == "amount"
    assert err.raw_value == ""
    assert err.expected == "a decimal amount"


# ---------------------------------------------------------------------------
# R-1.7: currency
# ---------------------------------------------------------------------------


def test_t116_non_eur_currency_raises(tmp_path: Path) -> None:
    row = _base_row(currency="USD", category="CASH", type="INTEREST_PAYMENT", amount="1")
    path = broker_csv_with(tmp_path, mutate_rows=[row], filename="badcurrency.csv")
    with pytest.raises(UnsupportedCurrencyError) as exc_info:
        broker_csv.parse(path)
    err = exc_info.value
    assert err.source_file == "badcurrency.csv"
    assert err.source_row == 2
    assert err.currency == "USD"


# ---------------------------------------------------------------------------
# R-2.14: duplicate transaction_id
# ---------------------------------------------------------------------------


def test_t117_duplicate_transaction_id_raises(tmp_path: Path) -> None:
    rows = [
        _base_row(transaction_id="dup", category="CASH", type="INTEREST_PAYMENT", amount="1"),
        _base_row(transaction_id="dup", category="CASH", type="INTEREST_PAYMENT", amount="2"),
    ]
    with pytest.raises(DuplicateSourceError) as exc_info:
        _parse(tmp_path, rows)
    assert exc_info.value.transaction_id == "dup"


# ---------------------------------------------------------------------------
# R-1.18 / R-1.19 (adapter-level integration; unit-level coverage in test_io_utils.py)
# ---------------------------------------------------------------------------


def test_t126_empty_file_yields_zero_entries_and_accounts(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    result = broker_csv.parse(path)
    assert result.entries == ()
    assert result.accounts == ()
    assert len(result.warnings) == 1
    assert result.warnings[0].source_file == "empty.csv"
    assert "empty.csv" in result.warnings[0].message


def test_t127_header_only_file_yields_zero_entries_and_accounts(tmp_path: Path) -> None:
    path = broker_csv_with(tmp_path, mutate_rows=[], filename="headeronly.csv")
    result = broker_csv.parse(path)
    assert result.entries == ()
    assert result.accounts == ()
    assert len(result.warnings) == 1
    assert result.warnings[0].source_file == "headeronly.csv"
    assert "headeronly.csv" in result.warnings[0].message


def test_t128_source_row_is_physical_1_indexed_row() -> None:
    result = broker_csv.parse(BROKER_CSV)
    assert result.entries[0].source_row == 2
    assert result.entries[-1].source_row == 19


# ---------------------------------------------------------------------------
# R-6.10..R-6.14: MIGRATION pairing
# ---------------------------------------------------------------------------


def _migration_row(**overrides: str) -> dict[str, str]:
    defaults = {
        "category": "DELIVERY",
        "type": "MIGRATION",
        "symbol": "X1",
        "date": "2023-02-01",
        "datetime": "2023-02-01T00:00:00Z",
        "shares": "10",
        "price": "5",
        "amount": "",
    }
    defaults.update(overrides)
    return _base_row(**defaults)


def test_t129_migration_pair_tagged_and_both_kept(tmp_path: Path) -> None:
    rows = [
        _migration_row(shares="10", datetime="2023-02-01T00:00:00Z"),
        _migration_row(shares="-10", datetime="2023-02-01T00:00:01Z"),
    ]
    result = _parse(tmp_path, rows)
    assert len(result.entries) == 2
    assert all(e.movement_type is MovementType.TECHNICAL_ADJUSTMENT for e in result.entries)
    assert all(e.cash_effect_eur == 0 for e in result.entries)


def test_t130_migration_group_of_one_raises(tmp_path: Path) -> None:
    rows = [_migration_row(shares="10")]
    path = broker_csv_with(tmp_path, mutate_rows=rows, filename="mig1.csv")
    with pytest.raises(MigrationPairError) as exc_info:
        broker_csv.parse(path)
    err = exc_info.value
    assert err.source_file == "mig1.csv"
    assert err.date == datetime_module.date(2023, 2, 1)
    assert err.asset == "X1"
    assert err.rows == (2,)


def test_t131_migration_group_of_three_raises(tmp_path: Path) -> None:
    rows = [
        _migration_row(shares="10", datetime="2023-02-01T00:00:00Z"),
        _migration_row(shares="-10", datetime="2023-02-01T00:00:01Z"),
        _migration_row(shares="0", datetime="2023-02-01T00:00:02Z"),
    ]
    path = broker_csv_with(tmp_path, mutate_rows=rows, filename="mig3.csv")
    with pytest.raises(MigrationPairError) as exc_info:
        broker_csv.parse(path)
    err = exc_info.value
    assert err.source_file == "mig3.csv"
    assert err.date == datetime_module.date(2023, 2, 1)
    assert err.asset == "X1"
    assert err.rows == (2, 3, 4)


def test_t132_migration_quantities_not_inverses_raises(tmp_path: Path) -> None:
    rows = [
        _migration_row(shares="10", datetime="2023-02-01T00:00:00Z"),
        _migration_row(shares="-9", datetime="2023-02-01T00:00:01Z"),
    ]
    path = broker_csv_with(tmp_path, mutate_rows=rows, filename="mig132.csv")
    with pytest.raises(MigrationPairError) as exc_info:
        broker_csv.parse(path)
    err = exc_info.value
    assert err.source_file == "mig132.csv"
    assert err.date == datetime_module.date(2023, 2, 1)
    assert err.asset == "X1"
    assert err.rows == (2, 3)


def test_t133_migration_prices_differ_raises(tmp_path: Path) -> None:
    rows = [
        _migration_row(shares="10", price="5", datetime="2023-02-01T00:00:00Z"),
        _migration_row(shares="-10", price="6", datetime="2023-02-01T00:00:01Z"),
    ]
    path = broker_csv_with(tmp_path, mutate_rows=rows, filename="mig133.csv")
    with pytest.raises(MigrationPairError) as exc_info:
        broker_csv.parse(path)
    err = exc_info.value
    assert err.source_file == "mig133.csv"
    assert err.date == datetime_module.date(2023, 2, 1)
    assert err.asset == "X1"
    assert err.rows == (2, 3)


def test_t134_migration_nonzero_amount_raises(tmp_path: Path) -> None:
    rows = [
        _migration_row(shares="10", amount="1.00", datetime="2023-02-01T00:00:00Z"),
        _migration_row(shares="-10", datetime="2023-02-01T00:00:01Z"),
    ]
    path = broker_csv_with(tmp_path, mutate_rows=rows, filename="mig134.csv")
    with pytest.raises(MigrationPairError) as exc_info:
        broker_csv.parse(path)
    err = exc_info.value
    assert err.source_file == "mig134.csv"
    assert err.date == datetime_module.date(2023, 2, 1)
    assert err.asset == "X1"
    assert err.rows == (2, 3)


def test_migration_group_of_one_empty_symbol_asset_falls_back_to_empty_string(
    tmp_path: Path,
) -> None:
    """`asset or ""` in the group-size raise site: only observable when the row's own
    `symbol` is empty, so `asset` is `None` and the fallback literal actually matters.
    """
    rows = [_migration_row(shares="10", symbol="")]
    path = broker_csv_with(tmp_path, mutate_rows=rows, filename="mig_emptysym1.csv")
    with pytest.raises(MigrationPairError) as exc_info:
        broker_csv.parse(path)
    assert exc_info.value.asset == ""


def test_migration_quantities_mismatch_empty_symbol_asset_falls_back_to_empty_string(
    tmp_path: Path,
) -> None:
    """Same as above, for the quantities/prices-mismatch raise site."""
    rows = [
        _migration_row(shares="10", symbol="", datetime="2023-02-01T00:00:00Z"),
        _migration_row(shares="-9", symbol="", datetime="2023-02-01T00:00:01Z"),
    ]
    path = broker_csv_with(tmp_path, mutate_rows=rows, filename="mig_emptysym2.csv")
    with pytest.raises(MigrationPairError) as exc_info:
        broker_csv.parse(path)
    assert exc_info.value.asset == ""


def test_migration_nonzero_amount_empty_symbol_asset_falls_back_to_empty_string(
    tmp_path: Path,
) -> None:
    """Same as above, for the nonzero-amount raise site."""
    rows = [
        _migration_row(shares="10", symbol="", amount="1.00", datetime="2023-02-01T00:00:00Z"),
        _migration_row(shares="-10", symbol="", datetime="2023-02-01T00:00:01Z"),
    ]
    path = broker_csv_with(tmp_path, mutate_rows=rows, filename="mig_emptysym3.csv")
    with pytest.raises(MigrationPairError) as exc_info:
        broker_csv.parse(path)
    assert exc_info.value.asset == ""


def test_migration_zero_amount_string_is_allowed(tmp_path: Path) -> None:
    rows = [
        _migration_row(shares="10", amount="0.00", datetime="2023-02-01T00:00:00Z"),
        _migration_row(shares="-10", datetime="2023-02-01T00:00:01Z"),
    ]
    result = _parse(tmp_path, rows)
    assert len(result.entries) == 2


# ---------------------------------------------------------------------------
# R-6.15: DELIVERY row with an unseen type is not generalized into the pairing rule
# ---------------------------------------------------------------------------


def test_t108b_delivery_unseen_type_not_generalized(tmp_path: Path) -> None:
    rows = [_base_row(category="DELIVERY", type="SOME_UNSEEN_TYPE", amount="")]
    with pytest.raises(UnknownMovementError):
        _parse(tmp_path, rows)


# ---------------------------------------------------------------------------
# R-6.16 / R-6.17: account declaration
# ---------------------------------------------------------------------------


def test_t135_account_declaration_from_first_self_referencing_row() -> None:
    result = broker_csv.parse(BROKER_CSV)
    assert len(result.accounts) == 1
    decl = result.accounts[0]
    assert decl.institution == "trade_republic"
    assert decl.iban_or_account is None
    assert decl.holder_name == FIXTURE_HOLDER
    assert decl.declared_in_file == "broker_ejemplo.csv"
    assert decl.as_of_date == datetime_module.date(2023, 11, 15)


def test_t136_no_self_referencing_row_yields_no_declaration_and_warning(
    tmp_path: Path,
) -> None:
    rows = [
        _base_row(
            category="CASH",
            type="CUSTOMER_INBOUND",
            amount="10",
            name="SOMEONE ELSE",
            counterparty_name=FIXTURE_HOLDER,
        )
    ]
    path = broker_csv_with(tmp_path, mutate_rows=rows, filename="nodecl.csv")
    result = broker_csv.parse(path)
    assert result.accounts == ()
    matching = [w for w in result.warnings if "no self-referencing" in w.message]
    assert len(matching) == 1
    assert matching[0].message == (
        "nodecl.csv: no self-referencing CUSTOMER_INBOUND row found; the broker "
        "account cannot be declared as owned from this file alone"
    )
    assert matching[0].source_file == "nodecl.csv"
    assert matching[0].source_row is None


def test_account_declaration_falls_back_to_empty_holder_name(tmp_path: Path) -> None:
    """When the self-referencing row's own `name` is empty, `holder_name` falls back to ""
    rather than to some other placeholder (R-6.16's `first.name_field or ""`).
    """
    rows = [
        _base_row(
            category="CASH",
            type="CUSTOMER_INBOUND",
            amount="10",
            name="",
            counterparty_name="",
        )
    ]
    result = _parse(tmp_path, rows)
    assert len(result.accounts) == 1
    assert result.accounts[0].holder_name == ""


# ---------------------------------------------------------------------------
# R-5.4: purity
# ---------------------------------------------------------------------------


def test_t137_adapter_does_not_call_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ExplodingDatetime(datetime_module.datetime):
        @classmethod
        def now(cls, tz: datetime_module.tzinfo | None = None) -> datetime_module.datetime:
            raise AssertionError("adapter must never call datetime.now()")

    monkeypatch.setattr(broker_csv, "datetime", _ExplodingDatetime)
    result = broker_csv.parse(BROKER_CSV)
    assert len(result.entries) == 18
    first = broker_csv.parse(BROKER_CSV)
    second = broker_csv.parse(BROKER_CSV)
    assert first.entries == second.entries
