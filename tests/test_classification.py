"""Tests for fina.classification (WP-6, revised by WP-23): R-3.1..R-3.7."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from fina.adapters import broker_csv
from fina.classification import classify_entries, collect_owned_accounts
from fina.errors import AccountConflictError
from fina.models import AccountDeclaration, LedgerEntry, MovementType

SOURCE_FILE = "banco_ejemplo.xlsx"


def make_account(**overrides: object) -> AccountDeclaration:
    defaults: dict[str, object] = {
        "institution": "bank_es",
        "iban_or_account": "ES0000000000000000000202",
        "holder_name": "FERNANDEZ ORTIZ LUCIA",
        "declared_in_file": SOURCE_FILE,
        "as_of_date": date(2027, 3, 10),
    }
    defaults.update(overrides)
    return AccountDeclaration(**defaults)  # type: ignore[arg-type]


def make_entry(**overrides: object) -> LedgerEntry:
    defaults: dict[str, object] = {
        "entry_id": "e1",
        "date": date(2027, 3, 1),
        "value_date": None,
        "source_timestamp": None,
        "institution": "bank_es",
        "account": "current_account",
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
        "source_row": 9,
        "file_sequence": -9,
        "raw": {"Importe": "100,00€"},
    }
    defaults.update(overrides)
    return LedgerEntry(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# R-3.4/R-3.5: every transfer is money in or out (revised 2026-09-27)
# ---------------------------------------------------------------------------


def test_t300_iban_of_a_supplied_account_is_still_external() -> None:
    """R-3.4 (revised): no transfer is recognized as internal, not even by the IBAN of an
    account whose own statement is supplied -- its type is kept and it counts as a flow."""
    account = make_account()
    entry = make_entry(
        counterparty_iban="ES00 0000 0000 0000 0000 0202",  # the owned account's IBAN, spaced
        cash_effect_eur=Decimal("100.00"),
    )
    (classified,) = classify_entries([entry])
    assert collect_owned_accounts([account]) == (account,)
    assert classified.movement_type is MovementType.EXTERNAL_DEPOSIT
    assert classified.is_external_flow is True
    assert classified == replace(entry, is_external_flow=True)


def test_t301_unknown_iban_is_external() -> None:
    entry = make_entry(
        counterparty_iban="ES0000000000000000000303",
        counterparty_name="A STRANGER",
        cash_effect_eur=Decimal("-50.00"),
        movement_type=MovementType.EXTERNAL_WITHDRAWAL,
    )
    (classified,) = classify_entries([entry])
    assert classified.movement_type is MovementType.EXTERNAL_WITHDRAWAL
    assert classified.is_external_flow is True


@pytest.mark.parametrize("name", ["Lucia Fernandez Ortiz ", "MORENO SANZ DAVID", None])
def test_t302_t303_a_row_without_iban_is_external_whatever_the_name(name: str | None) -> None:
    """R-3.4 (revised): the holder's own name no longer makes a transfer internal."""
    entry = make_entry(counterparty_iban=None, counterparty_name=name)
    (classified,) = classify_entries([entry])
    assert classified.movement_type is MovementType.EXTERNAL_DEPOSIT
    assert classified.is_external_flow is True


def test_t304_cross_file_broker_rows_are_all_external() -> None:
    """§12.3 (revised): the broker fixture's six transfers are all external, whether their IBAN
    is the bank fixture's (rows 1, 7, 10, 14) or unknown (15, 18). Broker-side savings_flow over
    the whole fixture is 8000 + 12000 + 5000 - 80 - 3000 + 6500 = +28420.00."""
    fixtures = Path(__file__).parent / "fixtures"
    broker_result = broker_csv.parse(fixtures / "broker_ejemplo.csv")
    classified = classify_entries(broker_result.entries)
    transfers = [e for e in classified if e.counterparty_iban is not None]
    assert len(transfers) == 6
    assert all(e.is_external_flow is True for e in transfers)
    assert all(
        e.movement_type in (MovementType.EXTERNAL_DEPOSIT, MovementType.EXTERNAL_WITHDRAWAL)
        for e in transfers
    )
    assert sum((e.cash_effect_eur for e in transfers), start=Decimal("0")) == Decimal("28420.00")


def test_t305_withdrawals_keep_their_type_and_every_entry_is_kept_in_order() -> None:
    first = make_entry(entry_id="e1", cash_effect_eur=Decimal("10.00"), source_row=9)
    second = make_entry(
        entry_id="e2",
        amount_eur=Decimal("-5.00"),
        cash_effect_eur=Decimal("-5.00"),
        movement_type=MovementType.EXTERNAL_WITHDRAWAL,
        source_row=10,
    )
    classified = classify_entries([first, second])
    assert [e.entry_id for e in classified] == ["e1", "e2"]
    assert classified[1].movement_type is MovementType.EXTERNAL_WITHDRAWAL


def test_t306_a_zero_transfer_is_kept_as_an_external_flow() -> None:
    """R-3.5's zero-cash-effect invariant only guarded internal transfers, which no longer
    exist: a zero transfer is an ordinary external row (R-2.16 already warns about it)."""
    entry = make_entry(amount_eur=Decimal("0.00"), cash_effect_eur=Decimal("0.00"))
    (classified,) = classify_entries([entry])
    assert classified.is_external_flow is True


# ---------------------------------------------------------------------------
# R-3.3 conflicting declarations
# ---------------------------------------------------------------------------


def test_t307_same_iban_two_holders_raises_account_conflict_error() -> None:
    a = make_account(holder_name="FERNANDEZ ORTIZ LUCIA", declared_in_file="a.xlsx")
    b = make_account(holder_name="MORENO SANZ DAVID", declared_in_file="b.xlsx")
    with pytest.raises(AccountConflictError) as exc_info:
        collect_owned_accounts([a, b])
    err = exc_info.value
    assert err.iban == "ES0000000000000000000202"
    assert set(err.holder_names) == {"FERNANDEZ ORTIZ LUCIA", "MORENO SANZ DAVID"}
    assert set(err.files) == {"a.xlsx", "b.xlsx"}


def test_same_iban_same_holder_across_two_files_is_not_a_conflict() -> None:
    a = make_account(declared_in_file="jan.xlsx")
    b = make_account(declared_in_file="feb.xlsx")
    owned = collect_owned_accounts([a, b])
    assert owned == (a, b)


def test_same_iban_written_with_different_whitespace_is_still_one_group() -> None:
    a = make_account(iban_or_account="ES00 0000 0000 0000 0000 0202", declared_in_file="a.xlsx")
    b = make_account(iban_or_account="es0000000000000000000202", declared_in_file="b.xlsx")
    # Same normalized IBAN, same normalized holder -> no conflict.
    collect_owned_accounts([a, b])  # must not raise


def test_declaration_with_no_iban_never_conflicts() -> None:
    a = make_account(iban_or_account=None, institution="trade_republic")
    b = make_account(iban_or_account=None, institution="trade_republic", holder_name="OTHER NAME")
    owned = collect_owned_accounts([a, b])
    assert owned == (a, b)


def test_a_no_iban_declaration_does_not_stop_scanning_the_rest_of_the_list() -> None:
    """A declaration with no IBAN is skipped, not treated as a reason to stop scanning: a
    genuine conflict declared *after* one in the input list must still be caught.
    """
    no_iban = make_account(iban_or_account=None, institution="trade_republic")
    conflicting_a = make_account(holder_name="FERNANDEZ ORTIZ LUCIA", declared_in_file="a.xlsx")
    conflicting_b = make_account(holder_name="MORENO SANZ DAVID", declared_in_file="b.xlsx")
    with pytest.raises(AccountConflictError):
        collect_owned_accounts([no_iban, conflicting_a, conflicting_b])


# ---------------------------------------------------------------------------
# R-3.7 owned_accounts is recorded exactly
# ---------------------------------------------------------------------------


def test_t309_owned_accounts_is_the_exact_union_in_input_order() -> None:
    a = make_account(declared_in_file="a.xlsx", institution="bank_es")
    b = make_account(
        iban_or_account=None,
        holder_name="FERNANDEZ ORTIZ LUCIA",
        institution="trade_republic",
        declared_in_file="b.csv",
    )
    owned = collect_owned_accounts([a, b])
    assert owned == (a, b)
    assert isinstance(owned, tuple)


# ---------------------------------------------------------------------------
# R-1.20 determinism / idempotency
# ---------------------------------------------------------------------------


def test_t310_classification_is_deterministic_across_runs() -> None:
    account = make_account()
    entries = [
        make_entry(entry_id="e1", counterparty_iban=account.iban_or_account, source_row=9),
        make_entry(
            entry_id="e2",
            counterparty_iban="ES0000000000000000000303",
            counterparty_name="MORENO SANZ DAVID",
            cash_effect_eur=Decimal("-10.00"),
            movement_type=MovementType.EXTERNAL_WITHDRAWAL,
            source_row=10,
        ),
    ]
    result_a = classify_entries(entries)
    assert classify_entries(entries) == result_a
    assert classify_entries(result_a) == result_a  # idempotent


# ---------------------------------------------------------------------------
# R-2.3 non-transfer movement types are untouched
# ---------------------------------------------------------------------------


def test_t311_non_transfer_movement_types_pass_through_unchanged() -> None:
    entry = make_entry(
        movement_type=MovementType.EXPENSE,
        counterparty_iban=None,
        counterparty_name=None,
        amount_eur=Decimal("-12.40"),
        cash_effect_eur=Decimal("-12.40"),
        is_external_flow=True,  # set by the bank adapter itself, unconditionally
    )
    (classified,) = classify_entries([entry])
    assert classified == entry


def test_dividend_entry_is_external_flow_none_and_untouched() -> None:
    entry = make_entry(
        movement_type=MovementType.DIVIDEND,
        institution="trade_republic",
        account="cash",
        amount_eur=Decimal("15.30"),
        cash_effect_eur=Decimal("15.30"),
        is_external_flow=None,
    )
    (classified,) = classify_entries([entry])
    assert classified.is_external_flow is None
    assert classified == entry


def test_empty_entries_and_accounts_produce_empty_results() -> None:
    assert classify_entries([]) == ()
    assert collect_owned_accounts([]) == ()
