"""Fast, pure-Python tests for fina.render.section1_chart: R-10.1..R-10.8.

WP-22a (2026-09-26) redesigned the chart with the project owner (mocks v2-v4, approved at a real
390px width): English labels, the three figures as a legend with their latest value, a
phone-width canvas with the y-axis labels inside the plot, at most five x-axis labels, and a
plain-words note while investments are valued at cost (R-9.4/R-9.14). The band, tooltip, token
and structural rules (T-700..T-703, T-709) are unchanged and their tests carried over as they
were; the tests pinning the old desktop geometry and Spanish copy are replaced by tests pinning
the new ones -- an approved redesign, not test weakening (G-8).

Real-browser checks (containment, touch, focus, translucency, PDF export) live in
tests/test_render_browser.py, excluded from mutmut's own test run.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
from pathlib import Path

import pytest

from fina.render import section1_chart
from fina.render.section1_chart import (
    POSITIONS_AT_COST_NOTE,
    ChartRow,
    _band_polygons,
    _format_eur,
    _lerp,
    _path_d,
    _Point,
    _polyline,
    _quad,
    _signed_eur,
    _tooltip_html,
    _tooltip_rows,
    render_section1_chart,
)

_MODULE_PATH = Path(inspect.getfile(section1_chart))
_MODULE_SOURCE = _MODULE_PATH.read_text(encoding="utf-8")


def _row(**overrides: object) -> ChartRow:
    defaults: dict[str, object] = {
        "month": "2027-03",
        "month_label": "Mar 2027",
        "as_of": "2027-03-31",
        "is_partial": False,
        "completeness": "positions_at_cost",
        "real_net_worth_position": 100.0,
        "savings_only_position": 90.0,
        "gap_position": 10.0,
        "real_net_worth_display": "100.00",
        "savings_only_display": "90.00",
        "gap_display": "10.00",
        "savings_flow_display": "5.00",
        "positions_at_cost_display": "0.00",
        "estimated_display": "0.00",
        "opening_balances_display": "0.00",
    }
    defaults.update(overrides)
    return ChartRow(**defaults)  # type: ignore[arg-type]


def _visible_text(html_doc: str) -> str:
    """Everything a reader can see: the body's text, the tooltips' text, the axis labels --
    no markup, CSS class names or script identifiers."""
    body = html_doc.split("<body>")[1]
    payload = re.search(r"var points = (\[.*?\]);", body, re.DOTALL)
    tooltips = " ".join(p["tooltip"] for p in json.loads(payload.group(1))) if payload else ""
    without_script = re.sub(r"<script>.*?</script>", " ", body, flags=re.DOTALL)
    return re.sub(r"<[^>]+>", " ", f"{without_script} {tooltips}")


# ---------------------------------------------------------------------------
# T-700 / R-10.4: no arithmetic on money -- structurally, no Decimal at all
# ---------------------------------------------------------------------------


def test_t700_render_module_never_references_decimal() -> None:
    tree = ast.parse(_MODULE_SOURCE, filename=str(_MODULE_PATH))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "Decimal":
            offenders.append(node.lineno)
        if isinstance(node, ast.ImportFrom) and node.module == "decimal":
            offenders.append(node.lineno)
        if isinstance(node, ast.Import) and any(a.name == "decimal" for a in node.names):
            offenders.append(node.lineno)
    assert offenders == [], f"Decimal referenced at lines {offenders}"


def test_t700_chart_row_fields_are_str_float_bool_only() -> None:
    for field in ChartRow.__dataclass_fields__.values():
        assert field.type in ("str", "float", "bool"), field


# ---------------------------------------------------------------------------
# T-701 / R-10.3: every colour token defined in the base :root, not only a theme block
# ---------------------------------------------------------------------------


def test_t701_every_dark_theme_token_is_also_defined_on_base_root() -> None:
    html_doc = render_section1_chart([_row()])
    root_match = re.search(r":root\s*\{([^}]*)\}", html_doc)
    dark_match = re.search(
        r"prefers-color-scheme:\s*dark\s*\)\s*\{\s*:root\s*\{([^}]*)\}", html_doc
    )
    assert root_match is not None
    assert dark_match is not None
    base_tokens = set(re.findall(r"(--[\w-]+)\s*:", root_match.group(1)))
    dark_tokens = set(re.findall(r"(--[\w-]+)\s*:", dark_match.group(1)))
    assert dark_tokens, "dark block defines no tokens"
    assert dark_tokens <= base_tokens, dark_tokens - base_tokens


def test_t701_no_color_property_defined_only_inside_a_media_or_theme_block() -> None:
    """A cruder, structural cross-check: every custom property name used anywhere via
    `var(--x)` must resolve to a token declared on the base `:root`.
    """
    html_doc = render_section1_chart([_row()])
    root_match = re.search(r":root\s*\{([^}]*)\}", html_doc)
    assert root_match is not None
    base_tokens = set(re.findall(r"(--[\w-]+)\s*:", root_match.group(1)))
    used_tokens = set(re.findall(r"var\((--[\w-]+)\)", html_doc))
    assert used_tokens <= base_tokens, used_tokens - base_tokens


# ---------------------------------------------------------------------------
# T-702 / R-10.2: band colour switches at the exact linear zero-crossing
# ---------------------------------------------------------------------------


def test_t702_band_splits_at_the_exact_linear_zero_crossing() -> None:
    # gap goes from +10 at x=0 to -10 at x=100: crossing at exactly the midpoint, x=50.
    a = _Point(x=0.0, real_y=0.0, savings_y=100.0, gap_value=10.0)
    b = _Point(x=100.0, real_y=100.0, savings_y=0.0, gap_value=-10.0)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 2
    (pts_a, cls_a), (pts_b, cls_b) = polygons
    assert cls_a == "band-gain"
    assert cls_b == "band-loss"
    # The crossing point (x=50.00) must appear as a shared vertex of both polygons.
    assert "50.00,50.00" in pts_a
    assert "50.00,50.00" in pts_b


def test_t702_band_crossing_is_not_at_the_nearer_endpoint() -> None:
    """An asymmetric crossing (gap +90 -> -10) must land at the *linear* fraction (90%
    across), not simply favour whichever endpoint's magnitude is smaller.
    """
    a = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=90.0)
    b = _Point(x=100.0, real_y=0.0, savings_y=0.0, gap_value=-10.0)
    polygons = _band_polygons([a, b])
    pts_a, _cls_a = polygons[0]
    # t = 90 / (90 - (-10)) = 0.9 -> crossing x = 90.00, not 50 (midpoint) or 10.
    assert "90.00," in pts_a


def test_band_segment_with_no_sign_change_is_a_single_polygon() -> None:
    a = _Point(x=0.0, real_y=0.0, savings_y=10.0, gap_value=5.0)
    b = _Point(x=10.0, real_y=1.0, savings_y=9.0, gap_value=3.0)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 1
    assert polygons[0][1] == "band-gain"


def test_band_segment_entirely_negative_is_a_single_negative_polygon() -> None:
    a = _Point(x=0.0, real_y=0.0, savings_y=10.0, gap_value=-5.0)
    b = _Point(x=10.0, real_y=1.0, savings_y=9.0, gap_value=-3.0)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 1
    assert polygons[0][1] == "band-loss"


def test_zero_gap_endpoint_counts_as_non_negative_not_a_crossing() -> None:
    """`gap == 0` is treated as the non-negative side (`>= 0`), so a segment from 0 to a
    positive value is one polygon, not a spurious split.
    """
    a = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=0.0)
    b = _Point(x=10.0, real_y=0.0, savings_y=0.0, gap_value=5.0)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 1
    assert polygons[0][1] == "band-gain"


def test_second_endpoint_of_zero_gap_also_counts_as_non_negative() -> None:
    """The `>= 0` check applies to *both* endpoints, not just the first: a segment ending
    exactly at gap == 0, from a positive start, is also one polygon, not a crossing.
    """
    a = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=5.0)
    b = _Point(x=10.0, real_y=0.0, savings_y=0.0, gap_value=0.0)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 1
    assert polygons[0][1] == "band-gain"


def test_second_endpoint_threshold_is_zero_not_one() -> None:
    """The non-negative threshold is exactly 0, not silently 1: a segment from a small
    positive value (0.5) to a larger one must not be seen as a "sign change" from the first
    endpoint's perspective.
    """
    a = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=0.0)
    b = _Point(x=10.0, real_y=0.0, savings_y=0.0, gap_value=0.5)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 1
    assert polygons[0][1] == "band-gain"


def test_crossing_segment_classifies_a_boundary_start_point_correctly() -> None:
    """A crossing segment starting exactly at gap == 0 (the non-negative boundary) must
    classify that side as `band-gain`, not `band-loss` -- and the *other* side, well below
    zero, as `band-loss`.
    """
    a = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=0.0)
    b = _Point(x=10.0, real_y=0.0, savings_y=0.0, gap_value=-10.0)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 2
    assert polygons[0][1] == "band-gain"
    assert polygons[1][1] == "band-loss"


def test_crossing_segment_classifies_a_negative_start_point_correctly() -> None:
    a = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=-5.0)
    b = _Point(x=10.0, real_y=0.0, savings_y=0.0, gap_value=5.0)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 2
    assert polygons[0][1] == "band-loss"
    assert polygons[1][1] == "band-gain"


def test_crossing_segment_classifies_a_boundary_end_point_correctly() -> None:
    """The end point of a crossing segment exactly at gap == 0 must also classify as
    `band-gain`, not `band-loss`.
    """
    a = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=-5.0)
    b = _Point(x=10.0, real_y=0.0, savings_y=0.0, gap_value=0.0)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 2
    assert polygons[0][1] == "band-loss"
    assert polygons[1][1] == "band-gain"


def test_band_polygons_processes_every_segment_not_just_the_first() -> None:
    """Three points (two segments), both same-signed but for genuinely different reasons --
    proves the loop continues past the first segment rather than stopping there.
    """
    p0 = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=5.0)
    p1 = _Point(x=10.0, real_y=0.0, savings_y=0.0, gap_value=3.0)
    p2 = _Point(x=20.0, real_y=0.0, savings_y=0.0, gap_value=-2.0)
    polygons = _band_polygons([p0, p1, p2])
    # Segment 0-1: both positive -> 1 polygon. Segment 1-2: crosses zero -> 2 polygons.
    assert len(polygons) == 3
    assert polygons[0][1] == "band-gain"
    assert polygons[1][1] == "band-gain"
    assert polygons[2][1] == "band-loss"


# ---------------------------------------------------------------------------
# T-703 / R-10.2: click-only tooltip, no hover-triggered opening
# ---------------------------------------------------------------------------


def test_t703_no_hover_triggered_open_handler_is_emitted() -> None:
    """The tooltip opens on click only -- no JS hover handler ever triggers it. The mock's own
    `.tooltip-close:hover` CSS rule (a cosmetic desktop affordance on the close button, with no
    effect on whether/how the tooltip *opens*) is ported verbatim and is not what this rule
    forbids -- so this checks for hover-driven *event handlers*, not the CSS pseudo-class.
    """
    html_doc = render_section1_chart([_row(), _row(month_label="Apr 2027")])
    lowered = html_doc.lower()
    for hover_token in ("mouseover", "mouseenter", "mousemove", "onmouseover"):
        assert hover_token not in lowered, hover_token


def test_t703_click_and_keydown_handlers_are_emitted() -> None:
    html_doc = render_section1_chart([_row()])
    assert '"click"' in html_doc
    assert '"keydown"' in html_doc


def test_a_second_click_on_the_same_point_closes_the_tooltip() -> None:
    """Ported from the mock's own `showFor`/`select` toggle: clicking the already-selected
    point again closes it, rather than re-opening the same tooltip.
    """
    html_doc = render_section1_chart([_row()])
    assert "selected === i" in html_doc or "selected === 0" in html_doc.replace(" ", " ")
    assert "hideTooltip" in html_doc


def test_click_outside_the_tooltip_closes_it() -> None:
    html_doc = render_section1_chart([_row()])
    assert "tooltip.contains(evt.target)" in html_doc


# ---------------------------------------------------------------------------
# The owner's three figures, as a legend with their latest value (WP-22a)
# ---------------------------------------------------------------------------


def test_legend_names_the_three_figures_with_their_latest_values() -> None:
    rows = [
        _row(real_net_worth_display="1.00"),
        _row(
            real_net_worth_display="390860.92",
            savings_only_display="219601.52",
            gap_display="171259.40",
        ),
    ]
    legend = section1_chart._legend_html(rows[-1])
    assert legend == (
        '<div class="legend">\n'
        '  <div class="legend-item"><span class="swatch"></span>'
        '<span class="lg-name">Net worth <small>(cash and other assets)</small></span>'
        '<b class="lg-value">390,860.92 €</b></div>\n'
        '  <div class="legend-item"><span class="swatch dashed"></span>'
        '<span class="lg-name">Total Savings <small>(cash contributions)</small></span>'
        '<b class="lg-value">219,601.52 €</b></div>\n'
        '  <div class="legend-item gain"><span class="chip"></span>'
        '<span class="lg-name">Total Return of Investments</span>'
        '<b class="lg-value">+171,259.40 €</b></div>\n'
        "</div>"
    )
    assert legend in render_section1_chart(rows)


def test_a_negative_return_is_signed_and_marked_as_a_loss() -> None:
    legend = section1_chart._legend_html(_row(gap_display="-3500.00"))
    assert '<div class="legend-item loss"><span class="chip"></span>' in legend
    assert '<b class="lg-value">-3,500.00 €</b>' in legend


def test_the_estimated_share_is_named_under_net_worth_only_when_there_is_one() -> None:
    legend = section1_chart._legend_html(_row(estimated_display="43050.00"))
    assert '<span class="lg-extra">incl. 43,050.00 € estimated</span>' in legend
    assert "lg-extra" not in section1_chart._legend_html(_row())


def test_the_cost_note_appears_only_while_investments_are_held_at_cost() -> None:
    with_positions = render_section1_chart([_row(positions_at_cost_display="223350.79")])
    assert f'<p class="note">{POSITIONS_AT_COST_NOTE.replace(chr(39), "&#x27;")}</p>' in (
        with_positions
    )
    assert POSITIONS_AT_COST_NOTE == (
        "Asset prices are not updated to today's value: investments are shown at what you "
        "paid for them."
    )
    assert 'class="note"' not in render_section1_chart([_row()])


def test_the_heading_says_up_to_which_day_and_whether_the_month_is_over() -> None:
    partial = render_section1_chart([_row(as_of="2026-09-24", is_partial=True)])
    assert '<p class="sub">Data up to 24 Sep 2026 · month in progress</p>' in partial
    closed = render_section1_chart([_row(as_of="2026-08-31")])
    assert '<p class="sub">Data up to 31 Aug 2026</p>' in closed
    assert "<h1>Net worth</h1>" in closed


def test_no_gap_or_hueco_wording_anywhere_a_reader_can_see() -> None:
    rows = [_row(), _row(month="2027-04", month_label="Apr 2027", is_partial=True)]
    visible = _visible_text(render_section1_chart(rows)).lower()
    assert "gap" not in visible
    assert "hueco" not in visible
    assert "patrimonio" not in visible


# ---------------------------------------------------------------------------
# Display strings: presentation transforms only (R-10.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("display", "expected"),
    [
        ("0.00", "0.00 €"),
        ("999.99", "999.99 €"),
        ("1000.00", "1,000.00 €"),
        ("28121.57", "28,121.57 €"),
        ("1234567.89", "1,234,567.89 €"),
        ("-28121.57", "-28,121.57 €"),
        ("-100.00", "-100.00 €"),
        ("5", "5 €"),
    ],
)
def test_format_eur_groups_thousands(display: str, expected: str) -> None:
    assert _format_eur(display) == expected


def test_signed_eur_prefixes_plus_only_for_non_negative() -> None:
    assert _signed_eur("11437.82") == "+11,437.82 €"
    assert _signed_eur("0.00") == "+0.00 €"
    assert _signed_eur("-5.00") == "-5.00 €"


@pytest.mark.parametrize(
    ("display", "zero"),
    [("0.00", True), ("-0.00", True), ("0", True), ("10.00", False), ("0.50", False)],
)
def test_is_zero(display: str, zero: bool) -> None:
    assert section1_chart._is_zero(display) is zero


@pytest.mark.parametrize(
    ("iso", "expected"),
    [("2026-09-24", "24 Sep 2026"), ("2027-01-05", "5 Jan 2027"), ("2023-12-31", "31 Dec 2023")],
)
def test_long_date(iso: str, expected: str) -> None:
    assert section1_chart._long_date(iso) == expected


# ---------------------------------------------------------------------------
# Y axis: round steps, labels inside the plot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("span", "step"),
    [
        (100.0, 20.0),
        (390000.0, 100000.0),
        (35000.0, 10000.0),
        (9.0, 2.0),
        (12.0, 2.5),
        (50.0, 10.0),
        (0.4, 0.1),
    ],
)
def test_nice_step_keeps_at_most_five_round_intervals(span: float, step: float) -> None:
    assert section1_chart._nice_step(span) == pytest.approx(step)


def test_axis_range_widens_to_whole_steps() -> None:
    assert section1_chart._axis_range([2100.0, 38900.0]) == (0.0, 40000.0, 10000.0)
    assert section1_chart._axis_range([-1500.0, 2600.0]) == (-2000.0, 3000.0, 1000.0)


def test_a_flat_series_gets_a_range_around_its_value() -> None:
    assert section1_chart._axis_range([50.0, 50.0]) == (49.0, 51.0, 0.5)


def test_y_ticks_cover_the_range_one_step_apart() -> None:
    assert section1_chart._y_ticks(0.0, 40000.0, 10000.0) == [
        0.0,
        10000.0,
        20000.0,
        30000.0,
        40000.0,
    ]


@pytest.mark.parametrize(
    ("value", "step", "label"),
    [
        (25000.0, 5000.0, "25k"),
        (2500.0, 2500.0, "2.5k"),
        (-5000.0, 5000.0, "-5k"),
        (0.0, 1000.0, "0k"),
        (40.0, 20.0, "40"),
        (2.5, 0.5, "2.5"),
        (999.0, 999.0, "999"),
    ],
)
def test_axis_label(value: float, step: float, label: str) -> None:
    assert section1_chart._axis_label(value, step) == label


def test_grid_puts_each_label_just_above_its_line_and_the_lowest_line_is_the_baseline() -> None:
    lines, labels = section1_chart._grid_svg(0.0, 20000.0, 10000.0)
    left, right = section1_chart._PAD_LEFT, section1_chart._WIDTH - section1_chart._PAD_RIGHT
    ys = [section1_chart._y_scale(v, 0.0, 20000.0) for v in (0.0, 10000.0, 20000.0)]
    assert lines == "\n".join(
        f'<line class="{cls}" x1="{left:.2f}" x2="{right:.2f}" y1="{y:.2f}" y2="{y:.2f}"></line>'
        for cls, y in zip(("baseline", "grid-line", "grid-line"), ys, strict=True)
    )
    assert labels == "\n".join(
        f'<text class="axis-label y-label" x="{left:.2f}" y="{y - 4:.2f}">{text}</text>'
        for text, y in zip(("0k", "10k", "20k"), ys, strict=True)
    )


def test_y_labels_are_drawn_after_the_lines_so_no_line_hides_them() -> None:
    html_doc = render_section1_chart([_row(), _row(real_net_worth_position=300.0)])
    assert html_doc.index('class="line-real"') < html_doc.index('class="axis-label y-label"')


def test_the_top_label_has_room_above_its_line() -> None:
    assert section1_chart._PAD_TOP - 4 - 11 >= 0  # 11px text above the top line fits


# ---------------------------------------------------------------------------
# X axis: at most five labels
# ---------------------------------------------------------------------------


def _months(first: str, count: int) -> list[ChartRow]:
    year, month = (int(p) for p in first.split("-"))
    rows = []
    for i in range(count):
        y, m = divmod(year * 12 + month - 1 + i, 12)
        rows.append(_row(month=f"{y}-{m + 1:02d}", month_label=f"M{i}"))
    return rows


@pytest.mark.parametrize(
    ("first", "count", "step"),
    [
        ("2026-01", 1, 1),
        ("2026-01", 5, 1),
        ("2026-01", 6, 2),
        ("2026-02", 9, 2),
        ("2024-07", 27, 6),
        ("2023-03", 49, 12),
        ("2000-01", 97, 24),
        ("2000-01", 121, 60),
        ("1900-01", 1500, 120),
    ],
)
def test_x_label_step_keeps_at_most_five_labels(first: str, count: int, step: int) -> None:
    rows = _months(first, count)
    assert section1_chart._x_label_step(rows) == step
    shown = [r for r in rows if section1_chart._month_ordinal(r.month) % step == 0]
    assert 1 <= len(shown) <= 5 or count > 1200


def test_x_label_text_is_the_year_alone_for_yearly_steps() -> None:
    assert section1_chart._x_label_text("2025-01", 12) == "2025"
    assert section1_chart._x_label_text("2025-07", 6) == "Jul 25"
    assert section1_chart._x_label_text("2025-12", 1) == "Dec 25"


def test_x_labels_sit_under_their_month_and_grow_inwards_at_the_edges() -> None:
    assert section1_chart._x_anchor(section1_chart._PAD_LEFT) == "start"
    assert section1_chart._x_anchor(section1_chart._WIDTH / 2) == "middle"
    assert section1_chart._x_anchor(section1_chart._WIDTH - section1_chart._PAD_RIGHT) == "end"
    edge = section1_chart._PAD_LEFT + section1_chart._EDGE_ZONE
    assert section1_chart._x_anchor(edge) == "middle"
    right_edge = section1_chart._WIDTH - section1_chart._PAD_RIGHT - section1_chart._EDGE_ZONE
    assert section1_chart._x_anchor(right_edge) == "middle"


def test_x_labels_svg_exact_markup() -> None:
    rows = _months("2026-01", 3)
    points = section1_chart._build_points(rows)
    y = section1_chart._HEIGHT - section1_chart._PAD_BOTTOM
    got = section1_chart._x_labels_svg(rows, points)
    expected = "\n".join(
        f'<line class="x-tick" x1="{p.x:.2f}" x2="{p.x:.2f}" y1="{y:.2f}" y2="{y + 4:.2f}"></line>'
        f'\n<text class="axis-label" x="{p.x:.2f}" y="{y + 18:.2f}" '
        f'text-anchor="{section1_chart._x_anchor(p.x)}">{label}</text>'
        for p, label in zip(points, ("Jan 26", "Feb 26", "Mar 26"), strict=True)
    )
    assert got == expected


def test_months_between_steps_get_no_label() -> None:
    rows = _months("2026-01", 6)  # step 2: Jan, Mar, May
    html_doc = render_section1_chart(rows)
    for label in ("Jan 26", "Mar 26", "May 26"):
        assert f">{label}</text>" in html_doc
    for label in ("Feb 26", "Apr 26", "Jun 26"):
        assert f">{label}</text>" not in html_doc


# ---------------------------------------------------------------------------
# Tooltip (R-10.2): English, the return signed and coloured
# ---------------------------------------------------------------------------


def test_tooltip_rows_exact_text() -> None:
    row = _row(
        real_net_worth_display="28121.57",
        savings_only_display="16683.75",
        savings_flow_display="-5.00",
    )
    assert _tooltip_rows(row) == [
        ("Net worth", "28,121.57 €"),
        ("Total savings", "16,683.75 €"),
        ("Saved this month", "-5.00 €"),
    ]


def test_tooltip_names_balances_already_held_in_the_month_they_arrive() -> None:
    rows = _tooltip_rows(_row(opening_balances_display="155903.36"))
    assert rows[-1] == ("Balances already held", "155,903.36 €")


def test_tooltip_html_exact_text() -> None:
    row = _row(month_label="Mar 2027", as_of="2027-03-07", is_partial=True, gap_display="-1.50")
    assert _tooltip_html(row) == (
        '<button type="button" class="tooltip-close" aria-label="Close">×</button>'
        '<div class="t-month">Mar 2027 (partial)</div>'
        '<div class="t-asof">as of 7 Mar 2027</div>'
        '<div class="t-row"><span class="lab">Net worth</span><span>100.00 €</span></div>'
        '<div class="t-row"><span class="lab">Total savings</span><span>90.00 €</span></div>'
        '<div class="t-row"><span class="lab">Saved this month</span><span>5.00 €</span></div>'
        '<div class="t-row t-return loss"><span class="lab">Total return</span>'
        "<span>-1.50 €</span></div>"
    )


def test_tooltip_html_marks_a_non_negative_return_as_a_gain() -> None:
    assert 't-return gain"' in _tooltip_html(_row(gap_display="0.00"))
    assert "(partial)" not in _tooltip_html(_row())


def test_tooltip_html_escapes_a_double_quote_in_the_month_label() -> None:
    assert "&quot;" in _tooltip_html(_row(month_label='Mar"'))


# ---------------------------------------------------------------------------
# T-709 / R-10.2b: no backdrop-filter anywhere
# ---------------------------------------------------------------------------


def test_t709_no_backdrop_filter_anywhere() -> None:
    html_doc = render_section1_chart([_row()])
    assert "backdrop-filter" not in html_doc.lower()
    assert "backdrop-filter" not in _MODULE_SOURCE.lower()


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def test_x_scale_single_point_is_centered() -> None:
    x = section1_chart._x_scale(0, 1)
    assert x == section1_chart._PAD_LEFT + section1_chart._PLOT_WIDTH / 2


def test_x_scale_spans_the_full_plot_width_for_first_and_last() -> None:
    assert section1_chart._x_scale(0, 5) == section1_chart._PAD_LEFT
    assert section1_chart._x_scale(4, 5) == section1_chart._PAD_LEFT + section1_chart._PLOT_WIDTH


def test_x_scale_with_exactly_two_points_spans_the_full_width() -> None:
    assert section1_chart._x_scale(0, 2) == section1_chart._PAD_LEFT
    assert section1_chart._x_scale(1, 2) == section1_chart._PAD_LEFT + section1_chart._PLOT_WIDTH


def test_x_scale_exact_value_at_an_interior_index() -> None:
    x = section1_chart._x_scale(1, 4)
    assert x == section1_chart._PAD_LEFT + section1_chart._PLOT_WIDTH / 3


def test_the_plot_uses_the_phone_width_edge_to_edge() -> None:
    assert section1_chart._WIDTH == 390.0
    assert section1_chart._PLOT_WIDTH == 366.0


def test_y_scale_flat_series_is_centered() -> None:
    y = section1_chart._y_scale(5.0, 5.0, 5.0)
    assert y == section1_chart._PAD_TOP + section1_chart._PLOT_HEIGHT / 2


def test_y_scale_exact_values_at_min_max_and_quarter_points() -> None:
    pad_top = section1_chart._PAD_TOP
    plot_height = section1_chart._PLOT_HEIGHT
    y_scale = section1_chart._y_scale
    assert y_scale(0.0, 0.0, 100.0) == pad_top + plot_height
    assert y_scale(100.0, 0.0, 100.0) == pad_top
    assert y_scale(25.0, 0.0, 100.0) == pad_top + plot_height * 0.75
    assert y_scale(-10.0, -20.0, 0.0) == pad_top + plot_height * 0.5


def test_build_points_scale_to_the_rounded_axis_range() -> None:
    rows = [
        _row(real_net_worth_position=0.0, savings_only_position=95.0, gap_position=-95.0),
        _row(real_net_worth_position=100.0, savings_only_position=0.0, gap_position=100.0),
    ]
    points = section1_chart._build_points(rows)
    y = section1_chart._y_scale
    assert points[0].real_y == y(0.0, 0.0, 100.0)
    assert points[0].savings_y == y(95.0, 0.0, 100.0)
    assert points[1].real_y == y(100.0, 0.0, 100.0)
    assert [p.gap_value for p in points] == [-95.0, 100.0]
    assert [p.x for p in points] == [section1_chart._x_scale(i, 2) for i in range(2)]


def test_lerp_exact_values() -> None:
    assert _lerp(0.0, 10.0, 0.5) == 5.0
    assert _lerp(10.0, 20.0, 0.25) == 12.5
    assert _lerp(5.0, 5.0, 0.7) == 5.0


def test_quad_produces_four_vertices() -> None:
    assert _quad(1.0, 2.0, 3.0, 4.0, 5.0, 6.0) == "1.00,2.00 4.00,5.00 4.00,6.00 1.00,3.00"


def test_polyline_joins_coordinates() -> None:
    assert _polyline([(1.0, 2.0), (3.5, 4.25)]) == "1.00,2.00 3.50,4.25"


def test_path_d_prefixes_m_and_joins_with_l() -> None:
    assert _path_d([(1.0, 2.0), (3.0, 4.0)]) == "M 1.00,2.00 L 3.00,4.00"


def test_path_d_single_point_has_no_l_segment() -> None:
    assert _path_d([(1.0, 2.0)]) == "M 1.00,2.00"


# ---------------------------------------------------------------------------
# The whole document
# ---------------------------------------------------------------------------


def test_points_payload_field_names_are_exact() -> None:
    row = _row(month_label="Mar 2027")
    point = _Point(x=1.0, real_y=2.0, savings_y=3.0, gap_value=0.0)
    payload = json.loads(section1_chart._points_payload([row], [point]))
    assert payload == [
        {
            "x": 1.0,
            "realY": 2.0,
            "savingsY": 3.0,
            "label": "Mar 2027",
            "tooltip": _tooltip_html(row),
        }
    ]


def test_points_payload_rounds_each_coordinate_to_two_decimal_places() -> None:
    point = _Point(x=1 / 3, real_y=2 / 3, savings_y=1 / 7, gap_value=0.0)
    payload = json.loads(section1_chart._points_payload([_row()], [point]))
    assert (payload[0]["x"], payload[0]["realY"], payload[0]["savingsY"]) == (
        round(1 / 3, 2),
        round(2 / 3, 2),
        round(1 / 7, 2),
    )


def test_render_last_index_matches_row_count_minus_one() -> None:
    rows = _months("2026-01", 4)
    assert "var lastIndex = 3;" in render_section1_chart(rows)


def test_end_dots_mark_the_last_point_of_each_line() -> None:
    rows = [_row(real_net_worth_position=10.0), _row(real_net_worth_position=80.0)]
    last = section1_chart._build_points(rows)[-1]
    html_doc = render_section1_chart(rows)
    assert (
        f'<circle class="end-dot savings" cx="{last.x:.2f}" cy="{last.savings_y:.2f}" r="4">'
        in html_doc
    )
    assert (
        f'<circle class="end-dot real" cx="{last.x:.2f}" cy="{last.real_y:.2f}" r="4">' in html_doc
    )


def test_render_empty_series_returns_placeholder_without_raising() -> None:
    html_doc = render_section1_chart([])
    assert "No data to show yet." in html_doc
    assert "<svg" not in html_doc


def test_render_is_a_pure_function_of_its_input() -> None:
    rows = [_row(), _row(month="2027-04", month_label="Apr 2027", real_net_worth_position=50.0)]
    assert render_section1_chart(rows) == render_section1_chart(rows)


def test_real_and_savings_paths_contain_actual_coordinates() -> None:
    rows = [_row(real_net_worth_position=10.0), _row(real_net_worth_position=20.0)]
    html_doc = render_section1_chart(rows)
    real_match = re.search(r'class="line-real" d="([^"]*)"', html_doc)
    savings_match = re.search(r'class="line-savings" d="([^"]*)"', html_doc)
    assert real_match is not None
    assert savings_match is not None
    coord_re = re.compile(r"^M -?\d+\.\d{2},-?\d+\.\d{2}(?: L -?\d+\.\d{2},-?\d+\.\d{2})*$")
    assert coord_re.match(real_match.group(1))
    assert coord_re.match(savings_match.group(1))
    assert real_match.group(1) != savings_match.group(1)


def test_band_svg_polygons_are_newline_joined() -> None:
    rows = [
        _row(gap_position=5.0),
        _row(gap_position=-5.0, real_net_worth_position=80.0),
        _row(gap_position=3.0),
    ]
    band_section = render_section1_chart(rows).split('<g class="band">')[1].split("</g>")[0]
    assert band_section.count("<polygon") == 4
    assert band_section.count("</polygon>\n<polygon") == 3


def test_one_shared_hit_area_regardless_of_row_count() -> None:
    html_doc = render_section1_chart(_months("2026-01", 5))
    assert html_doc.count('class="hit-area"') == 1


def test_points_payload_escapes_a_closing_script_tag_in_month_label() -> None:
    row = _row(month_label="Mar</script><script>alert(1)</script>")
    html_doc = render_section1_chart([row])
    assert html_doc.count("</script>") == 1
    assert "<\\/script>" in html_doc


def test_the_script_positions_the_tooltip_against_the_phone_width_canvas() -> None:
    html_doc = render_section1_chart([_row()])
    assert "var frac = p.x / 390;" in html_doc
    assert f"var step = {section1_chart._PLOT_WIDTH:.2f} / Math.max(lastIndex, 1);" in html_doc


def test_y_ticks_start_from_a_non_zero_minimum() -> None:
    assert section1_chart._y_ticks(-2000.0, 3000.0, 1000.0) == [
        -2000.0,
        -1000.0,
        0.0,
        1000.0,
        2000.0,
        3000.0,
    ]


def test_no_note_leaves_nothing_between_the_chart_and_the_script() -> None:
    html_doc = render_section1_chart([_row()])
    assert "</div>\n\n<script>" in html_doc
