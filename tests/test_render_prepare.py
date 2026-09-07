"""Tests for fina.render.prepare (WP-9): the Decimal -> ChartRow bridge (R-1.5/R-10.4)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from builders import BANK_XLSX
from fina.pipeline import run_pipeline
from fina.render.prepare import to_chart_rows
from fina.section1 import Section1Period


def test_to_chart_rows_rounds_for_display_and_matches_position_values() -> None:
    result = run_pipeline(BANK_XLSX.parent)
    rows = to_chart_rows(result.series)
    assert len(rows) == len(result.series)
    for row, period in zip(rows, result.series, strict=True):
        assert row.month_label == period.month.strftime("%b %Y")
        assert row.as_of == period.as_of.isoformat()
        assert row.is_partial == period.is_partial
        assert row.completeness == period.completeness
        assert row.real_net_worth_position == float(period.real_net_worth)
        assert isinstance(row.real_net_worth_display, str)


def test_to_chart_rows_of_empty_series_is_empty() -> None:
    assert to_chart_rows(()) == []


def test_to_chart_rows_exact_display_values_for_all_four_fields() -> None:
    """Four genuinely distinct `Decimal` values, each pinned to its own exact rounded
    display string -- catches any of the four fields being dropped, swapped with another, or
    replaced by `None`/`str(None)`.
    """
    period = Section1Period(
        month=date(2027, 3, 1),
        as_of=date(2027, 3, 31),
        is_partial=False,
        real_net_worth=Decimal("111.111"),
        completeness="cash_only",
        savings_flow=Decimal("444.444"),
        savings_only=Decimal("222.222"),
        gap=Decimal("333.333"),
    )
    (row,) = to_chart_rows([period])
    assert row.real_net_worth_display == "111.11"
    assert row.savings_only_display == "222.22"
    assert row.gap_display == "333.33"
    assert row.savings_flow_display == "444.44"
