"""Tests for fina.models (WP-2): R-1.21, R-1.23, R-2.1..R-2.17."""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import date
from decimal import Decimal

import pytest

from fina.errors import DuplicateSourceError, ValidationError
from fina.models import (
    AccountDeclaration,
    LedgerEntry,
    MovementType,
    Warning,
    check_duplicate_transaction_ids,
    check_zero_amount_warnings,
    compute_cash_effect,
    compute_entry_id,
)

SOURCE_FILE = "broker_ejemplo.csv"


def make_entry(**overrides: object) -> LedgerEntry:
    defaults: dict[str, object] = {
        "entry_id": "e1",
        "date": date(2023, 3, 10),
        "value_date": None,
        "source_timestamp": None,
        "institution": "trade_republic",
        "account": "cash",
        "movement_type": MovementType.EXTERNAL_DEPOSIT,
        "asset": None,
        "asset_class": None,
        "quantity": None,
        "unit_price": None,
        "currency": "EUR",
        "amount_eur": Decimal("100.00"),
        "fee_eur": None,
        "tax_eur": None,
        "original_amount": None,
        "original_currency": None,
        "fx_rate": None,
        "cash_effect_eur": Decimal("100.00"),
        "declared_balance": None,
        "counterparty_name": None,
        "counterparty_iban": None,
        "is_external_flow": None,
        "status": "actual",
        "source_file": SOURCE_FILE,
        "source_row": 2,
        "raw": {"amount": "100.00"},
    }
    defaults.update(overrides)
    return LedgerEntry(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# R-2.1 / R-2.2: immutability and raw preservation
# ---------------------------------------------------------------------------


def test_t050_ledger_entry_is_frozen() -> None:
    entry = make_entry()
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.amount_eur = Decimal("1")  # type: ignore[misc]


def test_t051_raw_preserved_verbatim() -> None:
    raw = {"amount": "8000.000000", "extra_column": "some value"}
    entry = make_entry(raw=raw)
    assert entry.raw == raw


# ---------------------------------------------------------------------------
# R-2.6 / R-2.7: cash effect formula
# ---------------------------------------------------------------------------


def test_t052_cash_effect_buy_with_fee() -> None:
    assert compute_cash_effect(MovementType.BUY, Decimal("-2000.00"), Decimal("-1.00")) == Decimal(
        "-2001.00"
    )


def test_t053_cash_effect_sell_with_fee() -> None:
    assert compute_cash_effect(MovementType.SELL, Decimal("450.00"), Decimal("-1.00")) == Decimal(
        "449.00"
    )


def test_t054_cash_effect_buy_no_fee() -> None:
    assert compute_cash_effect(MovementType.BUY, Decimal("-46.58"), None) == Decimal("-46.58")


def test_t055_cash_effect_dividend_ignores_tax() -> None:
    # tax_eur is not even a parameter to the formula -- it structurally cannot leak in.
    assert compute_cash_effect(MovementType.DIVIDEND, Decimal("21.42"), None) == Decimal("21.42")


def test_t056_cash_effect_interest_ignores_tax() -> None:
    assert compute_cash_effect(MovementType.INTEREST, Decimal("37.10"), None) == Decimal("37.10")


def test_t057_cash_effect_technical_adjustment_is_zero() -> None:
    assert compute_cash_effect(
        MovementType.TECHNICAL_ADJUSTMENT, Decimal("999.99"), Decimal("-5")
    ) == Decimal("0")


# Expected cash_effect_eur for amount_eur=1.23, fee_eur=-0.50, keyed by movement type. This
# table is the exhaustiveness proof required by implementation-plan.md's WP-2 notes: a
# MovementType member added without a corresponding entry here raises KeyError below and
# fails this test, rather than silently defaulting at runtime.
_EXPECTED_CASH_EFFECT: dict[MovementType, Decimal] = {
    MovementType.EXTERNAL_DEPOSIT: Decimal("1.23"),
    MovementType.EXTERNAL_WITHDRAWAL: Decimal("1.23"),
    MovementType.INTERNAL_TRANSFER_IN: Decimal("1.23"),
    MovementType.INTERNAL_TRANSFER_OUT: Decimal("1.23"),
    MovementType.BUY: Decimal("0.73"),
    MovementType.SELL: Decimal("0.73"),
    MovementType.DIVIDEND: Decimal("1.23"),
    MovementType.INTEREST: Decimal("1.23"),
    MovementType.EXPENSE: Decimal("1.23"),
    MovementType.PAYROLL_INCOME: Decimal("1.23"),
    MovementType.TECHNICAL_ADJUSTMENT: Decimal("0"),
}


@pytest.mark.parametrize("movement_type", list(MovementType))
def test_t058_cash_effect_exhaustive_over_all_movement_types(movement_type: MovementType) -> None:
    if movement_type in (MovementType.RSU_VESTING, MovementType.ESPP_PURCHASE):
        assert movement_type not in _EXPECTED_CASH_EFFECT
        with pytest.raises(NotImplementedError, match="D3"):
            compute_cash_effect(movement_type, Decimal("1.23"), Decimal("-0.50"))
        return
    expected = _EXPECTED_CASH_EFFECT[movement_type]  # KeyError => unhandled new member
    result = compute_cash_effect(movement_type, Decimal("1.23"), Decimal("-0.50"))
    assert result == expected
    assert isinstance(result, Decimal)


def test_expected_cash_effect_table_covers_every_non_d3_member() -> None:
    non_d3 = {
        mt
        for mt in MovementType
        if mt not in (MovementType.RSU_VESTING, MovementType.ESPP_PURCHASE)
    }
    assert set(_EXPECTED_CASH_EFFECT) == non_d3


def test_t059_rsu_and_espp_raise_not_implemented_naming_d3() -> None:
    with pytest.raises(
        NotImplementedError,
        match=r"^RSU_VESTING is deferred \(D3\): no employee-plan adapter exists in this "
        r"iteration\.$",
    ):
        compute_cash_effect(MovementType.RSU_VESTING, Decimal("1"), None)
    with pytest.raises(
        NotImplementedError,
        match=r"^ESPP_PURCHASE is deferred \(D3\): no employee-plan adapter exists in this "
        r"iteration\.$",
    ):
        compute_cash_effect(MovementType.ESPP_PURCHASE, Decimal("1"), None)
    with pytest.raises(NotImplementedError, match="D3"):
        make_entry(
            movement_type=MovementType.RSU_VESTING,
            quantity=Decimal("10"),
            amount_eur=Decimal("0"),
            cash_effect_eur=Decimal("0"),
        )


# ---------------------------------------------------------------------------
# R-1.21: entry_id
# ---------------------------------------------------------------------------


def test_t060_entry_id_from_transaction_id() -> None:
    got = compute_entry_id(
        transaction_id="e1000000-0001",
        institution="trade_republic",
        account="cash",
        source_file=SOURCE_FILE,
        source_row=2,
    )
    assert got == "e1000000-0001"


def test_t061_entry_id_deterministic_hash_when_absent() -> None:
    kwargs = {
        "transaction_id": None,
        "institution": "bank_es",
        "account": "current_account",
        "source_file": "banco_ejemplo.xlsx",
        "source_row": 9,
    }
    first = compute_entry_id(**kwargs)  # type: ignore[arg-type]
    second = compute_entry_id(**kwargs)  # type: ignore[arg-type]
    assert first == second
    assert first != ""
    # Exact digest value, independently computed from the documented separator (\x1f) and
    # utf-8 encoding, so a mutation to either is caught rather than merely "some hash".
    expected_digest_input = "\x1f".join(["bank_es", "current_account", "banco_ejemplo.xlsx", "9"])
    expected = "sha256:" + hashlib.sha256(expected_digest_input.encode("utf-8")).hexdigest()
    assert first == expected


def test_t062_entry_id_differs_by_source_row() -> None:
    a = compute_entry_id(
        transaction_id=None,
        institution="bank_es",
        account="current_account",
        source_file="banco_ejemplo.xlsx",
        source_row=9,
    )
    b = compute_entry_id(
        transaction_id=None,
        institution="bank_es",
        account="current_account",
        source_file="banco_ejemplo.xlsx",
        source_row=10,
    )
    assert a != b


def test_entry_id_empty_transaction_id_falls_back_to_hash() -> None:
    got = compute_entry_id(
        transaction_id="",
        institution="bank_es",
        account="current_account",
        source_file="banco_ejemplo.xlsx",
        source_row=9,
    )
    assert got.startswith("sha256:")


# ---------------------------------------------------------------------------
# R-2.10 / R-2.11: BUY / SELL sign invariants
# ---------------------------------------------------------------------------


def test_t063_buy_with_positive_amount_raises() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_entry(
            movement_type=MovementType.BUY,
            amount_eur=Decimal("2000.00"),
            quantity=Decimal("10"),
            cash_effect_eur=Decimal("2000.00"),
        )
    assert exc_info.value.source_file == SOURCE_FILE
    assert "R-2.10" in exc_info.value.invariant


def test_t064_buy_with_negative_quantity_raises() -> None:
    with pytest.raises(ValidationError, match="R-2.10"):
        make_entry(
            movement_type=MovementType.BUY,
            amount_eur=Decimal("-2000.00"),
            quantity=Decimal("-10"),
            cash_effect_eur=Decimal("-2000.00"),
        )


def test_t065_sell_with_negative_amount_raises() -> None:
    with pytest.raises(ValidationError, match="R-2.11"):
        make_entry(
            movement_type=MovementType.SELL,
            amount_eur=Decimal("-450.00"),
            quantity=Decimal("-1"),
            cash_effect_eur=Decimal("-450.00"),
        )


def test_t066_sell_with_positive_quantity_raises() -> None:
    with pytest.raises(ValidationError, match="R-2.11"):
        make_entry(
            movement_type=MovementType.SELL,
            amount_eur=Decimal("450.00"),
            quantity=Decimal("1"),
            cash_effect_eur=Decimal("450.00"),
        )


def test_buy_missing_quantity_raises() -> None:
    with pytest.raises(ValidationError, match="R-2.10"):
        make_entry(
            movement_type=MovementType.BUY,
            amount_eur=Decimal("-2000.00"),
            quantity=None,
            cash_effect_eur=Decimal("-2000.00"),
        )


# ---------------------------------------------------------------------------
# R-2.12: DIVIDEND / INTEREST invariant
# ---------------------------------------------------------------------------


def test_t067_dividend_with_quantity_raises() -> None:
    with pytest.raises(ValidationError, match="R-2.12"):
        make_entry(
            movement_type=MovementType.DIVIDEND,
            amount_eur=Decimal("15.30"),
            quantity=Decimal("10"),
            cash_effect_eur=Decimal("15.30"),
        )


def test_interest_with_negative_amount_raises() -> None:
    with pytest.raises(ValidationError, match="R-2.12"):
        make_entry(
            movement_type=MovementType.INTEREST,
            amount_eur=Decimal("-1.00"),
            quantity=None,
            cash_effect_eur=Decimal("-1.00"),
        )


def test_dividend_valid_construction_does_not_raise() -> None:
    entry = make_entry(
        movement_type=MovementType.DIVIDEND,
        amount_eur=Decimal("15.30"),
        quantity=None,
        tax_eur=Decimal("-2.30"),
        cash_effect_eur=Decimal("15.30"),
    )
    assert entry.tax_eur == Decimal("-2.30")


# ---------------------------------------------------------------------------
# R-2.13: fee sign invariant
# ---------------------------------------------------------------------------


def test_t068_positive_fee_raises() -> None:
    with pytest.raises(ValidationError, match="R-2.13"):
        make_entry(fee_eur=Decimal("1.00"))


def test_zero_fee_is_allowed() -> None:
    make_entry(fee_eur=Decimal("0"))


# ---------------------------------------------------------------------------
# R-2.16: zero-amount warning
# ---------------------------------------------------------------------------


def test_t069_zero_amount_allowed_and_warned() -> None:
    entry = make_entry(amount_eur=Decimal("0"), cash_effect_eur=Decimal("0"))
    warnings = check_zero_amount_warnings(SOURCE_FILE, [entry])
    assert len(warnings) == 1
    assert warnings[0].source_row == entry.source_row
    assert warnings[0].source_file == SOURCE_FILE
    assert warnings[0].message == f"amount_eur is exactly 0 for entry {entry.entry_id!r}"


def test_zero_amount_warnings_skips_nonzero_entries() -> None:
    entry = make_entry(amount_eur=Decimal("5.00"), cash_effect_eur=Decimal("5.00"))
    assert check_zero_amount_warnings(SOURCE_FILE, [entry]) == ()


# ---------------------------------------------------------------------------
# R-2.17: TECHNICAL_ADJUSTMENT cash-effect invariant
# ---------------------------------------------------------------------------


def test_t070_technical_adjustment_nonzero_cash_effect_raises() -> None:
    with pytest.raises(ValidationError, match="R-2.17"):
        make_entry(
            movement_type=MovementType.TECHNICAL_ADJUSTMENT,
            amount_eur=Decimal("0"),
            quantity=None,
            cash_effect_eur=Decimal("5.00"),
        )


def test_technical_adjustment_zero_cash_effect_is_allowed() -> None:
    entry = make_entry(
        movement_type=MovementType.TECHNICAL_ADJUSTMENT,
        amount_eur=Decimal("0"),
        quantity=None,
        cash_effect_eur=Decimal("0"),
    )
    assert entry.cash_effect_eur == Decimal("0")


# ---------------------------------------------------------------------------
# R-1.23: every exception class carries its required structured fields
# ---------------------------------------------------------------------------


def test_t071_exception_classes_carry_required_fields() -> None:
    from fina.errors import (
        AccountConflictError,
        MigrationPairError,
        ParseError,
        ReconciliationError,
        UnknownMovementError,
        UnsupportedCurrencyError,
    )

    parse_err = ParseError(source_file="f", source_row=1, column="c", raw_value="x", expected="y")
    assert (parse_err.source_file, parse_err.source_row, parse_err.column) == ("f", 1, "c")

    unknown = UnknownMovementError(source_file="f", source_row=1, observed="X/Y")
    assert unknown.observed == "X/Y"

    currency = UnsupportedCurrencyError(source_file="f", source_row=1, currency="USD")
    assert currency.currency == "USD"

    migration = MigrationPairError(
        source_file="f", date=date(2023, 9, 15), asset="US1", rows=(8, 9)
    )
    assert migration.rows == (8, 9)

    reconciliation = ReconciliationError(
        source_file="f",
        source_row=3,
        expected=Decimal("1"),
        declared=Decimal("2"),
        delta=Decimal("1"),
    )
    assert reconciliation.delta == Decimal("1")

    conflict = AccountConflictError(iban="ES00", holder_names=("A", "B"), files=("f1", "f2"))
    assert conflict.holder_names == ("A", "B")

    dup_by_tx = DuplicateSourceError(source_rows=(2, 3), transaction_id="tx1")
    assert dup_by_tx.transaction_id == "tx1"

    dup_by_file = DuplicateSourceError(source_rows=(0,), files=("a.csv", "b.csv"))
    assert dup_by_file.files == ("a.csv", "b.csv")

    with pytest.raises(ValueError):
        DuplicateSourceError(source_rows=(0,))  # neither files nor transaction_id
    with pytest.raises(ValueError):
        DuplicateSourceError(source_rows=(0,), files=("a",), transaction_id="t")


# ---------------------------------------------------------------------------
# R-2.14: duplicate transaction_id within one file
# ---------------------------------------------------------------------------


def test_t117_duplicate_transaction_id_raises() -> None:
    with pytest.raises(DuplicateSourceError) as exc_info:
        check_duplicate_transaction_ids(SOURCE_FILE, [("tx1", 2), ("tx2", 3), ("tx1", 4)])
    assert exc_info.value.transaction_id == "tx1"
    assert exc_info.value.source_rows == (2, 4)


def test_duplicate_transaction_id_absent_when_all_unique() -> None:
    check_duplicate_transaction_ids(SOURCE_FILE, [("tx1", 2), ("tx2", 3)])


def test_duplicate_transaction_id_empty_input() -> None:
    check_duplicate_transaction_ids(SOURCE_FILE, [])


# ---------------------------------------------------------------------------
# R-2.8 / R-2.9: AccountDeclaration
# ---------------------------------------------------------------------------


def test_account_declaration_iban_may_be_none() -> None:
    decl = AccountDeclaration(
        institution="trade_republic",
        iban_or_account=None,
        holder_name="FERNANDEZ ORTIZ LUCIA",
        declared_in_file=SOURCE_FILE,
        as_of_date=date(2023, 11, 15),
    )
    assert decl.iban_or_account is None


def test_warning_dataclass_is_frozen() -> None:
    w = Warning(message="m", source_file="f", source_row=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.message = "other"  # type: ignore[misc]
