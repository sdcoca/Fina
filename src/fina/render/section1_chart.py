"""Section 1 chart renderer (spec section 10).

Implements: R-10.1..R-10.5.

R-10.4 ("the renderer MUST NOT recompute any financial figure") is enforced structurally, not
just by convention: this module never imports or uses `decimal.Decimal` at all (T-700 greps
for it), so it is structurally incapable of computing a new financial figure from another --
`gap`, `real_net_worth`, `savings_only` and `savings_flow` arrive as already-rounded display
*strings* (`ChartRow`, R-1.5's rounding-for-display already applied by whoever builds a
`ChartRow` -- see `to_chart_rows` in `fina.render.prepare`) and are placed on the page verbatim; the
only numbers this module computes itself are pixel coordinates (`ChartRow`'s `*_position`
floats), which is unavoidable for any renderer and is not a financial figure.

There is no approved visual mock available in this working tree to port (see
docs/plan/open-questions.md Q-G); every rule below is satisfied on its own literal terms,
verified by rendering (not by reading this file), but the exact colours/spacing here are this
implementation's own reasonable design, not a reproduction of a pre-existing approved design.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass

_WIDTH = 800.0
_HEIGHT = 420.0
_PAD_LEFT = 16.0
_PAD_RIGHT = 175.0
_PAD_TOP = 28.0
_PAD_BOTTOM = 40.0
_PLOT_WIDTH = _WIDTH - _PAD_LEFT - _PAD_RIGHT
_PLOT_HEIGHT = _HEIGHT - _PAD_TOP - _PAD_BOTTOM


@dataclass(frozen=True)
class ChartRow:
    """One month's worth of already-prepared display data (built by
    `fina.pipeline.to_chart_rows`, never by this module): every money figure has already been
    rounded for display per R-1.5 and turned into plain `str`/`float`, so this module has
    nothing left to compute except pixel positions.
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


def _build_points(rows: Sequence[ChartRow]) -> list[_Point]:
    reals = [r.real_net_worth_position for r in rows]
    savings = [r.savings_only_position for r in rows]
    all_values = [*reals, *savings]
    min_value = min(all_values)
    max_value = max(all_values)
    # A little vertical breathing room so the lines never touch the plot's top/bottom edge.
    pad = (max_value - min_value) * 0.1 or 1.0
    min_value -= pad
    max_value += pad
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
    """
    polygons: list[tuple[str, str]] = []
    for a, b in zip(points, points[1:], strict=False):
        if (a.gap_value >= 0) == (b.gap_value >= 0):
            cls = "band-positive" if a.gap_value >= 0 else "band-negative"
            polygons.append((_quad(a.x, a.real_y, a.savings_y, b.x, b.real_y, b.savings_y), cls))
            continue
        # Real zero-crossing: gap is linear between the two points (both real_net_worth and
        # savings_only are piecewise-linear here), so the fraction where gap == 0 is exact.
        t = a.gap_value / (a.gap_value - b.gap_value)
        cx = _lerp(a.x, b.x, t)
        c_real_y = _lerp(a.real_y, b.real_y, t)
        c_savings_y = _lerp(a.savings_y, b.savings_y, t)
        cls_a = "band-positive" if a.gap_value >= 0 else "band-negative"
        cls_b = "band-positive" if b.gap_value >= 0 else "band-negative"
        polygons.append((_quad(a.x, a.real_y, a.savings_y, cx, c_real_y, c_savings_y), cls_a))
        polygons.append((_quad(cx, c_real_y, c_savings_y, b.x, b.real_y, b.savings_y), cls_b))
    return polygons


def _quad(x1: float, y1a: float, y1b: float, x2: float, y2a: float, y2b: float) -> str:
    return f"{x1:.2f},{y1a:.2f} {x2:.2f},{y2a:.2f} {x2:.2f},{y2b:.2f} {x1:.2f},{y1b:.2f}"


def _polyline(coords: Sequence[tuple[float, float]]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in coords)


def render_section1_chart(rows: Sequence[ChartRow]) -> str:
    """R-10.1: renders the Section 1 series as a standalone HTML/SVG document (no external
    assets), suitable for a headless browser to screenshot or export to PDF.
    """
    if not rows:
        return _EMPTY_DOCUMENT

    points = _build_points(rows)
    band_polygons = _band_polygons(points)
    real_line = _polyline([(p.x, p.real_y) for p in points])
    savings_line = _polyline([(p.x, p.savings_y) for p in points])

    band_svg = "\n".join(
        f'<polygon class="{cls}" points="{pts}"></polygon>' for pts, cls in band_polygons
    )

    dots_svg = []
    for i, (row, point) in enumerate(zip(rows, points, strict=True)):
        tooltip = _tooltip_payload(row)
        dots_svg.append(
            f'<circle class="hit" cx="{point.x:.2f}" cy="{point.real_y:.2f}" r="10" '
            f'data-index="{i}" data-tooltip="{html.escape(tooltip, quote=True)}" '
            f'tabindex="0" role="button" '
            f'aria-label="{html.escape(row.month_label, quote=True)}"></circle>'
        )
    dots_html = "\n".join(dots_svg)

    last = points[-1]
    last_row = rows[-1]
    # R-10.5: the qualifier travels with the figure itself (a second line on its own value
    # label), never as a separate explanatory footer (R-10.2 forbids one).
    real_label = _format_eur(last_row.real_net_worth_display)
    if last_row.completeness == "cash_only":
        real_label = f"{real_label} ({last_row.completeness})"
    labels_svg = (
        f'<text class="value-label real" x="{last.x + 8:.2f}" y="{last.real_y:.2f}">'
        f"{html.escape(real_label)}</text>"
        f'<text class="value-label savings" x="{last.x + 8:.2f}" y="{last.savings_y:.2f}">'
        f"{_format_eur(last_row.savings_only_display)}</text>"
    )

    return _DOCUMENT_TEMPLATE.format(
        width=_WIDTH,
        height=_HEIGHT,
        real_line=real_line,
        savings_line=savings_line,
        band_svg=band_svg,
        dots_html=dots_html,
        labels_svg=labels_svg,
    )


def _format_eur(display: str) -> str:
    return f"{display}€"


def _tooltip_payload(row: ChartRow) -> str:
    """Each field on its own short, fixed-shape line (label, then a line break, then the
    value) so no single line's rendered width depends on how large the number is -- an
    amount with many digits still never forces that *line* to wrap, only ever adds width
    the tooltip's own `max-width` already budgets for (verified by rendering: T-706).
    """
    partial = " (parcial)" if row.is_partial else ""
    return (
        f"{row.month_label}{partial}\n"
        f"a fecha de {row.as_of}\n"
        f"Patrimonio ({row.completeness})\n{_format_eur(row.real_net_worth_display)}\n"
        f"Solo ahorro\n{_format_eur(row.savings_only_display)}\n"
        f"Diferencia (gap)\n{_format_eur(row.gap_display)}\n"
        f"Ahorro del mes\n{_format_eur(row.savings_flow_display)}"
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
<title>Section 1 — puente patrimonial</title>
<style>
:root {{
  --bg: #ffffff;
  --fg: #1a1a1a;
  --muted: #6b7280;
  --real-line: #1d4ed8;
  --savings-line: #6b7280;
  --band-positive: rgba(16, 163, 74, 0.22);
  --band-negative: rgba(220, 38, 38, 0.22);
  --tooltip-bg: rgba(255, 255, 255, 0.88);
  --tooltip-border: rgba(0, 0, 0, 0.15);
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #12151a;
    --fg: #eef0f3;
    --muted: #9aa3af;
    --real-line: #7aa2ff;
    --savings-line: #9aa3af;
    --band-positive: rgba(52, 199, 111, 0.28);
    --band-negative: rgba(255, 99, 99, 0.28);
    --tooltip-bg: rgba(20, 22, 26, 0.88);
    --tooltip-border: rgba(255, 255, 255, 0.18);
  }}
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--fg); }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 14px;
  padding: 12px;
}}
.chart-wrap {{ position: relative; max-width: 800px; margin: 0 auto; }}
svg {{ width: 100%; height: auto; display: block; overflow: visible; }}
.line-real {{ fill: none; stroke: var(--real-line); stroke-width: 2.5; }}
.line-savings {{
  fill: none;
  stroke: var(--savings-line);
  stroke-width: 2;
  stroke-dasharray: 6 5;
  opacity: 0.85;
}}
.band-positive {{ fill: var(--band-positive); stroke: none; }}
.band-negative {{ fill: var(--band-negative); stroke: none; }}
.value-label {{ font-size: 12px; font-weight: 600; dominant-baseline: middle; }}
.value-label.real {{ fill: var(--real-line); }}
.value-label.savings {{ fill: var(--muted); }}
circle.hit {{ fill: transparent; stroke: none; cursor: pointer; }}
circle.hit:focus {{ outline: 2px solid var(--real-line); outline-offset: 2px; }}
.tooltip {{
  position: fixed;
  display: none;
  width: max-content;
  max-width: min(280px, calc(100vw - 24px));
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--tooltip-bg);
  border: 1px solid var(--tooltip-border);
  color: var(--fg);
  white-space: pre-line;
  font-size: 12.5px;
  line-height: 1.5;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18);
  z-index: 10;
}}
.tooltip.open {{ display: block; }}
.tooltip button.close {{
  position: absolute;
  top: 4px;
  right: 6px;
  border: none;
  background: none;
  color: var(--muted);
  font-size: 14px;
  cursor: pointer;
  line-height: 1;
  padding: 4px;
}}
</style>
</head>
<body>
<div class="chart-wrap">
  <svg viewBox="0 0 {width:.0f} {height:.0f}" xmlns="http://www.w3.org/2000/svg">
    <g class="band">
{band_svg}
    </g>
    <polyline class="line-savings" points="{savings_line}"></polyline>
    <polyline class="line-real" points="{real_line}"></polyline>
{labels_svg}
    <g class="hits">
{dots_html}
    </g>
  </svg>
  <div class="tooltip" id="s1-tooltip" role="dialog" aria-live="polite">
    <button class="close" type="button" aria-label="Cerrar">×</button>
    <div class="tooltip-body"></div>
  </div>
</div>
<script>
(function () {{
  var tooltip = document.getElementById("s1-tooltip");
  var body = tooltip.querySelector(".tooltip-body");
  var closeBtn = tooltip.querySelector(".close");

  function openTooltip(target) {{
    body.textContent = target.getAttribute("data-tooltip");
    tooltip.classList.add("open");
    // Opens right at the clicked point -- over the chart's own line/band, which is the
    // whole reason R-10.2 requires it to stay translucent (T-707) -- then clamps fully
    // inside the viewport (fixed positioning, so this is correct at any width, including
    // the 390px mobile case, R-10.2a, regardless of any ancestor's layout).
    var anchor = target.getBoundingClientRect();
    var box = tooltip.getBoundingClientRect();
    var left = anchor.left + anchor.width / 2 - box.width / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - box.width - 8));
    var top = anchor.top + anchor.height / 2 - box.height / 2;
    top = Math.max(8, Math.min(top, window.innerHeight - box.height - 8));
    tooltip.style.left = left + "px";
    tooltip.style.top = top + "px";
  }}

  function closeTooltip() {{
    tooltip.classList.remove("open");
  }}

  document.querySelectorAll("circle.hit").forEach(function (el) {{
    el.addEventListener("click", function (event) {{
      event.stopPropagation();
      openTooltip(el);
    }});
    el.addEventListener("keydown", function (event) {{
      if (event.key === "Enter" || event.key === " ") {{
        event.preventDefault();
        openTooltip(el);
      }}
    }});
  }});
  closeBtn.addEventListener("click", function (event) {{
    event.stopPropagation();
    closeTooltip();
  }});
  document.addEventListener("click", function (event) {{
    if (!tooltip.contains(event.target)) {{
      closeTooltip();
    }}
  }});
}})();
</script>
</body>
</html>
"""
