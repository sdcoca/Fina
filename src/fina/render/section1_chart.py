"""Section 1 chart renderer (spec section 10).

Implements: R-10.1..R-10.8.

WP-22a (2026-09-26, the project owner's mock iterations v2-v4, approved at a real 390px phone
width): the chart is the page's summary. It shows three figures as a legend with their latest
value — "Net worth (cash and other assets)", "Total Savings (money into your imported
accounts)" and "Total Return of Investments" (the signed band between the two lines) — above a
plot drawn edge to edge, with the y-axis labels inside the plot and at most five x-axis labels.
English throughout; the words "gap"/"hueco" never appear in displayed text. While open positions are
valued at purchase cost (R-9.4/R-9.14) a note says so in plain words.

Carried over unchanged from the approved Q-G port: the band coloured by the sign of the return
and split at the exact linear zero-crossing (R-10.2), the click-only tooltip with its own close
control and plain-alpha translucency (R-10.2/R-10.2b), token-defined themes (R-10.3), and the
Q-J touch/focus fixes (R-10.7/R-10.8, see the CSS comments at `.hit-area`).

R-10.4 ("the renderer MUST NOT recompute any financial figure") is enforced structurally: this
module never imports `decimal.Decimal` (T-700 greps for it). Every figure arrives as an
already-rounded display string (`ChartRow`, built by `fina.render.prepare.to_chart_rows`); the
only transforms here are presentation-only: pixel coordinates, thousands separators and a
sign prefix on a display string, axis tick values, and a date written out in words.
"""

from __future__ import annotations

import html
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass

#: A phone-width canvas (CLAUDE.md rule 19): drawn at 390 units wide it maps ~1:1 onto a real
#: 390px screen, so 11px text stays 11px instead of shrinking with a desktop-sized viewBox.
_WIDTH = 390.0
_HEIGHT = 250.0
_PAD_LEFT = 12.0
_PAD_RIGHT = 12.0
#: Room above the top gridline for its own label, which sits just above its line.
_PAD_TOP = 20.0
_PAD_BOTTOM = 26.0
_PLOT_WIDTH = _WIDTH - _PAD_LEFT - _PAD_RIGHT
_PLOT_HEIGHT = _HEIGHT - _PAD_TOP - _PAD_BOTTOM
#: At most this many y-axis intervals and x-axis labels: more is unreadable at 390px.
_MAX_Y_INTERVALS = 5
_MAX_X_LABELS = 5
_NICE_STEPS = (1.0, 2.0, 2.5, 5.0)
_MONTH_STEPS = (1, 2, 3, 6, 12, 24, 60, 120)
_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

POSITIONS_AT_COST_NOTE = (
    "Asset prices are not updated to today's value: investments are shown at what you paid "
    "for them."
)


@dataclass(frozen=True)
class ChartRow:
    """One month's worth of already-prepared display data (built by
    `fina.render.prepare.to_chart_rows`, never by this module): every money figure has already
    been rounded for display per R-1.5 and turned into plain `str`/`float`, so this module has
    nothing left to compute except pixel positions and presentation-only string dressing.
    """

    month: str  # "YYYY-MM"
    month_label: str
    as_of: str
    is_partial: bool
    completeness: str
    real_net_worth_position: float
    savings_only_position: float
    gap_position: float
    real_net_worth_display: str
    savings_only_display: str
    gap_display: str
    savings_flow_display: str
    positions_at_cost_display: str
    opening_balances_display: str


@dataclass(frozen=True)
class _Point:
    x: float
    real_y: float
    savings_y: float
    gap_value: float


def _x_scale(index: int, count: int) -> float:
    if count <= 1:
        return _PAD_LEFT + _PLOT_WIDTH / 2
    return _PAD_LEFT + (_PLOT_WIDTH * index) / (count - 1)


def _y_scale(value: float, min_value: float, max_value: float) -> float:
    span = max_value - min_value
    if span == 0:
        return _PAD_TOP + _PLOT_HEIGHT / 2
    fraction = (value - min_value) / span
    return _PAD_TOP + _PLOT_HEIGHT * (1 - fraction)


def _nice_step(span: float) -> float:
    """The smallest 1/2/2.5/5 × 10ⁿ step that splits `span` into at most `_MAX_Y_INTERVALS`
    intervals -- round tick values a reader can take in at a glance."""
    magnitude = 10.0 ** math.floor(math.log10(span / _MAX_Y_INTERVALS))
    # 10 × magnitude always qualifies (magnitude ≥ span / (10 × intervals) by construction),
    # so `next` always finds one.
    return next(
        factor * magnitude
        for factor in (*_NICE_STEPS, 10.0)
        if span / (factor * magnitude) <= _MAX_Y_INTERVALS
    )


def _axis_range(values: Sequence[float]) -> tuple[float, float, float]:
    """`(min, max, step)` of the y axis: the data's range widened to whole steps, so every
    gridline sits on a round value and the lines never touch the plot's edges. A flat series
    gets a one-unit range around its value."""
    low, high = min(values), max(values)
    if high == low:
        low, high = low - 1.0, high + 1.0
    step = _nice_step(high - low)
    return math.floor(low / step) * step, math.ceil(high / step) * step, step


def _build_points(rows: Sequence[ChartRow]) -> list[_Point]:
    reals = [r.real_net_worth_position for r in rows]
    savings = [r.savings_only_position for r in rows]
    min_value, max_value, _step = _axis_range([*reals, *savings])
    return [
        _Point(
            x=_x_scale(i, len(rows)),
            real_y=_y_scale(reals[i], min_value, max_value),
            savings_y=_y_scale(savings[i], min_value, max_value),
            gap_value=row.gap_position,
        )
        for i, row in enumerate(rows)
    ]


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _band_polygons(points: Sequence[_Point]) -> list[tuple[str, str]]:
    """Returns `(points_attr, css_class)` pairs, one per band segment. A segment whose two
    endpoints disagree in the sign of the return is split into two polygons at the exact linear
    zero-crossing between them (R-10.2), not coloured by whichever endpoint is "closer".
    """
    polygons: list[tuple[str, str]] = []
    for a, b in zip(points, points[1:], strict=False):
        if (a.gap_value >= 0) == (b.gap_value >= 0):
            cls = "band-gain" if a.gap_value >= 0 else "band-loss"
            polygons.append((_quad(a.x, a.real_y, a.savings_y, b.x, b.real_y, b.savings_y), cls))
            continue
        # Real zero-crossing: the return is linear between the two points (both lines are
        # piecewise-linear here), so the fraction where it is zero is exact.
        t = a.gap_value / (a.gap_value - b.gap_value)
        cx = _lerp(a.x, b.x, t)
        c_real_y = _lerp(a.real_y, b.real_y, t)
        c_savings_y = _lerp(a.savings_y, b.savings_y, t)
        cls_a = "band-gain" if a.gap_value >= 0 else "band-loss"
        cls_b = "band-gain" if b.gap_value >= 0 else "band-loss"
        polygons.append((_quad(a.x, a.real_y, a.savings_y, cx, c_real_y, c_savings_y), cls_a))
        polygons.append((_quad(cx, c_real_y, c_savings_y, b.x, b.real_y, b.savings_y), cls_b))
    return polygons


def _quad(x1: float, y1a: float, y1b: float, x2: float, y2a: float, y2b: float) -> str:
    return f"{x1:.2f},{y1a:.2f} {x2:.2f},{y2a:.2f} {x2:.2f},{y2b:.2f} {x1:.2f},{y1b:.2f}"


def _polyline(coords: Sequence[tuple[float, float]]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in coords)


def _path_d(coords: Sequence[tuple[float, float]]) -> str:
    """An SVG `<path>` `d` attribute (`"M x,y L x,y L ..."`), built from `_polyline`'s
    already-formatted coordinates so the two can never round a coordinate differently."""
    return "M " + _polyline(coords).replace(" ", " L ")


def _format_eur(display: str) -> str:
    """`"-28121.57"` -> `"-28,121.57 €"`: thousands separators on an already-rounded display
    string (a presentation transform, R-10.4 -- no number is parsed or recomputed)."""
    sign = "-" if display.startswith("-") else ""
    whole, _, cents = display.removeprefix("-").partition(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    decimals = f".{cents}" if cents else ""
    return f"{sign}{','.join(groups)}{decimals} €"


def _signed_eur(display: str) -> str:
    """A return always carries its sign (`"+11,437.82 €"` / `"-3,500.00 €"`): `display`
    already carries its own `-`, so only the non-negative case gains a `+`."""
    formatted = _format_eur(display)
    return formatted if display.startswith("-") else f"+{formatted}"


def _is_zero(display: str) -> bool:
    return set(display.removeprefix("-")) <= {"0", "."}


def _long_date(iso: str) -> str:
    """`"2026-09-24"` -> `"24 Sep 2026"`."""
    year, month, day = iso.split("-")
    return f"{int(day)} {_MONTH_NAMES[int(month) - 1]} {year}"


def _axis_label(value: float, step: float) -> str:
    """Y-axis tick label: thousands as `k` once the step is at least 1,000 (`"25k"`,
    `"2.5k"`), plain otherwise."""
    if step >= 1000:
        return f"{value / 1000:g}k"
    return f"{value:g}"


def _y_ticks(min_value: float, max_value: float, step: float) -> list[float]:
    count = round((max_value - min_value) / step)
    return [min_value + step * i for i in range(count + 1)]


def _month_ordinal(month: str) -> int:
    year, number = month.split("-")
    return int(year) * 12 + int(number) - 1


def _x_label_step(rows: Sequence[ChartRow]) -> int:
    """The smallest month step (1, 2, 3, 6, 12... months) that keeps at most `_MAX_X_LABELS`
    labels across the series."""
    first, last = _month_ordinal(rows[0].month), _month_ordinal(rows[-1].month)
    for step in _MONTH_STEPS:
        if (last // step) - ((first - 1) // step) <= _MAX_X_LABELS:
            return step
    return _MONTH_STEPS[-1]


def _x_label_text(month: str, step: int) -> str:
    """A yearly (or longer) step labels the year alone; a shorter one the month too."""
    year, number = month.split("-")
    if step >= 12:
        return year
    return f"{_MONTH_NAMES[int(number) - 1]} {year[2:]}"


#: A label centred on a point this close to either edge would be cut off by the screen edge.
_EDGE_ZONE = 24.0


def _x_anchor(x: float) -> str:
    """Centred under its tick, except at the edges, where it grows inwards instead."""
    if x < _PAD_LEFT + _EDGE_ZONE:
        return "start"
    if x > _WIDTH - _PAD_RIGHT - _EDGE_ZONE:
        return "end"
    return "middle"


def _x_labels_svg(rows: Sequence[ChartRow], points: Sequence[_Point]) -> str:
    step = _x_label_step(rows)
    y = _HEIGHT - _PAD_BOTTOM
    parts = [
        f'<line class="x-tick" x1="{point.x:.2f}" x2="{point.x:.2f}" '
        f'y1="{y:.2f}" y2="{y + 4:.2f}"></line>\n'
        f'<text class="axis-label" x="{point.x:.2f}" y="{y + 18:.2f}" '
        f'text-anchor="{_x_anchor(point.x)}">'
        f"{html.escape(_x_label_text(row.month, step))}</text>"
        for row, point in zip(rows, points, strict=True)
        if _month_ordinal(row.month) % step == 0
    ]
    return "\n".join(parts)


def _grid_svg(min_value: float, max_value: float, step: float) -> tuple[str, str]:
    """`(gridlines, labels)`: the labels sit inside the plot, just above their line, and are
    drawn after the data with a halo so a line crossing them never hides them."""
    lines: list[str] = []
    labels: list[str] = []
    for value in _y_ticks(min_value, max_value, step):
        y = _y_scale(value, min_value, max_value)
        cls = "baseline" if value == min_value else "grid-line"
        lines.append(
            f'<line class="{cls}" x1="{_PAD_LEFT:.2f}" x2="{_WIDTH - _PAD_RIGHT:.2f}" '
            f'y1="{y:.2f}" y2="{y:.2f}"></line>'
        )
        labels.append(
            f'<text class="axis-label y-label" x="{_PAD_LEFT:.2f}" y="{y - 4:.2f}">'
            f"{html.escape(_axis_label(value, step))}</text>"
        )
    return "\n".join(lines), "\n".join(labels)


def _tooltip_rows(row: ChartRow) -> list[tuple[str, str]]:
    """The tooltip's ordinary label/value rows, in display order; the balances accounts
    already held when their first statement starts (R-9.15) only in the month they arrive."""
    rows = [
        ("Net worth", _format_eur(row.real_net_worth_display)),
        ("Total savings", _format_eur(row.savings_only_display)),
        ("Saved this month", _format_eur(row.savings_flow_display)),
    ]
    if not _is_zero(row.opening_balances_display):
        rows.append(("Balances already held", _format_eur(row.opening_balances_display)))
    return rows


def _tooltip_html(row: ChartRow) -> str:
    """The click-opened tooltip's inner markup (R-10.2): a close button, the month (with
    "(partial)" per R-9.10) and its as-of date, one row per figure, and the signed return row
    coloured by sign."""
    month = html.escape(row.month_label)
    if row.is_partial:
        month = f"{month} (partial)"
    rows_html = "".join(
        f'<div class="t-row"><span class="lab">{html.escape(label)}</span>'
        f"<span>{html.escape(value)}</span></div>"
        for label, value in _tooltip_rows(row)
    )
    sign_class = "loss" if row.gap_display.startswith("-") else "gain"
    return (
        '<button type="button" class="tooltip-close" aria-label="Close">×</button>'
        f'<div class="t-month">{month}</div>'
        f'<div class="t-asof">as of {html.escape(_long_date(row.as_of))}</div>'
        f"{rows_html}"
        f'<div class="t-row t-return {sign_class}"><span class="lab">Total return</span>'
        f"<span>{html.escape(_signed_eur(row.gap_display))}</span></div>"
    )


def _points_payload(rows: Sequence[ChartRow], points: Sequence[_Point]) -> str:
    """The per-point data the page's own script needs to open the right tooltip at the right
    position on click, embedded as a JSON array literal. `</` is escaped so no display string
    can close the surrounding `<script>` tag early."""
    payload = [
        {
            "x": round(point.x, 2),
            "realY": round(point.real_y, 2),
            "savingsY": round(point.savings_y, 2),
            "label": row.month_label,
            "tooltip": _tooltip_html(row),
        }
        for row, point in zip(rows, points, strict=True)
    ]
    return json.dumps(payload).replace("</", "<\\/")


def _legend_html(last: ChartRow) -> str:
    """The three figures, each with its latest value (the owner's labels; the savings one
    names what it counts since every transfer is money in or out, R-3.4)."""
    sign_class = "loss" if last.gap_display.startswith("-") else "gain"
    return (
        '<div class="legend">\n'
        '  <div class="legend-item"><span class="swatch"></span>'
        '<span class="lg-name">Net worth <small>(cash and other assets)</small></span>'
        f'<b class="lg-value">{html.escape(_format_eur(last.real_net_worth_display))}</b>'
        "</div>\n"
        '  <div class="legend-item"><span class="swatch dashed"></span>'
        '<span class="lg-name">Total Savings '
        "<small>(money into your imported accounts)</small></span>"
        f'<b class="lg-value">{html.escape(_format_eur(last.savings_only_display))}</b>'
        "</div>\n"
        f'  <div class="legend-item {sign_class}"><span class="chip"></span>'
        '<span class="lg-name">Total Return of Investments</span>'
        f'<b class="lg-value">{html.escape(_signed_eur(last.gap_display))}</b></div>\n'
        "</div>"
    )


def render_section1_chart(rows: Sequence[ChartRow]) -> str:
    """R-10.1: renders the Section 1 series as a standalone HTML/SVG document (no external
    assets), suitable for a headless browser to screenshot or export to PDF.
    """
    if not rows:
        return _EMPTY_DOCUMENT

    points = _build_points(rows)
    min_value, max_value, step = _axis_range(
        [*(r.real_net_worth_position for r in rows), *(r.savings_only_position for r in rows)]
    )
    grid_svg, y_labels_svg = _grid_svg(min_value, max_value, step)
    band_svg = "\n".join(
        f'<polygon class="{cls}" points="{pts}"></polygon>' for pts, cls in _band_polygons(points)
    )
    last_row, last_point = rows[-1], points[-1]
    date_line = f"Data up to {_long_date(last_row.as_of)}"
    if last_row.is_partial:
        date_line += " · month in progress"
    note = ""
    if not _is_zero(last_row.positions_at_cost_display):
        note = f'<p class="note">{html.escape(POSITIONS_AT_COST_NOTE)}</p>'

    return _DOCUMENT_TEMPLATE.format(
        width=_WIDTH,
        height=_HEIGHT,
        pad_left=_PAD_LEFT,
        pad_top=_PAD_TOP,
        plot_width=_PLOT_WIDTH,
        plot_height=_PLOT_HEIGHT,
        date_line=html.escape(date_line),
        legend_html=_legend_html(last_row),
        grid_svg=grid_svg,
        x_labels_svg=_x_labels_svg(rows, points),
        band_svg=band_svg,
        savings_path=_path_d([(p.x, p.savings_y) for p in points]),
        real_path=_path_d([(p.x, p.real_y) for p in points]),
        y_labels_svg=y_labels_svg,
        end_x=last_point.x,
        end_real_y=last_point.real_y,
        end_savings_y=last_point.savings_y,
        note=note,
        points_payload=_points_payload(rows, points),
        last_index=len(rows) - 1,
    )


_EMPTY_DOCUMENT = """<!doctype html>
<html><head><meta charset="utf-8"><title>Net worth</title></head>
<body><p>No data to show yet.</p></body></html>
"""

_DOCUMENT_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Net worth</title>
<style>
:root {{
  /* The app's page background: the PWA manifest and icons reuse it (PWA-1.2a/1.3.3). */
  --page: #f6f5f1;
  --surface: #fcfcfb;
  --ink: #14140f;
  --ink-2: #55534a;
  --muted: #8b897f;
  --grid: #e3e1d7;
  --border: rgba(11, 11, 11, 0.12);
  --line-real: #2a78d6;
  --line-savings: #8b897f;
  --gain-fill: rgba(42, 120, 214, 0.14);
  --gain-line: #2a78d6;
  --loss-fill: rgba(227, 73, 72, 0.16);
  --loss-line: #e34948;
  --warning: #9a6b12;
  --tooltip-bg: rgba(150, 148, 140, 0.24);
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --page: #0d0d0d;
    --surface: #1a1a19;
    --ink: #ffffff;
    --ink-2: #c3c2b7;
    --muted: #898781;
    --grid: #2c2c2a;
    --border: rgba(255, 255, 255, 0.14);
    --line-real: #3987e5;
    --line-savings: #898781;
    --gain-fill: rgba(57, 135, 229, 0.20);
    --gain-line: #3987e5;
    --loss-fill: rgba(230, 103, 103, 0.22);
    --loss-line: #e66767;
    --warning: #d9a53f;
    --tooltip-bg: rgba(95, 93, 86, 0.34);
  }}
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: var(--surface); color: var(--ink); }}
body {{
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  padding: 14px 0 12px;
}}
.head, .legend, .note {{ padding: 0 12px; }}
h1 {{ font-size: 17px; line-height: 1.25; margin: 0; }}
.sub {{ margin: 2px 0 8px; font-size: 12.5px; color: var(--ink-2); }}
.legend {{
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-bottom: 8px;
  font-size: 13px;
  line-height: 1.3;
}}
.legend-item {{ display: flex; align-items: center; gap: 8px; }}
.lg-name {{ flex: 1 1 auto; min-width: 0; }}
.lg-name small {{ font-size: 11.5px; color: var(--muted); }}
.lg-value {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
.legend-item.gain .lg-value {{ color: var(--gain-line); }}
.legend-item.loss .lg-value {{ color: var(--loss-line); }}
.swatch {{ flex: 0 0 14px; height: 0; border-top: 2px solid var(--line-real); }}
.swatch.dashed {{ border-top: 2px dashed var(--line-savings); }}
.chip {{ flex: 0 0 14px; height: 9px; border-radius: 2px; }}
.gain .chip {{ background: var(--gain-fill); border: 1px solid var(--gain-line); }}
.loss .chip {{ background: var(--loss-fill); border: 1px solid var(--loss-line); }}
.chart-area {{ position: relative; }}
svg {{ width: 100%; height: auto; display: block; }}
.axis-label {{ font-size: 11px; fill: var(--ink-2); font-variant-numeric: tabular-nums; }}
.y-label {{
  paint-order: stroke;
  stroke: var(--surface);
  stroke-width: 3px;
  stroke-linejoin: round;
}}
.grid-line {{ stroke: var(--grid); stroke-width: 1; }}
.baseline, .x-tick {{ stroke: var(--muted); stroke-width: 1; }}
.line-real {{
  fill: none;
  stroke: var(--line-real);
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}}
.line-savings {{
  fill: none;
  stroke: var(--line-savings);
  stroke-width: 2;
  stroke-dasharray: 5 4;
  stroke-linecap: round;
}}
.band-gain {{ fill: var(--gain-fill); }}
.band-loss {{ fill: var(--loss-fill); }}
.end-dot {{ stroke: var(--surface); stroke-width: 2; }}
.end-dot.real, .dot-real {{ fill: var(--line-real); }}
.end-dot.savings, .dot-savings {{ fill: var(--line-savings); }}
.crosshair {{ stroke: var(--ink-2); stroke-width: 1; stroke-dasharray: 2 3; opacity: 0; }}
.dot {{ stroke: var(--surface); stroke-width: 1.5; opacity: 0; }}
.hit-area {{
  fill: transparent;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  touch-action: manipulation;
}}
/* R-10.8 (Q-J correction): a touch tap focuses this element (tabindex="0") and Chromium does
   not always classify that as :focus-visible, painting its own native ring instead. Plain
   :focus loses the ring; :focus-visible keeps it for real keyboard use. See T-712. */
.hit-area:focus {{ outline: none; }}
.hit-area:focus-visible {{ outline: 2px solid var(--line-real); outline-offset: 2px; }}
.note {{ margin: 6px 0 0; font-size: 12px; color: var(--warning); }}
.tooltip {{
  position: absolute;
  pointer-events: none;
  background: var(--tooltip-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 7px 24px 7px 9px;
  font-size: 11.5px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  opacity: 0;
  transition: opacity 0.08s ease;
  /* Kept in sync with the script's own tipWidth below (Q-J). 280px: a worst-case row --
     "Balances already held" beside "-999,999,999.99 €" -- measured to fit (T-710). */
  width: 280px;
  max-width: calc(100% - 16px);
  z-index: 5;
}}
.tooltip.visible {{ pointer-events: auto; opacity: 1; }}
.tooltip-close {{
  position: absolute;
  top: 6px;
  right: 6px;
  width: 20px;
  height: 20px;
  border: none;
  background: transparent;
  color: var(--muted);
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
  border-radius: 4px;
  -webkit-tap-highlight-color: transparent;
  touch-action: manipulation;
}}
.tooltip-close:hover {{ color: var(--ink); }}
.tooltip .t-month {{
  font-size: 11px;
  line-height: 1.3;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--muted);
  white-space: nowrap;
}}
.tooltip .t-asof {{
  font-size: 10.5px;
  line-height: 1.3;
  color: var(--muted);
  margin-bottom: 6px;
  white-space: nowrap;
}}
.tooltip .t-row {{
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-variant-numeric: tabular-nums;
  line-height: 1.4;
  padding: 1.5px 0;
  white-space: nowrap;
}}
.tooltip .t-row .lab {{ color: var(--ink-2); }}
.tooltip .t-return {{
  margin-top: 5px;
  padding-top: 5px;
  border-top: 1px solid var(--border);
  font-weight: 600;
}}
.tooltip .t-return.gain span:last-child {{ color: var(--gain-line); }}
.tooltip .t-return.loss span:last-child {{ color: var(--loss-line); }}
</style>
</head>
<body>
<div class="head">
  <h1>Net worth</h1>
  <p class="sub">{date_line}</p>
</div>
{legend_html}
<div class="chart-area">
  <svg viewBox="0 0 {width:.0f} {height:.0f}" preserveAspectRatio="xMidYMid meet" \
role="img" aria-label="Net worth and total savings by month">
{grid_svg}
{x_labels_svg}
    <g class="band">
{band_svg}
    </g>
    <path class="line-savings" d="{savings_path}"></path>
    <path class="line-real" d="{real_path}"></path>
{y_labels_svg}
    <circle class="end-dot savings" cx="{end_x:.2f}" cy="{end_savings_y:.2f}" r="4"></circle>
    <circle class="end-dot real" cx="{end_x:.2f}" cy="{end_real_y:.2f}" r="4"></circle>
    <line class="crosshair" y1="{pad_top:.2f}" y2="{pad_top:.2f}"></line>
    <circle class="dot dot-savings" cx="0" cy="0" r="4"></circle>
    <circle class="dot dot-real" cx="0" cy="0" r="4"></circle>
    <rect class="hit-area" x="{pad_left:.2f}" y="{pad_top:.2f}" width="{plot_width:.2f}" \
height="{plot_height:.2f}" tabindex="0" role="button" \
aria-label="Show each month's detail"></rect>
  </svg>
  <div class="tooltip" id="s1-tooltip" role="dialog" aria-live="polite"></div>
</div>
{note}
<script>
(function () {{
  var points = {points_payload};
  var lastIndex = {last_index};
  var svg = document.querySelector("svg");
  var hit = document.querySelector(".hit-area");
  var crosshair = document.querySelector(".crosshair");
  var dotReal = document.querySelector(".dot-real");
  var dotSavings = document.querySelector(".dot-savings");
  var tooltip = document.getElementById("s1-tooltip");
  var chartArea = document.querySelector(".chart-area");
  var selected = null;

  function showFor(i) {{
    var p = points[i];
    crosshair.setAttribute("x1", p.x);
    crosshair.setAttribute("x2", p.x);
    crosshair.setAttribute("y2", {pad_top:.2f} + {plot_height:.2f});
    crosshair.style.opacity = 1;
    dotReal.setAttribute("cx", p.x);
    dotReal.setAttribute("cy", p.realY);
    dotReal.style.opacity = 1;
    dotSavings.setAttribute("cx", p.x);
    dotSavings.setAttribute("cy", p.savingsY);
    dotSavings.style.opacity = 1;

    tooltip.innerHTML = p.tooltip;
    tooltip.querySelector(".tooltip-close").addEventListener("click", function (evt) {{
      evt.stopPropagation();
      hideTooltip();
    }});

    var frac = p.x / {width:.0f};
    var tipWidth = Math.min(280, chartArea.clientWidth - 16); // .tooltip's width (Q-J).
    var left = frac * chartArea.clientWidth - tipWidth / 2;
    left = Math.min(Math.max(left, 8), Math.max(chartArea.clientWidth - tipWidth - 8, 0));
    tooltip.style.left = left + "px";
    tooltip.style.top = "6px";
    tooltip.classList.add("visible");
    hit.setAttribute("aria-label", "Selected month: " + p.label);
  }}

  function hideTooltip() {{
    crosshair.style.opacity = 0;
    dotReal.style.opacity = 0;
    dotSavings.style.opacity = 0;
    tooltip.classList.remove("visible");
    hit.setAttribute("aria-label", "Show each month's detail");
    selected = null;
  }}

  function select(i) {{
    i = Math.max(0, Math.min(lastIndex, i));
    if (selected === i) {{
      hideTooltip();
      return;
    }}
    selected = i;
    showFor(i);
  }}

  function indexFromClientX(clientX) {{
    var rect = svg.getBoundingClientRect();
    var scaleX = {width:.0f} / rect.width;
    var svgX = (clientX - rect.left) * scaleX;
    var step = {plot_width:.2f} / Math.max(lastIndex, 1);
    return Math.round((svgX - {pad_left:.2f}) / step);
  }}

  hit.addEventListener("click", function (evt) {{
    evt.stopPropagation();
    select(indexFromClientX(evt.clientX));
  }});

  hit.addEventListener("keydown", function (evt) {{
    if (evt.key === "ArrowRight") {{
      evt.preventDefault();
      select(selected === null ? 0 : selected + 1);
    }} else if (evt.key === "ArrowLeft") {{
      evt.preventDefault();
      select(selected === null ? lastIndex : selected - 1);
    }} else if (evt.key === "Enter" || evt.key === " ") {{
      evt.preventDefault();
      select(selected === null ? 0 : selected);
    }} else if (evt.key === "Escape") {{
      hideTooltip();
    }}
  }});

  document.addEventListener("click", function (evt) {{
    if (selected === null) return;
    if (tooltip.contains(evt.target)) return;
    hideTooltip();
  }});
}})();
</script>
</body>
</html>
"""
