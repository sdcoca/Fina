"""Section 1 chart renderer (spec section 10).

Implements: R-10.1..R-10.5.

Ported from the project owner's approved mock (Q-G resolution --
`docs/design/section1-approved-mock.html`, iterated on directly with the project owner and
saved into the repo for exactly this reason after WP-9 could not reach it): colours,
typography (IBM Plex Serif/Sans/Mono), spacing, the click-to-reveal tooltip with its own close
button, a translucent (plain alpha compositing -- never a CSS background-blur property, per
R-10.2b) tooltip background, direct
end-of-line value labels, and a two-line series legend with no band-colour chips are all
carried over from that file. WP-9's original version here was an independently-designed
placeholder built because the mock was unreachable from that session's environment (Q-G); this
is the "port it, do not redesign it" pass implementation-plan.md's WP-9 entry always called for.

One deliberate deviation from the mock, flagged per Q-G's own instruction to record any case
where the mock's design actually conflicts with a literal rule rather than silently picking a
side (logged as open question Q-I): the mock loads "IBM Plex *" from `fonts.googleapis.com` via
a `<link>` tag. This module's long-standing contract (R-10.1's rationale: "suitable for a
headless browser to screenshot or export to PDF") is a **self-contained** document with no
external assets -- `tests/browser_support.py` documents that outbound network access to any CDN
is blocked in this project's own environment, and a PDF/screenshot pipeline that silently
degrades (or stalls) without network access on every future run is a determinism risk (R-1.20)
not worth taking for a cosmetic font. The font-family *declarations* are ported verbatim
(`"IBM Plex Serif"` / `"IBM Plex Sans"` / `"IBM Plex Mono"`, each with the mock's own fallback
stack, so the page still reads correctly wherever the real faces are installed); the `<link>`
tag itself is not.

R-10.4 ("the renderer MUST NOT recompute any financial figure") is enforced structurally, not
just by convention: this module never imports or uses `decimal.Decimal` at all (T-700 greps
for it), so it is structurally incapable of computing a new financial figure from another --
`gap`, `real_net_worth`, `savings_only` and `savings_flow` arrive as already-rounded display
*strings* (`ChartRow`, R-1.5's rounding-for-display already applied by whoever builds a
`ChartRow` -- see `to_chart_rows` in `fina.render.prepare`) and are placed on the page verbatim;
the only numbers this module computes itself are pixel coordinates (`ChartRow`'s `*_position`
floats, unavoidable for any renderer) and small presentation-only string transforms (an axis
tick rounded to the nearest thousand, a "+"/"-" sign prefix on the headline gap) -- neither is a
financial figure in its own right.
"""

from __future__ import annotations

import html
import json
from collections.abc import Sequence
from dataclasses import dataclass

_WIDTH = 960.0
_HEIGHT = 420.0
_PAD_LEFT = 64.0
_PAD_RIGHT = 104.0
_PAD_TOP = 28.0
_PAD_BOTTOM = 40.0
_PLOT_WIDTH = _WIDTH - _PAD_LEFT - _PAD_RIGHT
_PLOT_HEIGHT = _HEIGHT - _PAD_TOP - _PAD_BOTTOM
_AXIS_TICKS = 5


@dataclass(frozen=True)
class ChartRow:
    """One month's worth of already-prepared display data (built by
    `fina.render.prepare.to_chart_rows`, never by this module): every money figure has already
    been rounded for display per R-1.5 and turned into plain `str`/`float`, so this module has
    nothing left to compute except pixel positions and presentation-only string dressing.
    """

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


def _padded_range(values: Sequence[float]) -> tuple[float, float]:
    """The vertical value range both `_build_points` (line/band geometry) and the gridlines
    share: padded by 10% of the span on each side so nothing touches the plot's top/bottom
    edge, one shared definition so the two can never silently disagree on where "the top of
    the chart" is.
    """
    min_value = min(values)
    max_value = max(values)
    pad = (max_value - min_value) * 0.1 or 1.0
    return min_value - pad, max_value + pad


def _build_points(rows: Sequence[ChartRow]) -> list[_Point]:
    reals = [r.real_net_worth_position for r in rows]
    savings = [r.savings_only_position for r in rows]
    min_value, max_value = _padded_range([*reals, *savings])
    points: list[_Point] = []
    for i, row in enumerate(rows):
        points.append(
            _Point(
                x=_x_scale(i, len(rows)),
                real_y=_y_scale(reals[i], min_value, max_value),
                savings_y=_y_scale(savings[i], min_value, max_value),
                gap_value=row.gap_position,
            )
        )
    return points


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _band_polygons(points: Sequence[_Point]) -> list[tuple[str, str]]:
    """Returns `(points_attr, css_class)` pairs, one per band segment. A segment whose two
    endpoints disagree in the sign of `gap` is split into two polygons at the exact linear
    zero-crossing between them (R-10.2), not coloured by whichever endpoint is "closer".
    `band-gain`/`band-loss` are the mock's own class names (Q-G) for what WP-9's original
    design called `band-positive`/`band-negative`; the sign arithmetic is unchanged.
    """
    polygons: list[tuple[str, str]] = []
    for a, b in zip(points, points[1:], strict=False):
        if (a.gap_value >= 0) == (b.gap_value >= 0):
            cls = "band-gain" if a.gap_value >= 0 else "band-loss"
            polygons.append((_quad(a.x, a.real_y, a.savings_y, b.x, b.real_y, b.savings_y), cls))
            continue
        # Real zero-crossing: gap is linear between the two points (both real_net_worth and
        # savings_only are piecewise-linear here), so the fraction where gap == 0 is exact.
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
    """An SVG `<path>` `d` attribute (`"M x,y L x,y L ..."`), matching the mock's own use of
    `<path>` rather than `<polyline>` for the two series lines. Built from `_polyline`'s
    already-formatted coordinate string rather than a second formatting pass, so a path and a
    polyline built from the same points can never drift apart in how a coordinate is rounded.
    """
    return "M " + _polyline(coords).replace(" ", " L ")


def _format_eur(display: str) -> str:
    return f"{display}€"


def _signed_eur(display: str) -> str:
    """The mock's headline gap figure carries an explicit sign (`"+215.000 €"` / `"−3.500
    €"`) rather than relying on the reader to notice a bare minus. `display` (from
    `round_half_up`) already carries its own `-` for a negative value, so only the
    non-negative case needs a prefix added.
    """
    if display.startswith("-"):
        return f"-{_format_eur(display[1:])}"
    return f"+{_format_eur(display)}"


def _thousands_label(value: float) -> str:
    """Y-axis tick label, ported from the mock's own `Math.round(v/1000)+"k"`."""
    return f"{round(value / 1000)}k"


def _grid_ticks(min_value: float, max_value: float) -> list[float]:
    """`_AXIS_TICKS + 1` evenly spaced horizontal gridline values, matching the mock's own
    5-gridline layout.
    """
    span = max_value - min_value
    return [min_value + span * i / _AXIS_TICKS for i in range(_AXIS_TICKS + 1)]


def _tooltip_rows(row: ChartRow) -> list[tuple[str, str]]:
    """The tooltip's ordinary (non-gap) label/value rows, in display order. `real_value`
    carries R-10.5's `cash_only` qualifier inline -- the same figure, travelling with its own
    qualifier, never a separate footer (R-10.2 forbids one).
    """
    real_value = _format_eur(row.real_net_worth_display)
    if row.completeness == "cash_only":
        real_value = f"{real_value} ({row.completeness})"
    return [
        ("Patrimonio real", real_value),
        ("Solo ahorro", _format_eur(row.savings_only_display)),
        ("Ahorro del mes", _format_eur(row.savings_flow_display)),
    ]


def _tooltip_html(row: ChartRow) -> str:
    """The click-opened tooltip's inner markup (R-10.2): a close button, a month header (with
    a "(parcial)" suffix per R-9.10 and an "a fecha de ..." line), one row per figure, and a
    bordered-off gap row coloured by sign -- ported from the approved mock's own `showFor()`
    (`docs/design/section1-approved-mock.html`), built server-side here from real `ChartRow`
    data instead of the mock's hardcoded sample array (R-10.4: this module never computes a
    new financial figure, only arranges already-computed ones).
    """
    month = html.escape(row.month_label)
    if row.is_partial:
        month = f"{month} (parcial)"
    as_of = html.escape(row.as_of)
    rows_html = "".join(
        f'<div class="t-row"><span class="lab">{html.escape(label)}</span>'
        f"<span>{html.escape(value)}</span></div>"
        for label, value in _tooltip_rows(row)
    )
    gap_class = "loss" if row.gap_display.startswith("-") else "gain"
    gap_value = html.escape(_signed_eur(row.gap_display))
    return (
        '<button type="button" class="tooltip-close" aria-label="Cerrar">×</button>'
        f'<div class="t-month">{month}</div>'
        f'<div class="t-asof">a fecha de {as_of}</div>'
        f"{rows_html}"
        f'<div class="t-row t-gap {gap_class}"><span class="lab">Hueco</span>'
        f"<span>{gap_value}</span></div>"
    )


def _points_payload(rows: Sequence[ChartRow], points: Sequence[_Point]) -> str:
    """The per-point data the page's own script needs to open the right tooltip at the right
    position on click -- built once, server-side, from already-computed `ChartRow`/`_Point`
    data (R-10.4), then embedded as a JSON array literal. `</script` is escaped so a
    month label or display string can never prematurely close the surrounding `<script>` tag
    (the classic script-injection gotcha: the HTML parser tokenizes `</script` before the JS
    inside it is ever parsed, so this must happen regardless of `json.dumps`'s own, separate,
    string-literal escaping).
    """
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


def render_section1_chart(rows: Sequence[ChartRow]) -> str:
    """R-10.1: renders the Section 1 series as a standalone HTML/SVG document (no external
    assets), suitable for a headless browser to screenshot or export to PDF.
    """
    if not rows:
        return _EMPTY_DOCUMENT

    points = _build_points(rows)
    reals = [r.real_net_worth_position for r in rows]
    savings = [r.savings_only_position for r in rows]
    min_value, max_value = _padded_range([*reals, *savings])

    band_svg = "\n".join(
        f'<polygon class="{cls}" points="{pts}"></polygon>' for pts, cls in _band_polygons(points)
    )
    ahorro_path = _path_d([(p.x, p.savings_y) for p in points])
    real_path = _path_d([(p.x, p.real_y) for p in points])

    ticks = _grid_ticks(min_value, max_value)
    grid_svg = "\n".join(_grid_line_svg(value, min_value, max_value) for value in ticks)
    baseline_y = _PAD_TOP + _PLOT_HEIGHT
    grid_svg += (
        f'\n<line class="baseline" x1="{_PAD_LEFT:.2f}" x2="{_WIDTH - _PAD_RIGHT:.2f}" '
        f'y1="{baseline_y:.2f}" y2="{baseline_y:.2f}"></line>'
    )

    row_points = list(zip(rows, points, strict=True))
    x_labels_svg = "\n".join(
        _x_label_svg(row, point, i, len(rows)) for i, (row, point) in enumerate(row_points)
    )

    last_point = points[-1]
    last_row = rows[-1]
    real_end_label = _format_eur(last_row.real_net_worth_display)
    if last_row.completeness == "cash_only":
        real_end_label = f"{real_end_label} ({last_row.completeness})"
    label_x = last_point.x + 8
    end_labels_svg = (
        f'<text class="end-label real" x="{label_x:.2f}" y="{last_point.real_y + 4:.2f}">'
        f"{html.escape(real_end_label)}</text>\n"
        f'<text class="end-label ahorro" x="{label_x:.2f}" y="{last_point.savings_y + 4:.2f}">'
        f"{html.escape(_format_eur(last_row.savings_only_display))}</text>"
    )

    points_payload = _points_payload(rows, points)
    gap_headline = html.escape(_signed_eur(last_row.gap_display))
    last_index = len(rows) - 1

    return _DOCUMENT_TEMPLATE.format(
        width=_WIDTH,
        height=_HEIGHT,
        pad_left=_PAD_LEFT,
        pad_top=_PAD_TOP,
        plot_width=_PLOT_WIDTH,
        plot_height=_PLOT_HEIGHT,
        gap_headline=gap_headline,
        grid_svg=grid_svg,
        x_labels_svg=x_labels_svg,
        band_svg=band_svg,
        ahorro_path=ahorro_path,
        real_path=real_path,
        end_labels_svg=end_labels_svg,
        points_payload=points_payload,
        last_index=last_index,
    )


def _grid_line_svg(value: float, min_value: float, max_value: float) -> str:
    y = _y_scale(value, min_value, max_value)
    label = html.escape(_thousands_label(value))
    return (
        f'<line class="grid-line" x1="{_PAD_LEFT:.2f}" x2="{_WIDTH - _PAD_RIGHT:.2f}" '
        f'y1="{y:.2f}" y2="{y:.2f}"></line>\n'
        f'<text class="axis-label" x="{_PAD_LEFT - 10:.2f}" y="{y + 4:.2f}" text-anchor="end">'
        f"{label}</text>"
    )


def _x_label_svg(row: ChartRow, point: _Point, index: int, count: int) -> str:
    """Every other month's label, plus always the last one, to avoid crowding at narrow
    (mobile) widths -- ported from the mock's own `data.forEach` skip rule.
    """
    if index % 2 != 0 and index != count - 1:
        return ""
    label = html.escape(row.month_label)
    return (
        f'<text class="axis-label" x="{point.x:.2f}" y="{_HEIGHT - _PAD_BOTTOM + 18:.2f}" '
        f'text-anchor="middle">{label}</text>'
    )


_EMPTY_DOCUMENT = """<!doctype html>
<html><head><meta charset="utf-8"><title>Section 1</title></head>
<body><p>No hay datos para mostrar.</p></body></html>
"""

_DOCUMENT_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Puente patrimonial</title>
<style>
:root {{
  --page: #f6f5f1;
  --surface: #fcfcfb;
  --ink: #14140f;
  --ink-2: #55534a;
  --muted: #8b897f;
  --grid: #e3e1d7;
  --border: rgba(11, 11, 11, 0.10);
  --line-real: #2a78d6;
  --line-ahorro: #8b897f;
  --gain-fill: rgba(42, 120, 214, 0.14);
  --gain-line: #2a78d6;
  --loss-fill: rgba(227, 73, 72, 0.16);
  --loss-line: #e34948;
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
    --border: rgba(255, 255, 255, 0.12);
    --line-real: #3987e5;
    --line-ahorro: #898781;
    --gain-fill: rgba(57, 135, 229, 0.18);
    --gain-line: #3987e5;
    --loss-fill: rgba(230, 103, 103, 0.20);
    --loss-line: #e66767;
    --tooltip-bg: rgba(95, 93, 86, 0.34);
  }}
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: var(--page); color: var(--ink); }}
body {{
  font-family: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  padding: 20px 14px 28px;
}}
.wrap {{ max-width: 980px; margin: 0 auto; }}
.card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 20px 18px 16px;
}}
.eyebrow {{
  font-family: "IBM Plex Mono", monospace;
  font-size: 11.5px;
  letter-spacing: 0.09em;
  color: var(--muted);
  text-transform: uppercase;
  margin-bottom: 6px;
}}
h1 {{
  font-family: "IBM Plex Serif", Georgia, serif;
  font-weight: 600;
  font-size: 22px;
  line-height: 1.2;
  margin: 0 0 4px;
}}
.sub {{ color: var(--ink-2); font-size: 14px; margin: 0; }}
.sub b {{
  font-family: "IBM Plex Mono", monospace;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
  font-weight: 600;
}}
.chart-area {{ position: relative; margin-top: 16px; }}
svg {{ width: 100%; height: auto; display: block; overflow: visible; }}
.axis-label {{ font-family: "IBM Plex Mono", monospace; font-size: 11px; fill: var(--muted); }}
.grid-line {{ stroke: var(--grid); stroke-width: 1; }}
.baseline {{ stroke: var(--muted); stroke-width: 1; }}
.line-real {{
  fill: none;
  stroke: var(--line-real);
  stroke-width: 2.25;
  stroke-linecap: round;
  stroke-linejoin: round;
}}
.line-ahorro {{
  fill: none;
  stroke: var(--line-ahorro);
  stroke-width: 1.75;
  stroke-dasharray: 5 4;
  stroke-linecap: round;
}}
.band-gain {{ fill: var(--gain-fill); }}
.band-loss {{ fill: var(--loss-fill); }}
.crosshair {{ stroke: var(--ink-2); stroke-width: 1; stroke-dasharray: 2 3; opacity: 0; }}
.dot {{ r: 4; stroke: var(--surface); stroke-width: 1.5; opacity: 0; }}
.dot-real {{ fill: var(--line-real); }}
.dot-ahorro {{ fill: var(--line-ahorro); }}
.hit-area {{ fill: transparent; cursor: pointer; }}
.hit-area:focus-visible {{ outline: 2px solid var(--line-real); outline-offset: 2px; }}
.end-label {{
  font-family: "IBM Plex Mono", monospace;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}}
.end-label.real {{ fill: var(--line-real); font-weight: 600; }}
.end-label.ahorro {{ fill: var(--ink-2); }}
.legend {{
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  margin-top: 12px;
  font-size: 12.5px;
  color: var(--ink-2);
}}
.legend-item {{ display: flex; align-items: center; gap: 7px; }}
.swatch {{ width: 18px; height: 0; border-top-width: 2.5px; border-top-style: solid; }}
.swatch.dashed {{ border-top-style: dashed; }}
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
  max-width: 176px;
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
  font-family: "IBM Plex Sans", sans-serif;
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
  border-radius: 4px;
}}
.tooltip-close:hover {{ color: var(--ink); }}
.tooltip .t-month {{
  font-family: "IBM Plex Mono", monospace;
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
  font-family: "IBM Plex Mono", monospace;
  font-variant-numeric: tabular-nums;
  line-height: 1.4;
  padding: 1.5px 0;
  white-space: nowrap;
}}
.tooltip .t-row .lab {{ font-family: "IBM Plex Sans", sans-serif; color: var(--ink-2); }}
.tooltip .t-gap {{
  margin-top: 5px;
  padding-top: 5px;
  border-top: 1px solid var(--border);
  font-weight: 600;
}}
.tooltip .t-gap.gain span:last-child {{ color: var(--gain-line); }}
.tooltip .t-gap.loss span:last-child {{ color: var(--loss-line); }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="eyebrow">Sección 1 · Informe mensual de patrimonio</div>
    <h1>Patrimonio real vs. solo ahorro</h1>
    <p class="sub">Hueco acumulado a cierre: <b>{gap_headline}</b></p>

    <div class="chart-area">
      <svg viewBox="0 0 {width:.0f} {height:.0f}" preserveAspectRatio="xMidYMid meet">
{grid_svg}
{x_labels_svg}
        <g class="band">
{band_svg}
        </g>
        <path class="line-ahorro" d="{ahorro_path}"></path>
        <path class="line-real" d="{real_path}"></path>
{end_labels_svg}
        <line class="crosshair" y1="{pad_top:.2f}" y2="{pad_top:.2f}"></line>
        <circle class="dot dot-ahorro" cx="0" cy="0"></circle>
        <circle class="dot dot-real" cx="0" cy="0"></circle>
        <rect class="hit-area" x="{pad_left:.2f}" y="{pad_top:.2f}" width="{plot_width:.2f}" \
height="{plot_height:.2f}" tabindex="0" role="button" \
aria-label="Ver el detalle de cada mes"></rect>
      </svg>
      <div class="tooltip" id="s1-tooltip" role="dialog" aria-live="polite"></div>
    </div>

    <div class="legend">
      <div class="legend-item">\
<span class="swatch" style="border-color: var(--line-real)"></span>\
Patrimonio neto real</div>
      <div class="legend-item">\
<span class="swatch dashed" style="border-color: var(--line-ahorro)"></span>\
Solo ahorro, sin invertir</div>
    </div>
  </div>
</div>
<script>
(function () {{
  var points = {points_payload};
  var lastIndex = {last_index};
  var svg = document.querySelector("svg");
  var hit = document.querySelector(".hit-area");
  var crosshair = document.querySelector(".crosshair");
  var dotReal = document.querySelector(".dot-real");
  var dotAhorro = document.querySelector(".dot-ahorro");
  var tooltip = document.getElementById("s1-tooltip");
  var chartArea = document.querySelector(".chart-area");
  var selected = null;

  function showFor(i) {{
    var p = points[i];
    crosshair.setAttribute("x1", p.x);
    crosshair.setAttribute("x2", p.x);
    crosshair.style.opacity = 1;
    dotReal.setAttribute("cx", p.x);
    dotReal.setAttribute("cy", p.realY);
    dotReal.style.opacity = 1;
    dotAhorro.setAttribute("cx", p.x);
    dotAhorro.setAttribute("cy", p.savingsY);
    dotAhorro.style.opacity = 1;

    tooltip.innerHTML = p.tooltip;
    tooltip.querySelector(".tooltip-close").addEventListener("click", function (evt) {{
      evt.stopPropagation();
      hideTooltip();
    }});

    var frac = p.x / {width:.0f};
    var tipWidth = 176;
    var left = frac * chartArea.clientWidth - tipWidth / 2;
    left = Math.min(Math.max(left, 0), Math.max(chartArea.clientWidth - tipWidth, 0));
    tooltip.style.left = left + "px";
    tooltip.style.top = "6px";
    tooltip.classList.add("visible");
    hit.setAttribute("aria-label", "Mes seleccionado: " + p.label);
  }}

  function hideTooltip() {{
    crosshair.style.opacity = 0;
    dotReal.style.opacity = 0;
    dotAhorro.style.opacity = 0;
    tooltip.classList.remove("visible");
    hit.setAttribute("aria-label", "Ver el detalle de cada mes");
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
