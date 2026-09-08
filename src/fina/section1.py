"""Section 1: the net-worth bridge (spec section 9).

Implements: R-9.1..R-9.11. R-9.12 (the per-asset FIFO cross-check) is D2, not implemented in
this iteration.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as _date
from decimal import Decimal
from typing import Literal

from fina.models import SAVINGS_FLOW_ELIGIBLE_TYPES, LedgerEntry, MovementType
from fina.reconciliation import anchor_at, sort_key

#: R-9.3/R-9.4: no price feed exists yet (D1), so `real_net_worth` is cash-only. Every period
#: this module emits carries this same literal completeness flag until D1 lands.
Completeness = Literal["cash_only"]
_CASH_ONLY: Completeness = "cash_only"

#: R-9.5's `<D3>` branch: these two types can never actually reach `contribution` through a
#: constructed `LedgerEntry` (its own `__post_init__` already refuses to construct one), but
#: `contribution` takes bare scalars rather than a `LedgerEntry` specifically so this branch
#: stays independently testable -- mirroring `compute_cash_effect`'s identical D3 guard.
_DEFERRED_D3_TYPES = frozenset({MovementType.RSU_VESTING, MovementType.ESPP_PURCHASE})


@dataclass(frozen=True)
class Section1Period:
    """One calendar month (or, for the last one, a partial month) of the net-worth bridge."""

    month: _date
    as_of: _date
    is_partial: bool
    real_net_worth: Decimal
    completeness: Completeness
    savings_flow: Decimal
    savings_only: Decimal
    gap: Decimal


def cash_balance(
    entries: Sequence[LedgerEntry], institution: str, account: str, as_of: _date
) -> Decimal:
    """R-9.1: the account's balance at `as_of`, anchored to `reconciliation.py`'s own R-8.3
    baseline (via `anchor_at`) when one exists for this `(institution, account)` pair --
    `anchor.declared_balance` plus every later entry's `cash_effect_eur` through `as_of`.
    Falls back to raw summation of `cash_effect_eur` from an assumed zero balance -- still
    unverified per R-8.4 -- only when the pair carries no declared balance at all.

    Before this anchoring, this function summed `cash_effect_eur` from zero unconditionally,
    which silently dropped any real balance that predated an account's earliest ingested entry
    (found in the first end-to-end run over both fixtures together -- see R-9.1's rationale in
    the spec and regression tests T-401a/b/c). `reconciliation.py` computes the correct anchor
    already (R-8.3); re-deriving it here independently is exactly how the two modules'
    individually-100%-covered test suites disagreed once combined.
    """
    anchor = anchor_at(entries, institution, account, as_of)
    if anchor is None:
        return sum(
            (
                e.cash_effect_eur
                for e in entries
                if e.institution == institution and e.account == account and e.date <= as_of
            ),
            start=Decimal("0"),
        )
    anchor_entry, anchor_declared_balance = anchor
    anchor_sort_key = sort_key(anchor_entry)
    return anchor_declared_balance + sum(
        (
            e.cash_effect_eur
            for e in entries
            if e.institution == institution
            and e.account == account
            and e.date <= as_of
            and sort_key(e) > anchor_sort_key
        ),
        start=Decimal("0"),
    )


def quantity_held(
    entries: Sequence[LedgerEntry],
    institution: str,
    account: str,
    asset: str,
    as_of: _date,
) -> Decimal:
    """R-9.2: cumulative signed `quantity` for one `(institution, account, asset)`, through
    `as_of` inclusive, excluding `TECHNICAL_ADJUSTMENT` rows (R-2.5: migration rows are kept
    for traceability but never affect holdings).
    """
    return sum(
        (
            e.quantity
            for e in entries
            if e.institution == institution
            and e.account == account
            and e.asset == asset
            and e.movement_type is not MovementType.TECHNICAL_ADJUSTMENT
            and e.quantity is not None
            and e.date <= as_of
        ),
        start=Decimal("0"),
    )


def real_net_worth(entries: Sequence[LedgerEntry], as_of: _date) -> Decimal:
    """R-9.3/R-9.4: `Σ cash_balance(·, t)` over every distinct `(institution, account)` pair
    present in `entries`. This MUST delegate to `cash_balance` per group rather than flatten
    `entries` into one raw sum: once any group is anchored to a non-zero R-8.3 baseline
    (R-9.1), summing every entry's own `cash_effect_eur` from zero across *all* groups no
    longer agrees with summing each group's own anchored balance and adding the groups
    together (it only ever coincided by construction back when every group summed from zero).
    Groups are collected via a plain dict (insertion order = first appearance in `entries`,
    itself deterministic per R-1.20), never a `set`, so iteration order never leaks into the
    result -- moot here since `Decimal` addition is exact and order-independent, but kept
    consistent with this module's own convention (`reconciliation._group_by_account`).
    The `quantity_held × close_price` term is D1 (no price feed exists) and is never added;
    callers MUST treat this figure as cash-only (`completeness == "cash_only"`), never as
    total net worth (R-9.4).
    """
    accounts: dict[tuple[str, str], None] = {}
    for e in entries:
        accounts.setdefault((e.institution, e.account), None)
    return sum(
        (cash_balance(entries, institution, account, as_of) for institution, account in accounts),
        start=Decimal("0"),
    )


def contribution(
    movement_type: MovementType,
    cash_effect_eur: Decimal,
    is_external_flow: bool | None,
) -> Decimal:
    """R-9.5's per-entry `savings_flow` contribution formula, exhaustive over every
    `MovementType` member (proven behaviourally by T-404's 13-way parametrization, the same
    convention `compute_cash_effect` uses -- see its own docstring for why this is a plain
    `if` chain and not a `match`/`case` with a wildcard).
    """
    if movement_type in _DEFERRED_D3_TYPES:
        raise NotImplementedError(
            f"{movement_type.value} is deferred (D3): no employee-plan adapter exists "
            "in this iteration."
        )
    if movement_type in SAVINGS_FLOW_ELIGIBLE_TYPES and is_external_flow is True:
        return cash_effect_eur
    return Decimal("0")


def savings_flow(entries: Sequence[LedgerEntry], year: int, month: int) -> Decimal:
    """R-9.5/R-9.6: total contribution over every entry dated within one calendar month."""
    return sum(
        (
            contribution(e.movement_type, e.cash_effect_eur, e.is_external_flow)
            for e in entries
            if e.date.year == year and e.date.month == month
        ),
        start=Decimal("0"),
    )


def _month_start(year: int, month: int) -> _date:
    return _date(year, month, 1)


def _month_end(year: int, month: int) -> _date:
    return _date(year, month, calendar.monthrange(year, month)[1])


def _month_ordinal(year: int, month: int) -> int:
    """Months since year 0, so consecutive calendar months are consecutive integers -- turns
    "walk from one month to the next" into plain integer arithmetic with no loop of its own
    to get wrong (a `range()` below is always finite, regardless of what these two ordinals
    turn out to be).
    """
    return year * 12 + (month - 1)


def _months_from(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """Every calendar month from `start` to `end` inclusive, in order (R-9.6: a month with no
    entries still appears in the series).
    """
    start_ordinal = _month_ordinal(*start)
    end_ordinal = _month_ordinal(*end)
    months: list[tuple[int, int]] = []
    for ordinal in range(start_ordinal, end_ordinal + 1):
        year, zero_based_month = divmod(ordinal, 12)
        months.append((year, zero_based_month + 1))
    return months


def compute_section1(entries: Sequence[LedgerEntry]) -> tuple[Section1Period, ...]:
    """R-9.1..R-9.11: the full Section 1 series, one entry per calendar month from the
    earliest entry's month (R-9.7) to the latest entry's month, the last of which may be
    partial (R-9.10). An empty ledger produces an empty series (R-1.18), not an exception.
    """
    if not entries:
        return ()

    dates = [e.date for e in entries]
    earliest, latest = min(dates), max(dates)
    months = _months_from((earliest.year, earliest.month), (latest.year, latest.month))

    periods: list[Section1Period] = []
    previous_savings_only: Decimal | None = None
    for index, (year, month) in enumerate(months):
        is_final = index == len(months) - 1
        month_end = _month_end(year, month)
        # R-9.10: only the final period can be partial, and only when data actually ends
        # before that month's last calendar day.
        as_of = latest if is_final else month_end
        is_partial = is_final and as_of != month_end

        rnw = real_net_worth(entries, as_of)
        flow = savings_flow(entries, year, month)
        # R-9.7 (t0 seeds savings_only from real_net_worth itself) / R-9.8 (the recursion
        # for every later period) are the same assignment, branching only on which applies.
        so = rnw if previous_savings_only is None else previous_savings_only + flow
        previous_savings_only = so

        periods.append(
            Section1Period(
                month=_month_start(year, month),
                as_of=as_of,
                is_partial=is_partial,
                real_net_worth=rnw,
                completeness=_CASH_ONLY,
                savings_flow=flow,
                savings_only=so,
                gap=rnw - so,  # R-9.9.
            )
        )
    return tuple(periods)
