"""Fast, pure-Python tests for fina.render.section1_chart (WP-9, re-derived for Q-G): R-10.1..R-10.5.

Q-G: this module was re-derived as a genuine port of the project owner's approved mock
(`docs/design/section1-approved-mock.html`) after WP-9's original, independently-designed
version. Every literal rule (R-10.1..R-10.5, R-10.2a, R-10.2b) and the T-700..T-709 test IDs
below still apply and still pass; the specific markup/CSS-class assertions that pin *how* they
pass were updated to the new port's own structure (`band-gain`/`band-loss` instead of
`band-positive`/`band-negative`, a single `.hit-area` instead of one `circle.hit` per point, a
structured tooltip body instead of a `data-tooltip` attribute payload) -- this is expected of a
deliberate, approved redesign, not test weakening (G-8): no assertion here is looser than its
predecessor, each pins the same rule against the new markup instead.

Real-browser visual verification (T-705..T-708, which genuinely need a rendered page) lives
in tests/test_render_browser.py, excluded from mutmut's own test run (like test_gates_meta.py)
since re-launching a browser per mutant would be prohibitively slow for no unique coverage --
every line of this module's own Python code is already covered by the tests here.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
from pathlib import Path

from fina.render import section1_chart
from fina.render.section1_chart import (
    ChartRow,
    _band_polygons,
    _format_eur,
    _lerp,
    _path_d,
    _Point,
    _polyline,
    _quad,
    _signed_eur,
    _thousands_label,
    _tooltip_html,
    _tooltip_rows,
    render_section1_chart,
)

_MODULE_PATH = Path(inspect.getfile(section1_chart))
_MODULE_SOURCE = _MODULE_PATH.read_text(encoding="utf-8")


def _row(**overrides: object) -> ChartRow:
    defaults: dict[str, object] = {
        "month_label": "Mar 2027",
        "as_of": "2027-03-31",
        "is_partial": False,
        "completeness": "cash_only",
        "real_net_worth_position": 100.0,
        "savings_only_position": 90.0,
        "gap_position": 10.0,
        "real_net_worth_display": "100.00",
        "savings_only_display": "90.00",
        "gap_display": "10.00",
        "savings_flow_display": "5.00",
    }
    defaults.update(overrides)
    return ChartRow(**defaults)  # type: ignore[arg-type]


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
# T-704 / R-10.5: cash_only qualifier travels with the figure, no separate footer
# ---------------------------------------------------------------------------


def test_t704_cash_only_qualifier_is_rendered_on_the_end_label() -> None:
    html_doc = render_section1_chart([_row(completeness="cash_only")])
    value_label_match = re.search(r'class="end-label real"[^>]*>([^<]*)</text>', html_doc)
    assert value_label_match is not None
    assert value_label_match.group(1) == "100.00€ (cash_only)"


def test_t704_qualifier_omitted_when_completeness_is_not_cash_only() -> None:
    html_doc = render_section1_chart([_row(completeness="other")])  # type: ignore[arg-type]
    value_label_match = re.search(r'class="end-label real"[^>]*>([^<]*)</text>', html_doc)
    assert value_label_match is not None
    assert value_label_match.group(1) == "100.00€"


def test_t704_cash_only_qualifier_also_carried_in_the_tooltip() -> None:
    rows = _tooltip_rows(_row(completeness="cash_only"))
    label, value = rows[0]
    assert label == "Patrimonio real"
    assert value == "100.00€ (cash_only)"


def test_end_labels_sit_at_the_last_points_x_plus_8_exactly() -> None:
    row = _row(
        real_net_worth_position=10.0,
        savings_only_position=20.0,
        real_net_worth_display="10.00",
        savings_only_display="20.00",
        completeness="other",  # type: ignore[arg-type]
    )
    html_doc = render_section1_chart([row])
    points = section1_chart._build_points([row])
    expected_x = f"{points[0].x + 8:.2f}"
    real_x_match = re.search(r'class="end-label real" x="([^"]*)"', html_doc)
    ahorro_x_match = re.search(r'class="end-label ahorro" x="([^"]*)"', html_doc)
    assert real_x_match is not None
    assert ahorro_x_match is not None
    assert real_x_match.group(1) == expected_x
    assert ahorro_x_match.group(1) == expected_x


def test_ahorro_end_label_shows_the_exact_savings_display_value() -> None:
    row = _row(savings_only_display="12345.67", completeness="other")  # type: ignore[arg-type]
    html_doc = render_section1_chart([row])
    label_match = re.search(r'class="end-label ahorro"[^>]*>([^<]*)</text>', html_doc)
    assert label_match is not None
    assert label_match.group(1) == "12345.67€"


def test_no_separate_explanatory_footer_element() -> None:
    """R-10.2: no explanatory footer -- no `<footer>` element, and the page's only `<p>` is
    the mock's own head-row headline (the accumulated-gap figure, ported verbatim), which sits
    *before* the chart rather than trailing it as an explanation would.
    """
    html_doc = render_section1_chart([_row()])
    assert "<footer" not in html_doc
    # `<p[ >]` (not `<p[a-z]`) so `<path ...>` (the two series lines) is never miscounted.
    assert len(re.findall(r"<p[ >]", html_doc)) == 1
    assert html_doc.count('<p class="sub">') == 1
    assert html_doc.index('<p class="sub">') < html_doc.index("<svg")


def test_no_legend_entries_for_band_colours() -> None:
    """R-10.2 forbids legend entries for the *band* colours specifically -- the mock's own
    two-line series legend (solid "real", dashed "ahorro") is explicitly approved and present;
    what must never appear is a legend item naming the gain/loss band colours.
    """
    html_doc = render_section1_chart([_row(), _row(gap_position=-1.0, gap_display="-1.00")])
    legend_section = html_doc.split('<div class="legend">')[1].split("</div>\n  </div>")[0]
    assert "band-gain" not in legend_section
    assert "band-loss" not in legend_section
    assert "gain" not in legend_section.lower()
    assert "loss" not in legend_section.lower()
    assert "hueco" not in legend_section.lower()
    # The two series *are* legitimately listed.
    assert "Patrimonio neto real" in html_doc
    assert "Solo ahorro" in html_doc


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
    first = section1_chart._x_scale(0, 5)
    last = section1_chart._x_scale(4, 5)
    assert first == section1_chart._PAD_LEFT
    assert last == section1_chart._PAD_LEFT + section1_chart._PLOT_WIDTH


def test_x_scale_with_exactly_two_points_spans_the_full_width() -> None:
    """The single-point centering branch triggers only for `count <= 1`, not `<= 2`: with
    exactly two points, they must sit at the two ends of the plot, not both at the centre.
    """
    first = section1_chart._x_scale(0, 2)
    second = section1_chart._x_scale(1, 2)
    assert first == section1_chart._PAD_LEFT
    assert second == section1_chart._PAD_LEFT + section1_chart._PLOT_WIDTH
    assert first != second


def test_x_scale_exact_value_at_an_interior_index() -> None:
    # index 1 of 4 (count=4, so 3 gaps): PAD_LEFT + PLOT_WIDTH * 1/3.
    x = section1_chart._x_scale(1, 4)
    assert x == section1_chart._PAD_LEFT + section1_chart._PLOT_WIDTH / 3


def test_y_scale_flat_series_is_centered() -> None:
    y = section1_chart._y_scale(5.0, 5.0, 5.0)
    assert y == section1_chart._PAD_TOP + section1_chart._PLOT_HEIGHT / 2


def test_y_scale_exact_values_at_min_max_and_quarter_points() -> None:
    pad_top = section1_chart._PAD_TOP
    plot_height = section1_chart._PLOT_HEIGHT
    y_scale = section1_chart._y_scale
    # value == min_value -> fraction 0 -> bottom of the plot (y = pad_top + plot_height).
    assert y_scale(0.0, 0.0, 100.0) == pad_top + plot_height
    # value == max_value -> fraction 1 -> top of the plot (y = pad_top).
    assert y_scale(100.0, 0.0, 100.0) == pad_top
    # value a quarter of the way up -> fraction 0.25 -> y = pad_top + plot_height * 0.75.
    assert y_scale(25.0, 0.0, 100.0) == pad_top + plot_height * 0.75
    # A negative-value span, to rule out any bug that only shows up with min_value < 0.
    assert y_scale(-10.0, -20.0, 0.0) == pad_top + plot_height * 0.5


def test_lerp_exact_values() -> None:
    assert _lerp(0.0, 10.0, 0.5) == 5.0
    assert _lerp(0.0, 10.0, 0.25) == 2.5
    assert _lerp(10.0, 0.0, 0.25) == 7.5
    assert _lerp(-10.0, 10.0, 0.5) == 0.0
    assert _lerp(4.0, 4.0, 0.7) == 4.0  # a == b: t is irrelevant, result is always a (== b)


def test_quad_produces_four_vertices() -> None:
    pts = _quad(0, 1, 2, 10, 3, 4)
    assert len(pts.split(" ")) == 4


def test_polyline_joins_coordinates() -> None:
    assert _polyline([(0.0, 0.0), (1.5, 2.5)]) == "0.00,0.00 1.50,2.50"


def test_path_d_prefixes_m_and_joins_with_l() -> None:
    assert _path_d([(0.0, 0.0), (1.5, 2.5), (3.0, 4.0)]) == "M 0.00,0.00 L 1.50,2.50 L 3.00,4.00"


def test_path_d_single_point_has_no_l_segment() -> None:
    assert _path_d([(1.0, 1.0)]) == "M 1.00,1.00"


def test_format_eur_appends_symbol_with_no_space() -> None:
    assert _format_eur("100.00") == "100.00€"


def test_signed_eur_prefixes_plus_for_non_negative() -> None:
    assert _signed_eur("100.00") == "+100.00€"
    assert _signed_eur("0.00") == "+0.00€"


def test_signed_eur_keeps_a_single_minus_for_negative() -> None:
    assert _signed_eur("-500.00") == "-500.00€"
    assert _signed_eur("-500.00").count("-") == 1


def test_thousands_label_rounds_to_the_nearest_thousand() -> None:
    assert _thousands_label(2600.0) == "3k"
    assert _thousands_label(2499.0) == "2k"
    assert _thousands_label(0.0) == "0k"


def test_thousands_label_divides_by_1000_exactly_not_1001() -> None:
    """`1_000_000 / 1000 == 1000.0` (rounds to `1000k`); `1_000_000 / 1001 ==
    999.000999...` (rounds to `999k`) -- a value large enough that an off-by-one divisor
    changes the rounded result, unlike the smaller values above.
    """
    assert _thousands_label(1_000_000.0) == "1000k"


def test_tooltip_rows_exact_text_with_three_distinct_values() -> None:
    row = _row(
        completeness="cash_only",
        real_net_worth_display="111.11",
        savings_only_display="222.22",
        savings_flow_display="333.33",
    )
    assert _tooltip_rows(row) == [
        ("Patrimonio real", "111.11€ (cash_only)"),
        ("Solo ahorro", "222.22€"),
        ("Ahorro del mes", "333.33€"),
    ]


def test_tooltip_html_contains_month_asof_partial_and_gap_sign() -> None:
    row = _row(month_label="Mar 2027", as_of="2027-03-15", is_partial=True, gap_display="-50.00")
    payload = _tooltip_html(row)
    assert "Mar 2027 (parcial)" in payload
    assert "a fecha de 2027-03-15" in payload
    assert 'class="t-row t-gap loss"' in payload
    assert "-50.00€" in payload
    assert 'class="tooltip-close"' in payload


def test_tooltip_html_gain_class_for_a_non_negative_gap() -> None:
    payload = _tooltip_html(_row(gap_display="0.00"))
    assert 'class="t-row t-gap gain"' in payload


def test_tooltip_html_exact_text_with_four_distinct_values() -> None:
    """Four genuinely distinct display values, joined with no separator between rows and
    with the close button's exact markup pinned -- a dropped row, a stray join separator, or
    a case/wording change to the close button would otherwise be easy to miss.
    """
    row = _row(
        month_label="Mar 2027",
        as_of="2027-03-31",
        is_partial=False,
        completeness="cash_only",
        real_net_worth_display="111.11",
        savings_only_display="222.22",
        gap_display="333.33",
        savings_flow_display="444.44",
    )
    assert _tooltip_html(row) == (
        '<button type="button" class="tooltip-close" aria-label="Cerrar">×</button>'
        '<div class="t-month">Mar 2027</div>'
        '<div class="t-asof">a fecha de 2027-03-31</div>'
        '<div class="t-row"><span class="lab">Patrimonio real</span>'
        "<span>111.11€ (cash_only)</span></div>"
        '<div class="t-row"><span class="lab">Solo ahorro</span><span>222.22€</span></div>'
        '<div class="t-row"><span class="lab">Ahorro del mes</span><span>444.44€</span></div>'
        '<div class="t-row t-gap gain"><span class="lab">Hueco</span><span>+333.33€</span></div>'
    )


def test_tooltip_html_escapes_a_double_quote_in_the_month_label() -> None:
    payload = _tooltip_html(_row(month_label='Mar" onclick="alert(1)'))
    assert "onclick=" not in payload or "&quot;" in payload
    assert "<script" not in payload


def test_build_points_exact_padded_range() -> None:
    """`_build_points` pads the value range by 10% of its span on each side before scaling,
    so the lines never touch the plot's top/bottom edge -- pinned to exact values here (not
    just "the padding is nonzero") to catch a wrong operator or a dropped +=/-=.
    """
    rows = [
        _row(real_net_worth_position=0.0, savings_only_position=100.0),
        _row(real_net_worth_position=100.0, savings_only_position=0.0),
    ]
    points = section1_chart._build_points(rows)
    pad_top = section1_chart._PAD_TOP
    plot_height = section1_chart._PLOT_HEIGHT
    # span = 100, pad = 10 -> padded range [-10, 110], total span 120.
    assert points[0].real_y == pad_top + plot_height * (1 - 10 / 120)
    assert points[0].savings_y == pad_top + plot_height * (1 - 110 / 120)
    assert points[1].real_y == pad_top + plot_height * (1 - 110 / 120)
    assert points[1].savings_y == pad_top + plot_height * (1 - 10 / 120)


def test_build_points_pad_uses_the_span_not_the_sum() -> None:
    """`pad = (max - min) * 0.1`, not `(max + min) * 0.1` -- indistinguishable when min == 0,
    so this uses a range that does not start at zero.
    """
    rows = [
        _row(real_net_worth_position=10.0, savings_only_position=10.0),
        _row(real_net_worth_position=100.0, savings_only_position=100.0),
    ]
    points = section1_chart._build_points(rows)
    pad_top = section1_chart._PAD_TOP
    plot_height = section1_chart._PLOT_HEIGHT
    # span = 90, pad = 9 -> padded range [1, 109], total span 108.
    assert points[0].real_y == pad_top + plot_height * (1 - 9 / 108)
    assert points[1].real_y == pad_top + plot_height * (1 - 99 / 108)


def test_build_points_falls_back_to_a_fixed_pad_when_the_range_is_flat() -> None:
    """When every value is identical (`max - min == 0`), the `or 1.0` fallback applies so the
    padded range still has *some* span, rather than leaving min == max (a zero-division risk
    that `_y_scale`'s own `span == 0` branch would otherwise have to absorb silently).
    """
    rows = [_row(real_net_worth_position=50.0, savings_only_position=50.0)]
    points = section1_chart._build_points(rows)
    pad_top = section1_chart._PAD_TOP
    plot_height = section1_chart._PLOT_HEIGHT
    # padded range [49, 51], span 2 -> value 50 sits exactly at fraction 0.5.
    assert points[0].real_y == pad_top + plot_height * 0.5


def test_grid_ticks_are_evenly_spaced_including_both_ends() -> None:
    ticks = section1_chart._grid_ticks(0.0, 100.0)
    assert ticks == [0.0, 20.0, 40.0, 60.0, 80.0, 100.0]


def test_grid_ticks_span_uses_the_difference_not_the_sum() -> None:
    """`span = max - min`, not `max + min` -- indistinguishable when `min_value == 0` (the
    test above), so this uses a non-zero minimum: span is `40` (`50 - 10`), not `60`
    (`50 + 10`).
    """
    ticks = section1_chart._grid_ticks(10.0, 50.0)
    assert ticks == [10.0, 18.0, 26.0, 34.0, 42.0, 50.0]


def test_grid_line_svg_exact_markup() -> None:
    value, min_value, max_value = 1000.0, 0.0, 2000.0
    got = section1_chart._grid_line_svg(value, min_value, max_value)
    y = section1_chart._y_scale(value, min_value, max_value)
    pad_left = section1_chart._PAD_LEFT
    x2 = section1_chart._WIDTH - section1_chart._PAD_RIGHT
    assert got == (
        f'<line class="grid-line" x1="{pad_left:.2f}" x2="{x2:.2f}" '
        f'y1="{y:.2f}" y2="{y:.2f}"></line>\n'
        f'<text class="axis-label" x="{pad_left - 10:.2f}" y="{y + 4:.2f}" '
        'text-anchor="end">1k</text>'
    )


def test_points_payload_field_names_are_exact() -> None:
    row = _row(month_label="Mar 2027")
    point = _Point(x=1.0, real_y=2.0, savings_y=3.0, gap_value=0.0)
    payload = json.loads(section1_chart._points_payload([row], [point]))
    assert len(payload) == 1
    assert set(payload[0].keys()) == {"x", "realY", "savingsY", "label", "tooltip"}
    assert payload[0]["label"] == "Mar 2027"
    assert payload[0]["tooltip"] == _tooltip_html(row)


def test_points_payload_rounds_each_coordinate_to_two_decimal_places() -> None:
    """Uses values with more than two decimal digits (`1/3`, `2/3`, `1/7`) so a wrong
    rounding precision (or a dropped `point.x`/`point.real_y`/`point.savings_y` reference
    entirely) is distinguishable from the correct, rounded-to-2dp result.
    """
    row = _row(month_label="Mar 2027")
    point = _Point(x=1 / 3, real_y=2 / 3, savings_y=1 / 7, gap_value=0.0)
    payload = json.loads(section1_chart._points_payload([row], [point]))
    assert payload[0]["x"] == round(1 / 3, 2)
    assert payload[0]["realY"] == round(2 / 3, 2)
    assert payload[0]["savingsY"] == round(1 / 7, 2)


def test_render_gap_headline_shows_the_signed_final_gap() -> None:
    html_doc = render_section1_chart([_row(gap_display="10.00")])
    assert '<p class="sub">Hueco acumulado a cierre: <b>+10.00€</b></p>' in html_doc


def test_render_gap_headline_shows_a_negative_sign_for_a_negative_final_gap() -> None:
    html_doc = render_section1_chart([_row(gap_display="-5.00")])
    assert '<p class="sub">Hueco acumulado a cierre: <b>-5.00€</b></p>' in html_doc


def test_render_last_index_matches_row_count_minus_one() -> None:
    rows = [_row(month_label=f"M{i}") for i in range(4)]
    html_doc = render_section1_chart(rows)
    assert "var lastIndex = 3;" in html_doc


def test_render_grid_has_exactly_six_gridlines_and_one_baseline() -> None:
    """`_AXIS_TICKS + 1 == 6` gridlines, plus one bottom baseline -- both must be present
    together (a dropped `+=` in favour of `=` would silently replace the gridlines with just
    the baseline).
    """
    html_doc = render_section1_chart([_row()])
    assert html_doc.count('<line class="grid-line"') == 6
    assert html_doc.count('<line class="baseline"') == 1


def test_render_grid_join_uses_a_bare_newline_not_a_padded_separator() -> None:
    """The gridlines' `"\\n".join(...)` must join with exactly a newline -- a mutated
    separator (e.g. `"XX\\nXX"`) would insert stray literal text between every pair of
    elements. Scoped to `grid-line` specifically (not `baseline`, appended separately via
    `+=` right after) so this can only match a join *between two ticks*, not the one legitimate
    plain-newline concatenation onto the baseline that exists regardless of this mutation.
    There are `_AXIS_TICKS + 1 = 6` ticks, so 5 such internal junctions.
    """
    html_doc = render_section1_chart([_row()])
    assert html_doc.count('</text>\n<line class="grid-line"') == 5


def test_render_x_label_join_uses_a_bare_newline_not_a_padded_separator() -> None:
    """Same check for the x-axis month labels' own join. Two rows (both always shown: index 0
    and the last index) join directly adjacent to each other with no skipped element between
    them, so their join separator is directly observable as a single substring -- with 3+
    rows, a skipped middle label contributes an *empty* string to the join, which would make
    a plain single-`\\n` search pass even under the mutant (two joins either side of an empty
    element look the same either way), as a first attempt at this test found.
    """
    html_doc = render_section1_chart([_row(month_label="M0"), _row(month_label="M1")])
    assert 'text-anchor="middle">M0</text>\n<text class="axis-label"' in html_doc


def test_render_baseline_sits_at_the_bottom_of_the_plot_spanning_its_full_width() -> None:
    html_doc = render_section1_chart([_row()])
    baseline_y = section1_chart._PAD_TOP + section1_chart._PLOT_HEIGHT
    x2 = section1_chart._WIDTH - section1_chart._PAD_RIGHT
    match = re.search(r'<line class="baseline"[^>]*>', html_doc)
    assert match is not None
    assert f'x1="{section1_chart._PAD_LEFT:.2f}"' in match.group(0)
    assert f'x2="{x2:.2f}"' in match.group(0)
    assert f'y1="{baseline_y:.2f}"' in match.group(0)
    assert f'y2="{baseline_y:.2f}"' in match.group(0)


def test_render_end_labels_sit_4px_below_their_points_not_above() -> None:
    """`y = point_y + 4` (a small downward nudge so the label's text baseline centres on the
    point, not `- 4`, and not `+ 5`) -- pinned exactly, since a plausible-looking off-by-one
    would only be visible as a few misplaced pixels, never a wrong count or missing element.
    """
    row = _row(real_net_worth_position=10.0, savings_only_position=90.0, completeness="other")  # type: ignore[arg-type]
    html_doc = render_section1_chart([row])
    points = section1_chart._build_points([row])
    point = points[0]
    real_match = re.search(r'class="end-label real"[^>]*y="([^"]*)"', html_doc)
    ahorro_match = re.search(r'class="end-label ahorro"[^>]*y="([^"]*)"', html_doc)
    assert real_match is not None
    assert ahorro_match is not None
    assert real_match.group(1) == f"{point.real_y + 4:.2f}"
    assert ahorro_match.group(1) == f"{point.savings_y + 4:.2f}"


def test_render_empty_series_returns_placeholder_without_raising() -> None:
    html_doc = render_section1_chart([])
    assert "No hay datos" in html_doc
    assert "<svg" not in html_doc


def test_render_is_a_pure_function_of_its_input() -> None:
    rows = [_row(), _row(month_label="Abr 2027", real_net_worth_position=50.0)]
    assert render_section1_chart(rows) == render_section1_chart(rows)


def test_real_and_ahorro_paths_contain_actual_coordinates() -> None:
    """Each line's `d` attribute is a real `M .. L ..` coordinate path, not a dropped/`None`
    value silently stringified into the template.
    """
    rows = [_row(real_net_worth_position=10.0), _row(real_net_worth_position=20.0)]
    html_doc = render_section1_chart(rows)
    real_match = re.search(r'class="line-real" d="([^"]*)"', html_doc)
    ahorro_match = re.search(r'class="line-ahorro" d="([^"]*)"', html_doc)
    assert real_match is not None
    assert ahorro_match is not None
    coord_re = re.compile(r"^M -?\d+\.\d{2},-?\d+\.\d{2}(?: L -?\d+\.\d{2},-?\d+\.\d{2})*$")
    assert coord_re.match(real_match.group(1))
    assert coord_re.match(ahorro_match.group(1))
    assert real_match.group(1) != ahorro_match.group(1)


def test_band_svg_polygons_are_newline_joined_not_concatenated() -> None:
    """With two segments (three rows), the band group must contain two `<polygon>` tags
    separated by an actual newline -- a dropped separator or a `None` join would either
    concatenate them illegibly or drop the content entirely.
    """
    rows = [_row(month_label=f"M{i}") for i in range(3)]
    html_doc = render_section1_chart(rows)
    assert html_doc.count("<polygon") >= 1
    band_section = html_doc.split('<g class="band">')[1].split("</g>")[0]
    assert "None" not in band_section
    if band_section.count("<polygon") > 1:
        assert "</polygon>\n<polygon" in band_section


def test_one_shared_hit_area_regardless_of_row_count() -> None:
    """Unlike WP-9's original one-circle-per-point design, the port uses a single shared
    `.hit-area` (per the mock) whose click position is mapped to the nearest month index in
    JS -- so the count of hit targets must always be 1, not `len(rows)`.
    """
    rows = [_row(month_label=f"M{i}") for i in range(5)]
    html_doc = render_section1_chart(rows)
    assert html_doc.count('class="hit-area"') == 1


def test_points_payload_has_one_entry_per_row() -> None:
    rows = [_row(month_label=f"M{i}") for i in range(4)]
    html_doc = render_section1_chart(rows)
    match = re.search(r"var points = (\[.*?\]);", html_doc, re.DOTALL)
    assert match is not None
    payload = json.loads(match.group(1))
    assert len(payload) == 4
    assert [p["label"] for p in payload] == ["M0", "M1", "M2", "M3"]


def test_points_payload_escapes_a_closing_script_tag_in_month_label() -> None:
    """A month label containing a literal `</script>` must never be able to prematurely close
    the page's own `<script>` block -- the HTML parser tokenizes `</script` before any JS
    inside it is parsed, so this must be escaped regardless of `json.dumps`'s own separate
    string-literal escaping.
    """
    row = _row(month_label="Mar</script><script>alert(1)</script>")
    html_doc = render_section1_chart([row])
    # Only this module's own single closing tag remains a real `</script>`; any would-be
    # injected ones from the month label must come out escaped as `<\/script>` instead -- a
    # literal (non-closing) `<script>` substring surviving mid-string is harmless (per the
    # HTML spec, a script element's raw text is only ever terminated by `</script`, so an
    # *opening* tag appearing inside it is inert), so this checks the closing tag count only.
    assert html_doc.count("</script>") == 1
    assert "<\\/script>" in html_doc


def test_double_quote_in_tooltip_content_is_html_escaped() -> None:
    """`month_label` (unlike `completeness`, which only ever feeds an `== "cash_only"`
    comparison and is never echoed verbatim) is placed into the tooltip's markup via
    `html.escape`, so a literal `"` in it must never survive unescaped.
    """
    row = _row(month_label='Mar" onmouseover="alert(1)')
    html_doc = render_section1_chart([row])
    assert 'onmouseover="alert(1)"' not in html_doc
    assert "&quot;" in html_doc


def test_multiple_rows_each_produce_their_own_x_label_or_are_skipped_for_crowding() -> None:
    """Every-other-month labelling (ported from the mock) plus always the last month: for 5
    rows, indices 0, 2, 4 get a label (3 of them), not all 5.
    """
    rows = [_row(month_label=f"M{i}") for i in range(5)]
    html_doc = render_section1_chart(rows)
    # Scoped to the axis label's own markup specifically (`text-anchor="middle"`, unique to
    # `_x_label_svg`) -- every row's month label also appears, unconditionally, inside its own
    # tooltip payload elsewhere in the document, which a bare `>M{i}<` substring search would
    # otherwise false-match regardless of whether that row's *axis* label was skipped.
    for i in (0, 2, 4):
        assert f'text-anchor="middle">M{i}</text>' in html_doc
    for i in (1, 3):
        assert f'text-anchor="middle">M{i}</text>' not in html_doc


def test_last_month_label_always_shown_even_when_it_would_otherwise_be_skipped() -> None:
    """With 4 rows (indices 0..3), the skip rule alone would omit index 3 (odd, not a
    multiple of 2) -- but it is the *last* row, so it must still get a label.
    """
    rows = [_row(month_label=f"M{i}") for i in range(4)]
    html_doc = render_section1_chart(rows)
    assert 'text-anchor="middle">M3</text>' in html_doc


def test_x_label_svg_exact_markup_for_a_shown_label() -> None:
    row = _row(month_label="Mar 2027")
    point = _Point(x=123.45, real_y=0.0, savings_y=0.0, gap_value=0.0)
    got = section1_chart._x_label_svg(row, point, index=0, count=1)
    expected_y = section1_chart._HEIGHT - section1_chart._PAD_BOTTOM + 18
    assert got == (
        f'<text class="axis-label" x="123.45" y="{expected_y:.2f}" '
        'text-anchor="middle">Mar 2027</text>'
    )


def test_x_label_svg_skips_an_odd_non_last_index() -> None:
    row = _row(month_label="Mar 2027")
    point = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=0.0)
    assert section1_chart._x_label_svg(row, point, index=1, count=5) == ""


def test_x_label_svg_last_index_check_is_count_minus_one_not_count_plus_one() -> None:
    """The "always show the last one" exception compares `index != count - 1`; with `count`
    itself as a plausible-looking off-by-two typo (`count + 1`), index 3 of a 4-row series
    (the real last index) would wrongly be skipped -- `count + 1` (5) never equals any real
    index, so the exception would never fire at all.
    """
    row = _row(month_label="Mar 2027")
    point = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=0.0)
    assert section1_chart._x_label_svg(row, point, index=3, count=4) != ""
