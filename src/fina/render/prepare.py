"""Bridges section1.py's exact `Decimal` series to the render module's `Decimal`-free
`ChartRow`s (R-10.4).

Deliberately its own small file, inside the `render` package rather than `fina.pipeline`:
G-6's float-ban AST scan (T-902) excludes this whole package as "not a money path" -- it is
the one place a `Decimal` is legitimately turned into both a rounded display `str` (R-1.5) and
a `float` for pixel placement, and keeping that one conversion point inside `render/` (instead
of scattering a hole in the scan's scope through `pipeline.py`, which stays float-free) is what
makes the exclusion narrow and auditable rather than a blanket exemption.
"""

from __future__ import annotations

from collections.abc import Sequence

from fina.money import round_half_up
from fina.render.section1_chart import ChartRow
from fina.section1 import Section1Period


def to_chart_rows(series: Sequence[Section1Period]) -> list[ChartRow]:
    """Rounds every money figure for display exactly once (R-1.5/R-9.11) -- the rest of the
    render package never sees a `Decimal` and therefore cannot recompute one (R-10.4).
    """
    return [
        ChartRow(
            month_label=p.month.strftime("%b %Y"),
            as_of=p.as_of.isoformat(),
            is_partial=p.is_partial,
            completeness=p.completeness,
            real_net_worth_position=float(p.real_net_worth),
            savings_only_position=float(p.savings_only),
            gap_position=float(p.gap),
            real_net_worth_display=str(round_half_up(p.real_net_worth)),
            savings_only_display=str(round_half_up(p.savings_only)),
            gap_display=str(round_half_up(p.gap)),
            savings_flow_display=str(round_half_up(p.savings_flow)),
        )
        for p in series
    ]
