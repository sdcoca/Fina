"""Tests for fina.section1 (WP-8): R-9.1..R-9.11."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from builders import BANK_XLSX, BROKER_CSV
from fina.adapters import bank_xlsx, broker_csv
from fina.classification import classify_entries
from fina.models import LedgerEntry, MovementType, Warning
from fina.section1 import (
    Section1Period,
    cash_balance,
    compute_section1,
    contribution,
    cost_basis_warnings,
    opening_balances,
    positions_at_cost,
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


def test_t401_final_broker_net_worth_is_its_cash_plus_positions_at_cost() -> None:
    """21937.82 of cash (both sub-accounts' cash effects) plus 6538.246667 of units still
    held, at FIFO purchase cost (R-9.14)."""
    result = broker_csv.parse(BROKER_CSV)
    latest = max(e.date for e in result.entries)
    cash = sum(
        (cash_balance(result.entries, "trade_republic", a, latest) for a in ("cash", "positions")),
        start=Decimal("0"),
    )
    assert cash == Decimal("21937.82")
    assert positions_at_cost(result.entries, latest) == Decimal("6538.246667")
    assert real_net_worth(result.entries, latest) == Decimal("28476.066667")


# ---------------------------------------------------------------------------
# T-401a/b/c: R-9.1's anchor fix (Q-H regression) -- cash_balance/real_net_worth must anchor
# to reconciliation.py's own R-8.3 baseline, not sum cash_effect_eur from an assumed zero.
# ---------------------------------------------------------------------------


def test_t401a_final_bank_cash_is_the_anchored_figure_not_the_raw_sum() -> None:
    """§12.1: the bank fixture's earliest entry declares `balance=7500.00` *after* a
    `+6500.00` effect -- the account held `1000.00` immediately before the ledger's first row
    for it, an amount no entry's `cash_effect_eur` will ever sum to. Raw summation from zero
    gives `5183.75` (§12.1's own `savings_flow` total, by coincidence -- every bank row is
    savings-eligible); the correct, anchored figure is `6183.75`, matching the fixture's own
    header balance (R-8.5).
    """
    result = bank_xlsx.parse(BANK_XLSX)
    entries = result.entries
    latest = max(e.date for e in entries)
    raw_sum = sum((e.cash_effect_eur for e in entries), start=Decimal("0"))
    assert raw_sum == Decimal("5183.75")  # the bug's old (wrong) answer, for contrast
    got = cash_balance(entries, "bank_es", "current_account", latest)
    assert got == Decimal("6183.75")


def test_t401b_combined_run_over_both_fixtures_is_28121_57_not_27121_57() -> None:
    """The real bug, reproduced exactly as found: running the pipeline over both fixtures
    together must total `21937.82` (broker, correct -- its ledger starts at account opening)
    `+ 6183.75` (bank, anchored) `= 28121.57`, never the anchor-blind `27121.57` (`21937.82 +
    5183.75`) the pre-fix raw-summation formula produced.
    """
    bank_result = bank_xlsx.parse(BANK_XLSX)
    broker_result = broker_csv.parse(BROKER_CSV)
    entries = [*bank_result.entries, *broker_result.entries]
    latest = max(e.date for e in entries)
    got = real_net_worth(entries, latest) - positions_at_cost(entries, latest)
    assert got == Decimal("28121.57")
    assert got != Decimal("27121.57")  # the bug's old (wrong) combined answer


def test_t401c_account_with_no_declared_balance_still_falls_back_to_raw_summation() -> None:
    """R-8.4's case (the broker export has no running-balance column at all): `cash_balance`
    must still fall back to raw summation from zero, unchanged by the R-9.1 anchor fix -- the
    anchor only ever changes behaviour for an account that actually has a declared balance to
    anchor to. `reconciliation.anchor_at` returning `None` here is R-8.4's case (no declared balance
    to cross-check against); this test pins the `cash_balance`-level behaviour for it.
    """
    result = broker_csv.parse(BROKER_CSV)
    entries = result.entries
    latest = max(e.date for e in entries)
    raw_sum = sum((e.cash_effect_eur for e in entries if e.account == "cash"), start=Decimal("0"))
    got = cash_balance(entries, "trade_republic", "cash", latest)
    assert got == raw_sum  # unchanged fallback behaviour, not the anchored formula


def test_cash_balance_anchored_branch_sums_only_matching_later_entries_inclusive_of_as_of() -> None:
    """Exercises every condition in `cash_balance`'s anchored branch (R-9.1) at once: the
    anchor's own `declared_balance` plus later `cash_effect_eur` values *added* (not
    subtracted), restricted to the same `(institution, account)`, dated on or before `as_of`
    **inclusive** (`<=`, not `<`) -- even when interleaved with entries from a different
    account, a different institution, and one dated exactly on the `as_of` boundary.
    """
    anchor_entry = make_entry(
        entry_id="anchor",
        institution="bank_es",
        account="current_account",
        date=date(2023, 1, 1),
        declared_balance=Decimal("100.00"),
        cash_effect_eur=Decimal("100.00"),
        source_row=2,
        file_sequence=2,
    )
    later_no_balance = make_entry(
        entry_id="later1",
        institution="bank_es",
        account="current_account",
        date=date(2023, 1, 2),
        declared_balance=None,
        cash_effect_eur=Decimal("-30.00"),
        source_row=3,
        file_sequence=3,
    )
    on_as_of_boundary = make_entry(
        entry_id="later2",
        institution="bank_es",
        account="current_account",
        date=date(2023, 1, 3),
        declared_balance=None,
        cash_effect_eur=Decimal("10.00"),
        source_row=4,
        file_sequence=4,
    )
    other_account = make_entry(
        entry_id="other_acct",
        institution="bank_es",
        account="savings_account",
        date=date(2023, 1, 2),
        declared_balance=None,
        cash_effect_eur=Decimal("999999.00"),
        source_row=5,
        file_sequence=5,
    )
    other_institution = make_entry(
        entry_id="other_inst",
        institution="trade_republic",
        account="current_account",
        date=date(2023, 1, 2),
        declared_balance=None,
        cash_effect_eur=Decimal("888888.00"),
        source_row=6,
        file_sequence=6,
    )
    entries = [
        anchor_entry,
        later_no_balance,
        on_as_of_boundary,
        other_account,
        other_institution,
    ]
    got = cash_balance(entries, "bank_es", "current_account", date(2023, 1, 3))
    assert got == Decimal("80.00")  # 100.00 - 30.00 + 10.00, boundary entry included


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
assert len(_ALL_MOVEMENT_TYPES) == 14


@pytest.mark.parametrize("movement_type", _ALL_MOVEMENT_TYPES)
def test_t404_contribution_parametrized_over_all_14_movement_types(
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
    """§12.3 (revised 2026-09-27): every broker transfer is external, whether its IBAN is the
    bank fixture's or unknown. Total = 8000 + 12000 + 5000 - 80 - 3000 + 6500 = +28420.00.
    """
    classified = classify_entries(broker_csv.parse(BROKER_CSV).entries)

    total = sum(
        (contribution(e.movement_type, e.cash_effect_eur, e.is_external_flow) for e in classified),
        start=Decimal("0"),
    )
    assert total == Decimal("28420.00")


def test_t408_bank_savings_flow_for_2027_03_matches_the_oracle() -> None:
    """§12.1: every row is EXPENSE or an external EXTERNAL_DEPOSIT. Total = +6500.00 - 120.50 -
    430.00 - 650.25 - 38.90 - 64.20 - 12.40 = +5183.75.
    """
    classified = classify_entries(bank_xlsx.parse(BANK_XLSX).entries)

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


def test_t414_completeness_flags_positions_at_cost() -> None:
    entry = make_entry(date=date(2023, 1, 1))
    periods = compute_section1([entry])
    assert all(p.completeness == "positions_at_cost" for p in periods)


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


def test_a_buy_moves_cash_into_a_position_at_cost_leaving_net_worth_unchanged() -> None:
    """R-9.3/R-9.4 (revised) and R-9.14: the 500 that left the cash is now held as 5 units
    valued at what they cost -- never at a (nonexistent, D1) market price, never at zero.
    """
    buy = make_entry(
        movement_type=MovementType.BUY,
        amount_eur=Decimal("-500.00"),
        cash_effect_eur=Decimal("-500.00"),
        quantity=Decimal("5"),
        asset="X1",
        account="positions",
    )
    assert real_net_worth([buy], date(2023, 3, 10)) == Decimal("0")
    assert positions_at_cost([buy], date(2023, 3, 10)) == Decimal("500.00")


def test_real_net_worth_of_an_empty_ledger_is_decimal_zero_not_int() -> None:
    """No `(institution, account)` group at all (an empty `entries`) must still return
    `Decimal("0")`, not the bare `int` `0` a dropped `start=Decimal("0")` default on the
    outer `sum()` would silently produce -- indistinguishable by `==` but not by `isinstance`.
    """
    result = real_net_worth([], date(2023, 1, 1))
    assert result == Decimal("0")
    assert isinstance(result, Decimal)


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


# ---------------------------------------------------------------------------
# R-9.14: open positions at FIFO purchase cost
# ---------------------------------------------------------------------------


def _trade(row: int, quantity: str, cash: str, day: int = 10, **extra: object) -> LedgerEntry:
    q = Decimal(quantity)
    return make_entry(
        entry_id=f"t{row}",
        movement_type=MovementType.BUY if q > 0 else MovementType.SELL,
        account="positions",
        asset="X1",
        quantity=q,
        amount_eur=Decimal(cash),
        cash_effect_eur=Decimal(cash),
        is_external_flow=None,
        source_row=row,
        file_sequence=row,
        date=date(2023, 3, day),
        **extra,
    )


def test_a_sale_consumes_the_oldest_lots_first() -> None:
    """Bought 10 at 100 then 10 at 300; selling 15 empties the first lot and half the second:
    what is left costs 150, not the 225 an average-cost rule would give."""
    trades = [_trade(2, "10", "-1000"), _trade(3, "10", "-3000"), _trade(4, "-15", "2500")]
    assert positions_at_cost(trades, date(2023, 3, 10)) == Decimal("1500.000000")
    # cash -1000 - 3000 + 2500 = -1500, plus 1500 still held at cost: the 15 sold for exactly
    # what they cost (1000 + 1500), so nothing was gained or lost.
    assert real_net_worth(trades, date(2023, 3, 10)) == Decimal("0")


def test_a_partly_sold_lot_keeps_the_exact_remainder_of_its_cost() -> None:
    """1/3 of a 100-cost lot is 33.333333 (6 decimals); the lot keeps 66.666667, and selling
    the rest releases exactly that -- nothing is lost to rounding."""
    trades = [_trade(2, "3", "-100"), _trade(3, "-1", "40")]
    assert positions_at_cost(trades, date(2023, 3, 10)) == Decimal("66.666667")
    everything = [*trades, _trade(4, "-2", "80")]
    assert positions_at_cost(everything, date(2023, 3, 10)) == Decimal("0")


def test_lots_follow_ledger_order_and_the_as_of_date() -> None:
    later_buy = _trade(3, "1", "-50", day=20)
    trades = [later_buy, _trade(2, "1", "-10", day=5), _trade(4, "-1", "12", day=25)]
    assert positions_at_cost(trades, date(2023, 3, 19)) == Decimal("10")
    assert positions_at_cost(trades, date(2023, 3, 25)) == Decimal("50")


def test_lots_are_kept_per_asset() -> None:
    other = _trade(3, "-1", "20")
    other = make_entry(**{**_fields(other), "asset": "X2", "quantity": Decimal("-1")})
    trades = [_trade(2, "1", "-10"), _trade(4, "1", "-30")]
    assert positions_at_cost([*trades, other], date(2023, 3, 10)) == Decimal("40")


def _fields(entry: LedgerEntry) -> dict[str, object]:
    import dataclasses

    return {f.name: getattr(entry, f.name) for f in dataclasses.fields(entry)}


def test_technical_adjustments_never_move_lots() -> None:
    migration_out = make_entry(
        entry_id="m1",
        movement_type=MovementType.TECHNICAL_ADJUSTMENT,
        account="positions",
        asset="X1",
        quantity=Decimal("-1"),
        amount_eur=Decimal("0"),
        cash_effect_eur=Decimal("0"),
        is_external_flow=None,
        source_row=3,
    )
    trades = [_trade(2, "1", "-10"), migration_out]
    assert positions_at_cost(trades, date(2023, 3, 10)) == Decimal("10")
    assert cost_basis_warnings(trades) == ()


def test_selling_units_never_bought_in_the_files_warns_once_and_values_nothing() -> None:
    trades = [_trade(2, "1", "-10"), _trade(3, "-3", "45")]
    assert positions_at_cost(trades, date(2023, 3, 10)) == Decimal("0")
    assert cost_basis_warnings(trades) == (
        Warning(
            message=(
                "test.csv:3: X1 -- 2 units leave the account but no earlier purchase of them "
                "is in the supplied files, so their cost is unknown; import the older "
                "statement that bought them"
            ),
            source_file="test.csv",
            source_row=3,
            rule="R-9.14",
        ),
    )


def test_cost_basis_warnings_on_an_empty_ledger() -> None:
    assert cost_basis_warnings([]) == ()


def test_positions_at_cost_of_nothing_is_a_decimal_zero() -> None:
    result = positions_at_cost([make_entry()], date(2023, 3, 10))
    assert (result, type(result)) == (Decimal("0"), Decimal)


def test_each_period_reports_its_positions_at_cost() -> None:
    periods = compute_section1([make_entry(), _trade(3, "2", "-40", day=12)])
    assert [p.positions_at_cost for p in periods] == [Decimal("40")]
    assert periods[0].real_net_worth == Decimal("100.00")


# ---------------------------------------------------------------------------
# R-9.15: balances already held when an account's first statement starts
# ---------------------------------------------------------------------------


def _bank_row(row: int, cash: str, balance: str, day: int, month: int = 5) -> LedgerEntry:
    return make_entry(
        entry_id=f"b{row}",
        institution="bank_es",
        account="current_account",
        movement_type=MovementType.EXPENSE,
        amount_eur=Decimal(cash),
        cash_effect_eur=Decimal(cash),
        declared_balance=Decimal(balance),
        source_file="bank.xlsx",
        source_row=row,
        file_sequence=-row,
        date=date(2023, month, day),
    )


def test_the_bank_fixture_already_held_1000_before_its_first_row() -> None:
    entries = bank_xlsx.parse(BANK_XLSX).entries
    assert opening_balances(entries) == {
        ("bank_es", "current_account"): (date(2027, 3, 1), Decimal("1000.00"))
    }


def test_accounts_without_a_declared_balance_have_no_opening_balance() -> None:
    assert opening_balances(broker_csv.parse(BROKER_CSV).entries) == {}


def test_opening_balance_uses_the_first_declared_balance_and_every_move_up_to_it() -> None:
    """Rows 3 and 2 on the same day: the earlier one (row 3, later in the file) is the
    baseline; 5000 - (-10) = 5010 was already there."""
    rows = [_bank_row(2, "-20", "4980", day=4), _bank_row(3, "-10", "5000", day=4)]
    assert opening_balances(rows) == {
        ("bank_es", "current_account"): (date(2023, 5, 4), Decimal("5010"))
    }


def test_a_later_account_brings_its_opening_balance_as_savings_not_as_return() -> None:
    """The broker starts in March; the bank's first statement row is in May and shows it
    already held 5010. That money is savings from before the files, so the return stays 0."""
    deposit = make_entry(date=date(2023, 3, 10), cash_effect_eur=Decimal("100.00"))
    bank = [_bank_row(2, "-10", "5000", day=4)]
    periods = compute_section1([deposit, *bank])
    may = periods[2]
    assert may.opening_balances == Decimal("5010")
    assert may.savings_only == Decimal("100.00") + Decimal("5010") + Decimal("-10")
    assert may.gap == Decimal("0")
    assert [p.opening_balances for p in periods[:2]] == [Decimal("0"), Decimal("0")]


def test_an_account_starting_in_the_first_month_is_not_counted_twice() -> None:
    bank = [_bank_row(2, "-10", "5000", day=4, month=3)]
    (march,) = compute_section1([make_entry(), *bank])
    assert march.opening_balances == Decimal("0")
    assert march.savings_only == march.real_net_worth


def test_a_zero_quantity_row_opens_no_lot() -> None:
    zero = make_entry(
        entry_id="r3",
        movement_type=MovementType.REDEMPTION,
        account="positions",
        asset="X1",
        quantity=Decimal("0"),
        amount_eur=Decimal("5"),
        cash_effect_eur=Decimal("5"),
        is_external_flow=None,
        source_row=3,
    )
    assert positions_at_cost([_trade(2, "1", "-10"), zero], date(2023, 3, 10)) == Decimal("10")


def test_selling_exactly_a_whole_lot_releases_all_of_its_cost() -> None:
    """A lot sold in full goes as a whole, even when its cost has more decimals than the
    6-decimal split would keep."""
    trades = [_trade(2, "3", "-100.0000004"), _trade(3, "-3", "120")]
    assert positions_at_cost(trades, date(2023, 3, 10)) == Decimal("0")


def test_a_partial_split_rounds_half_up() -> None:
    """Half of 0.000001 is 0.0000005: rounded half-up the half sold takes 0.000001, leaving 0
    (half-even would leave 0.000001 behind)."""
    trades = [_trade(2, "2", "-0.000001"), _trade(3, "-1", "1")]
    assert positions_at_cost(trades, date(2023, 3, 10)) == Decimal("0.000000")


def test_a_fully_matched_sale_warns_nothing() -> None:
    assert cost_basis_warnings([_trade(2, "2", "-10"), _trade(3, "-2", "12")]) == ()


def test_less_than_one_unmatched_unit_still_warns() -> None:
    (warning,) = cost_basis_warnings([_trade(2, "1", "-10"), _trade(3, "-1.5", "15")])
    assert "0.5 units leave the account" in warning.message
