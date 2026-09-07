"""Tests for fina.section1 (WP-8): R-9.1..R-9.11."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from builders import BANK_XLSX, BROKER_CSV
from fina.adapters import bank_xlsx, broker_csv
from fina.classification import classify_entries, collect_owned_accounts
from fina.models import LedgerEntry, MovementType
from fina.section1 import (
    Section1Period,
    cash_balance,
    compute_section1,
    contribution,
    quantity_held,
    real_net_worth,
    savings_flow,
)

SOURCE_FILE = "test.csv"


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
        "is_external_flow": True,
        "status": "actual",
        "source_file": SOURCE_FILE,
        "source_row": 2,
        "file_sequence": 2,
        "raw": {},
    }
    defaults.update(overrides)
    return LedgerEntry(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# R-9.1: cash_balance, R-9.2: quantity_held (§12.2 oracle)
# ---------------------------------------------------------------------------

_ORACLE_RUNNING_CASH = [
    Decimal("8000.00"),
    Decimal("5999.00"),
    Decimal("6036.10"),
    Decimal("2930.10"),
    Decimal("2883.52"),
    Decimal("2898.82"),
    Decimal("14898.82"),
    Decimal("14898.82"),
    Decimal("14898.82"),
    Decimal("19898.82"),
    Decimal("18562.82"),
    Decimal("19011.82"),
    Decimal("18923.82"),
    Decimal("18843.82"),
    Decimal("15843.82"),
    Decimal("15792.82"),
    Decimal("15437.82"),
    Decimal("21937.82"),
]


def test_t400_cash_balance_matches_the_oracle_running_column_row_by_row() -> None:
    """§12.2's "running cash" is the broker's whole cash exposure, not one sub-account: R-6.4
    tags `BUY`/`SELL` rows `account="positions"` (for holdings purposes) even though they
    still move real cash. `cash_balance` is exercised per its literal per-account definition
    (R-9.1) and the two sub-accounts summed, which is what reproduces the oracle's single
    running total.
    """
    result = broker_csv.parse(BROKER_CSV)
    entries = result.entries
    assert len(entries) == len(_ORACLE_RUNNING_CASH) == 18
    # Two pairs of rows share a physical date (the fractional-MSFT buy pair, and the
    # MIGRATION pair); cash_balance() is date-keyed, so the value that must hold for a given
    # date is the *last* oracle value recorded for it, not an intermediate one.
    expected_by_date: dict[date, Decimal] = {}
    for entry, expected in zip(entries, _ORACLE_RUNNING_CASH, strict=True):
        expected_by_date[entry.date] = expected
    for entry in entries:
        got = cash_balance(entries, "trade_republic", "cash", entry.date) + cash_balance(
            entries, "trade_republic", "positions", entry.date
        )
        assert got == expected_by_date[entry.date]


def test_t401_final_broker_cash_is_21937_82() -> None:
    result = broker_csv.parse(BROKER_CSV)
    latest = max(e.date for e in result.entries)
    assert real_net_worth(result.entries, latest) == Decimal("21937.82")


def test_cash_balance_excludes_other_accounts_of_the_same_institution() -> None:
    cash_entry = make_entry(
        entry_id="c1", account="cash", cash_effect_eur=Decimal("100.00"), date=date(2023, 1, 1)
    )
    positions_entry = make_entry(
        entry_id="p1",
        account="positions",
        movement_type=MovementType.BUY,
        amount_eur=Decimal("-40.00"),
        cash_effect_eur=Decimal("-40.00"),
        quantity=Decimal("1"),
        asset="X1",
        date=date(2023, 1, 1),
    )
    entries = [cash_entry, positions_entry]
    assert cash_balance(entries, "trade_republic", "cash", date(2023, 1, 1)) == Decimal("100.00")
    assert cash_balance(entries, "trade_republic", "positions", date(2023, 1, 1)) == Decimal(
        "-40.00"
    )


def test_cash_balance_quantity_held_real_net_worth_savings_flow_are_decimal_even_when_empty() -> (
    None
):
    """R-9.11: every emitted figure is `Decimal`, including when nothing matches the filter
    (a dropped `start=Decimal("0")` default -- falling back to `sum`'s own `int` `0` -- would
    still equal `Decimal("0")` by value, silently passing an `==` check while returning the
    wrong *type*).
    """
    entry = make_entry(date=date(2023, 6, 1))
    before_anything = date(2023, 1, 1)
    assert isinstance(cash_balance([entry], "trade_republic", "cash", before_anything), Decimal)
    assert isinstance(
        quantity_held([entry], "trade_republic", "cash", "NOASSET", before_anything), Decimal
    )
    assert isinstance(real_net_worth([entry], before_anything), Decimal)
    assert isinstance(savings_flow([entry], 2000, 1), Decimal)


def test_t402_quantity_held_excludes_technical_adjustment() -> None:
    """A TECHNICAL_ADJUSTMENT row's quantity must never affect holdings, even when (unlike
    the real MIGRATION pair, which happens to net to zero) it does not net out on its own.
    """
    buy = make_entry(
        entry_id="b1",
        movement_type=MovementType.BUY,
        amount_eur=Decimal("-500.00"),
        cash_effect_eur=Decimal("-500.00"),
        quantity=Decimal("5"),
        asset="X1",
        account="positions",
        institution="trade_republic",
        date=date(2023, 1, 1),
    )
    phantom_adjustment = make_entry(
        entry_id="t1",
        movement_type=MovementType.TECHNICAL_ADJUSTMENT,
        amount_eur=Decimal("0"),
        cash_effect_eur=Decimal("0"),
        quantity=Decimal("100"),  # would corrupt holdings to 105 if not excluded
        asset="X1",
        account="positions",
        institution="trade_republic",
        date=date(2023, 1, 2),
    )
    held = quantity_held(
        [buy, phantom_adjustment], "trade_republic", "positions", "X1", date(2023, 1, 2)
    )
    assert held == Decimal("5")


def test_quantity_held_includes_an_entry_dated_exactly_as_of() -> None:
    """The `as_of` boundary is inclusive (`date <= as_of`, not `<`)."""
    buy = make_entry(
        movement_type=MovementType.BUY,
        amount_eur=Decimal("-100.00"),
        cash_effect_eur=Decimal("-100.00"),
        quantity=Decimal("3"),
        asset="X1",
        account="positions",
        date=date(2023, 5, 1),
    )
    assert quantity_held([buy], "trade_republic", "positions", "X1", date(2023, 5, 1)) == Decimal(
        "3"
    )


def test_t403_holdings_match_the_oracle_exactly() -> None:
    result = broker_csv.parse(BROKER_CSV)
    latest = max(e.date for e in result.entries)
    expected = {
        "US4592001014": Decimal("10.0"),  # IBM
        "US5949181045": Decimal("10.15"),  # MSFT
        "BTC": Decimal("0.022"),
        "FR0000000001": Decimal("50.0"),
        "IE00BYX5NX33": Decimal("30.0"),
    }
    for asset, expected_quantity in expected.items():
        got = quantity_held(result.entries, "trade_republic", "positions", asset, latest)
        assert got == expected_quantity


# ---------------------------------------------------------------------------
# R-9.5: savings_flow contribution, exhaustive over MovementType
# ---------------------------------------------------------------------------

_ALL_MOVEMENT_TYPES = list(MovementType)
assert len(_ALL_MOVEMENT_TYPES) == 13


@pytest.mark.parametrize("movement_type", _ALL_MOVEMENT_TYPES)
def test_t404_contribution_parametrized_over_all_13_movement_types(
    movement_type: MovementType,
) -> None:
    cash_effect = Decimal("42.00")
    if movement_type in (MovementType.RSU_VESTING, MovementType.ESPP_PURCHASE):
        with pytest.raises(NotImplementedError) as exc_info:
            contribution(movement_type, cash_effect, True)
        assert str(exc_info.value) == (
            f"{movement_type.value} is deferred (D3): no employee-plan adapter exists "
            "in this iteration."
        )
        return
    result = contribution(movement_type, cash_effect, True)
    if movement_type in (
        MovementType.EXTERNAL_DEPOSIT,
        MovementType.EXTERNAL_WITHDRAWAL,
        MovementType.EXPENSE,
        MovementType.PAYROLL_INCOME,
    ):
        assert result == cash_effect
    else:
        assert result == Decimal("0")


def test_t404_contribution_requires_is_external_flow_true_not_just_eligible_type() -> None:
    for is_external_flow in (False, None):
        assert contribution(MovementType.EXPENSE, Decimal("-10.00"), is_external_flow) == Decimal(
            "0"
        )


def test_t405_internal_transfers_contribute_zero() -> None:
    for movement_type in (MovementType.INTERNAL_TRANSFER_IN, MovementType.INTERNAL_TRANSFER_OUT):
        assert contribution(movement_type, Decimal("500.00"), False) == Decimal("0")


def test_t406_dividend_and_interest_contribute_zero() -> None:
    for movement_type in (MovementType.DIVIDEND, MovementType.INTEREST):
        assert contribution(movement_type, Decimal("15.30"), None) == Decimal("0")


def test_t407_broker_savings_flow_total_matches_the_cross_file_oracle() -> None:
    """§12.3: broker rows carrying the bank fixture's IBAN are internal (contribute 0); the
    two rows carrying the unmatched IBAN are external. Total = -3000.00 + 6500.00 = +3500.00.
    """
    bank_result = bank_xlsx.parse(BANK_XLSX)
    broker_result = broker_csv.parse(BROKER_CSV)
    owned = collect_owned_accounts([*bank_result.accounts, *broker_result.accounts])
    classified, _warnings = classify_entries(broker_result.entries, owned)

    total = sum(
        (contribution(e.movement_type, e.cash_effect_eur, e.is_external_flow) for e in classified),
        start=Decimal("0"),
    )
    assert total == Decimal("3500.00")


def test_t408_bank_savings_flow_for_2027_03_matches_the_oracle() -> None:
    """§12.1: every row is EXPENSE or an external EXTERNAL_DEPOSIT; MORENO SANZ DAVID matches
    no owned account. Total = +6500.00 - 120.50 - 430.00 - 650.25 - 38.90 - 64.20 - 12.40 =
    +5183.75.
    """
    bank_result = bank_xlsx.parse(BANK_XLSX)
    owned = collect_owned_accounts(list(bank_result.accounts))
    classified, _warnings = classify_entries(bank_result.entries, owned)

    assert savings_flow(classified, 2027, 3) == Decimal("5183.75")


# ---------------------------------------------------------------------------
# R-9.6..R-9.11: the monthly series
# ---------------------------------------------------------------------------


def test_compute_section1_savings_flow_matches_the_standalone_function_per_month() -> None:
    """`compute_section1` must call `savings_flow` with *that period's own* year and month --
    a swapped/dropped argument would silently zero out every period's flow (since no entry's
    date ever matches a `None` year or month), which an all-zero-flow ledger could not catch.
    """
    entries = [
        make_entry(entry_id="e1", date=date(2023, 1, 10), cash_effect_eur=Decimal("300.00")),
        make_entry(entry_id="e2", date=date(2023, 2, 20), cash_effect_eur=Decimal("-75.00")),
    ]
    periods = compute_section1(entries)
    assert [p.savings_flow for p in periods] == [Decimal("300.00"), Decimal("-75.00")]


def test_t409_month_with_no_entries_still_appears_with_zero_flow_and_carried_balance() -> None:
    jan = make_entry(entry_id="e1", date=date(2023, 1, 15), cash_effect_eur=Decimal("100.00"))
    march = make_entry(entry_id="e2", date=date(2023, 3, 15), cash_effect_eur=Decimal("50.00"))
    periods = compute_section1([jan, march])
    assert [p.month for p in periods] == [date(2023, 1, 1), date(2023, 2, 1), date(2023, 3, 1)]
    february = periods[1]
    assert february.savings_flow == Decimal("0")
    assert february.real_net_worth == periods[0].real_net_worth  # carried forward
    assert february.savings_only == periods[0].savings_only  # carried forward (flow was 0)


def test_t410_t0_seeding_savings_only_equals_real_net_worth_at_t0() -> None:
    entry = make_entry(date=date(2023, 5, 1), cash_effect_eur=Decimal("1000.00"))
    periods = compute_section1([entry])
    assert periods[0].savings_only == periods[0].real_net_worth


def test_t411_recursion_holds_for_every_period() -> None:
    entries = [
        make_entry(entry_id="e1", date=date(2023, 1, 5), cash_effect_eur=Decimal("100.00")),
        make_entry(entry_id="e2", date=date(2023, 2, 10), cash_effect_eur=Decimal("-30.00")),
        make_entry(entry_id="e3", date=date(2023, 4, 1), cash_effect_eur=Decimal("20.00")),
    ]
    periods = compute_section1(entries)
    for i in range(1, len(periods)):
        assert periods[i].savings_only - periods[i - 1].savings_only == periods[i].savings_flow


def test_t412_gap_equals_real_net_worth_minus_savings_only_for_every_period() -> None:
    entries = [
        make_entry(entry_id="e1", date=date(2023, 1, 5), cash_effect_eur=Decimal("100.00")),
        make_entry(
            entry_id="e2",
            date=date(2023, 2, 10),
            cash_effect_eur=Decimal("-30.00"),
            is_external_flow=False,
            movement_type=MovementType.INTERNAL_TRANSFER_OUT,
        ),
    ]
    periods = compute_section1(entries)
    for period in periods:
        assert period.gap == period.real_net_worth - period.savings_only


def test_t413_partial_final_month_carries_as_of_and_partial_flag() -> None:
    entries = [
        make_entry(entry_id="e1", date=date(2023, 1, 5), cash_effect_eur=Decimal("100.00")),
        make_entry(entry_id="e2", date=date(2023, 1, 20), cash_effect_eur=Decimal("50.00")),
    ]
    periods = compute_section1(entries)
    assert len(periods) == 1
    assert periods[0].is_partial is True
    assert periods[0].as_of == date(2023, 1, 20)


def test_a_final_month_ending_exactly_on_the_last_calendar_day_is_not_partial() -> None:
    entry = make_entry(date=date(2023, 4, 30), cash_effect_eur=Decimal("10.00"))
    periods = compute_section1([entry])
    assert periods[0].is_partial is False
    assert periods[0].as_of == date(2023, 4, 30)


def test_a_non_final_month_is_never_partial() -> None:
    entries = [
        make_entry(entry_id="e1", date=date(2023, 1, 5), cash_effect_eur=Decimal("10.00")),
        make_entry(entry_id="e2", date=date(2023, 2, 5), cash_effect_eur=Decimal("10.00")),
    ]
    periods = compute_section1(entries)
    assert periods[0].is_partial is False
    assert periods[0].as_of == date(2023, 1, 31)


def test_t414_completeness_is_cash_only() -> None:
    entry = make_entry(date=date(2023, 1, 1))
    periods = compute_section1([entry])
    assert all(p.completeness == "cash_only" for p in periods)


def test_t415_every_emitted_figure_is_decimal() -> None:
    entry = make_entry(date=date(2023, 1, 1))
    periods = compute_section1([entry])
    period = periods[0]
    for value in (period.real_net_worth, period.savings_flow, period.savings_only, period.gap):
        assert isinstance(value, Decimal)


def test_t416_empty_ledger_yields_empty_series_without_exception() -> None:
    assert compute_section1([]) == ()


def test_t417_single_entry_ledger_yields_one_period_with_zero_gap() -> None:
    entry = make_entry(date=date(2023, 6, 15), cash_effect_eur=Decimal("77.00"))
    periods = compute_section1([entry])
    assert len(periods) == 1
    assert periods[0].gap == Decimal("0")


def test_real_net_worth_never_includes_a_quantity_price_term() -> None:
    """R-9.3/R-9.4: real_net_worth is cash-only by construction -- a BUY that moves cash but
    also creates a holding must not have its holding's (nonexistent, D1) value added back.
    """
    buy = make_entry(
        movement_type=MovementType.BUY,
        amount_eur=Decimal("-500.00"),
        cash_effect_eur=Decimal("-500.00"),
        quantity=Decimal("5"),
        asset="X1",
        account="positions",
    )
    assert real_net_worth([buy], date(2023, 3, 10)) == Decimal("-500.00")


def test_section1_period_is_a_frozen_dataclass() -> None:
    import dataclasses

    entry = make_entry(date=date(2023, 1, 1))
    period = compute_section1([entry])[0]
    assert isinstance(period, Section1Period)
    with pytest.raises(dataclasses.FrozenInstanceError):
        period.gap = Decimal("1")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# T-602..T-604: property-based (Hypothesis)
# ---------------------------------------------------------------------------

_savings_flow_type = st.sampled_from(
    [
        MovementType.EXTERNAL_DEPOSIT,
        MovementType.EXTERNAL_WITHDRAWAL,
        MovementType.EXPENSE,
        MovementType.PAYROLL_INCOME,
    ]
)


@st.composite
def _ledgers(draw: st.DrawFn) -> list[LedgerEntry]:
    n = draw(st.integers(min_value=1, max_value=12))
    entries: list[LedgerEntry] = []
    for i in range(n):
        movement_type = draw(_savings_flow_type)
        cash_effect = draw(
            st.decimals(
                min_value=Decimal("-9999.99"),
                max_value=Decimal("9999.99"),
                places=2,
                allow_nan=False,
                allow_infinity=False,
            )
        )
        day_offset = draw(st.integers(min_value=0, max_value=400))
        is_external_flow = draw(st.booleans())
        entries.append(
            make_entry(
                entry_id=f"e{i}",
                date=date(2023, 1, 1) + timedelta(days=day_offset),
                movement_type=movement_type,
                amount_eur=cash_effect,
                cash_effect_eur=cash_effect,
                is_external_flow=is_external_flow,
                source_row=i + 2,
                file_sequence=i + 2,
            )
        )
    return entries


@given(_ledgers())
def test_t602_sum_of_cash_effects_equals_final_real_net_worth(entries: list[LedgerEntry]) -> None:
    latest = max(e.date for e in entries)
    total = sum((e.cash_effect_eur for e in entries), start=Decimal("0"))
    assert real_net_worth(entries, latest) == total


@given(_ledgers())
def test_t603_savings_only_recursion_holds_for_any_ledger(entries: list[LedgerEntry]) -> None:
    periods = compute_section1(entries)
    for i in range(1, len(periods)):
        assert periods[i].savings_only - periods[i - 1].savings_only == periods[i].savings_flow


@given(_ledgers())
def test_t604_gap_recursion_holds_for_any_ledger(entries: list[LedgerEntry]) -> None:
    periods = compute_section1(entries)
    for i in range(1, len(periods)):
        lhs = periods[i].gap - periods[i - 1].gap
        rhs = (periods[i].real_net_worth - periods[i - 1].real_net_worth) - periods[i].savings_flow
        assert lhs == rhs
