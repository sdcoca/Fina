"""Tests for fina.classification (WP-6): R-3.1..R-3.7."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from fina.adapters import bank_xlsx, broker_csv
from fina.classification import classify_entries, collect_owned_accounts
from fina.errors import AccountConflictError, ValidationError
from fina.models import AccountDeclaration, LedgerEntry, MovementType, Warning

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
        "raw": {"Importe": "100,00€"},
    }
    defaults.update(overrides)
    return LedgerEntry(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# R-3.4 classification rules
# ---------------------------------------------------------------------------


def test_t300_iban_match_is_internal_and_sets_is_external_flow_false() -> None:
    account = make_account()
    entry = make_entry(
        counterparty_iban="ES00 0000 0000 0000 0000 0202",  # spaced, differently cased IBAN
        cash_effect_eur=Decimal("100.00"),
    )
    (classified,), warnings = classify_entries([entry], [account])
    assert classified.movement_type is MovementType.INTERNAL_TRANSFER_IN
    assert classified.is_external_flow is False
    assert warnings == ()


def test_t301_unknown_iban_is_external() -> None:
    account = make_account()
    entry = make_entry(
        counterparty_iban="ES0000000000000000000303",
        counterparty_name="A STRANGER",
        cash_effect_eur=Decimal("-50.00"),
        movement_type=MovementType.EXTERNAL_WITHDRAWAL,
    )
    (classified,), warnings = classify_entries([entry], [account])
    assert classified.movement_type is MovementType.EXTERNAL_WITHDRAWAL
    assert classified.is_external_flow is True
    assert warnings == ()


def test_t302_no_iban_holder_name_match_is_internal() -> None:
    account = make_account(holder_name="FERNANDEZ ORTIZ LUCIA")
    entry = make_entry(
        counterparty_iban=None,
        counterparty_name="Lucia Fernandez Ortiz ",  # different order/case/whitespace
        cash_effect_eur=Decimal("100.00"),
    )
    (classified,), warnings = classify_entries([entry], [account])
    assert classified.movement_type is MovementType.INTERNAL_TRANSFER_IN
    assert classified.is_external_flow is False
    assert warnings == ()


def test_t303_no_iban_different_name_is_external() -> None:
    account = make_account(holder_name="FERNANDEZ ORTIZ LUCIA")
    entry = make_entry(
        counterparty_iban=None,
        counterparty_name="MORENO SANZ DAVID",
        cash_effect_eur=Decimal("-50.00"),
        movement_type=MovementType.EXTERNAL_WITHDRAWAL,
    )
    (classified,), warnings = classify_entries([entry], [account])
    assert classified.movement_type is MovementType.EXTERNAL_WITHDRAWAL
    assert classified.is_external_flow is True
    assert warnings == ()


def test_iban_present_but_unmatched_does_not_fall_back_to_name_match() -> None:
    """R-3.4 rule 2 only applies when the IBAN is empty/absent. A present-but-unmatched IBAN
    must not fall back to a name match, even when the name would have matched -- it goes
    straight to rule 3 (external), and (since the name matches) triggers the R-3.6 warning.
    """
    account = make_account(holder_name="FERNANDEZ ORTIZ LUCIA")
    entry = make_entry(
        counterparty_iban="ES0000000000000000000303",  # present, matches no owned account
        counterparty_name="FERNANDEZ ORTIZ LUCIA",  # would match by name alone
        cash_effect_eur=Decimal("100.00"),
    )
    (classified,), warnings = classify_entries([entry], [account])
    assert classified.movement_type is MovementType.EXTERNAL_DEPOSIT
    assert classified.is_external_flow is True
    assert len(warnings) == 1


def test_a_name_matched_internal_transfer_does_not_stop_processing_later_entries() -> None:
    """After classifying one entry internal via the no-IBAN/name-match path (R-3.4 rule 2),
    the loop must keep processing the rest of the entries, not stop.
    """
    account = make_account(holder_name="FERNANDEZ ORTIZ LUCIA")
    first = make_entry(
        entry_id="e1",
        counterparty_iban=None,
        counterparty_name="FERNANDEZ ORTIZ LUCIA",
        cash_effect_eur=Decimal("10.00"),
        source_row=9,
    )
    second = make_entry(
        entry_id="e2",
        counterparty_iban="ES0000000000000000000303",
        counterparty_name="A STRANGER",
        cash_effect_eur=Decimal("-5.00"),
        movement_type=MovementType.EXTERNAL_WITHDRAWAL,
        source_row=10,
    )
    classified, _warnings = classify_entries([first, second], [account])
    assert len(classified) == 2
    assert classified[0].movement_type is MovementType.INTERNAL_TRANSFER_IN
    assert classified[1].movement_type is MovementType.EXTERNAL_WITHDRAWAL


def test_no_iban_and_no_counterparty_name_is_external_without_warning() -> None:
    """Neither an IBAN nor a name to match against: rule 3 (external), and the R-3.6 name
    check has nothing to compare, so no warning either.
    """
    account = make_account(holder_name="FERNANDEZ ORTIZ LUCIA")
    entry = make_entry(
        counterparty_iban=None,
        counterparty_name=None,
        cash_effect_eur=Decimal("50.00"),
    )
    (classified,), warnings = classify_entries([entry], [account])
    assert classified.movement_type is MovementType.EXTERNAL_DEPOSIT
    assert classified.is_external_flow is True
    assert warnings == ()


def test_t304_cross_file_broker_row_classified_via_bank_declaration() -> None:
    """§12.3: rows 1, 7, 10, 14 of the broker fixture carry counterparty_iban equal to the
    bank fixture's declared IBAN -> internal, contributing 0 to savings_flow. Rows 15 and 18
    carry a different IBAN, matching no owned account -> external. Broker-side savings_flow
    over the whole fixture is -3000.00 + 6500.00 = +3500.00.
    """
    fixtures = Path(__file__).parent / "fixtures"
    bank_result = bank_xlsx.parse(fixtures / "banco_ejemplo.xlsx")
    broker_result = broker_csv.parse(fixtures / "broker_ejemplo.csv")

    owned = collect_owned_accounts([*bank_result.accounts, *broker_result.accounts])
    classified, _warnings = classify_entries(broker_result.entries, owned)

    internal_entries = [
        e
        for e in classified
        if e.movement_type
        in (MovementType.INTERNAL_TRANSFER_IN, MovementType.INTERNAL_TRANSFER_OUT)
    ]
    external_entries = [
        e
        for e in classified
        if e.movement_type in (MovementType.EXTERNAL_DEPOSIT, MovementType.EXTERNAL_WITHDRAWAL)
    ]
    # §12.3: rows carrying the bank fixture's IBAN (...0202) are internal; rows carrying the
    # unmatched IBAN (...0303) are external. Checking the IBAN each entry actually carries is
    # more robust than hand-mapping source_row offsets, and is exactly what the rule cares
    # about.
    assert {e.counterparty_iban for e in internal_entries} == {"ES0000000000000000000202"}
    assert {e.counterparty_iban for e in external_entries} == {"ES0000000000000000000303"}
    assert len(internal_entries) == 4
    assert len(external_entries) == 2

    savings_flow = sum(
        (e.cash_effect_eur for e in external_entries),
        start=Decimal("0"),
    )
    assert savings_flow == Decimal("3500.00")


# ---------------------------------------------------------------------------
# R-3.5 direction and the zero-cash-effect invariant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cash_effect", "expected_type"),
    [
        (Decimal("100.00"), MovementType.INTERNAL_TRANSFER_IN),
        (Decimal("-100.00"), MovementType.INTERNAL_TRANSFER_OUT),
        (Decimal("0.01"), MovementType.INTERNAL_TRANSFER_IN),  # just above zero, not just above 1
    ],
)
def test_t305_direction_follows_cash_effect_sign(
    cash_effect: Decimal, expected_type: MovementType
) -> None:
    account = make_account()
    entry = make_entry(counterparty_iban=account.iban_or_account, cash_effect_eur=cash_effect)
    (classified,), _warnings = classify_entries([entry], [account])
    assert classified.movement_type is expected_type


def test_t306_internal_transfer_with_zero_cash_effect_raises_validation_error() -> None:
    account = make_account()
    entry = make_entry(
        counterparty_iban=account.iban_or_account,
        amount_eur=Decimal("0.00"),
        cash_effect_eur=Decimal("0.00"),
    )
    with pytest.raises(ValidationError) as exc_info:
        classify_entries([entry], [account])
    err = exc_info.value
    assert err.source_file == entry.source_file
    assert err.source_row == entry.source_row
    assert err.invariant == "R-3.5: an internal transfer must have a non-zero cash_effect_eur"


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
# R-3.6 stability guard
# ---------------------------------------------------------------------------


def test_t308_external_row_matching_owned_holder_name_warns_but_stays_external() -> None:
    account = make_account(holder_name="FERNANDEZ ORTIZ LUCIA")
    entry = make_entry(
        counterparty_iban="ES0000000000000000000303",  # unmatched IBAN
        counterparty_name="FERNANDEZ ORTIZ LUCIA",  # matches the owned holder by name
        cash_effect_eur=Decimal("50.00"),
        movement_type=MovementType.EXTERNAL_DEPOSIT,
    )
    (classified,), warnings = classify_entries([entry], [account])
    assert classified.movement_type is MovementType.EXTERNAL_DEPOSIT
    assert classified.is_external_flow is True
    assert len(warnings) == 1
    warning = warnings[0]
    assert isinstance(warning, Warning)
    assert warning.message == (
        f"{entry.source_file}:{entry.source_row}: transfer from 'FERNANDEZ ORTIZ LUCIA' "
        "classified external, but the name matches an owned account holder; a statement "
        "for this account may not have been supplied"
    )
    assert warning.source_file == entry.source_file
    assert warning.source_row == entry.source_row


def test_t308_direction_wording_deposit_vs_withdrawal() -> None:
    account = make_account(holder_name="FERNANDEZ ORTIZ LUCIA")
    deposit = make_entry(
        counterparty_iban=None,
        counterparty_name="FERNANDEZ ORTIZ LUCIA",
        movement_type=MovementType.EXTERNAL_DEPOSIT,
        cash_effect_eur=Decimal("50.00"),
    )
    withdrawal = make_entry(
        counterparty_iban="ES0000000000000000000303",
        counterparty_name="FERNANDEZ ORTIZ LUCIA",
        movement_type=MovementType.EXTERNAL_WITHDRAWAL,
        cash_effect_eur=Decimal("-50.00"),
    )
    # `deposit` has no IBAN and the name matches -> classified internal, no R-3.6 warning
    # (the whole point of rule 2). Use `withdrawal` (IBAN present but unmatched) to exercise
    # the "to" wording, and a second withdrawal-shaped external row for "from" is covered by
    # test_t308_external_row_matching_owned_holder_name_warns_but_stays_external already.
    (_classified_deposit,), deposit_warnings = classify_entries([deposit], [account])
    assert deposit_warnings == ()
    (_classified_withdrawal,), withdrawal_warnings = classify_entries([withdrawal], [account])
    assert "transfer to" in withdrawal_warnings[0].message


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
    result_a = classify_entries(entries, [account])
    result_b = classify_entries(entries, [account])
    assert result_a == result_b


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
    (classified,), warnings = classify_entries([entry], [])
    assert classified == entry
    assert warnings == ()


def test_dividend_entry_is_external_flow_none_and_untouched() -> None:
    entry = make_entry(
        movement_type=MovementType.DIVIDEND,
        institution="trade_republic",
        account="cash",
        amount_eur=Decimal("15.30"),
        cash_effect_eur=Decimal("15.30"),
        is_external_flow=None,
    )
    (classified,), _warnings = classify_entries([entry], [])
    assert classified.is_external_flow is None
    assert classified == entry


def test_empty_entries_and_accounts_produce_empty_results() -> None:
    assert classify_entries([], []) == ((), ())
    assert collect_owned_accounts([]) == ()
