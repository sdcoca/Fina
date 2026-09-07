"""Fast, pure-Python tests for fina.render.section1_chart (WP-9): R-10.1..R-10.5.

Real-browser visual verification (T-705..T-708, which genuinely need a rendered page) lives
in tests/test_render_browser.py, excluded from mutmut's own test run (like test_gates_meta.py)
since re-launching a browser per mutant would be prohibitively slow for no unique coverage --
every line of this module's own Python code is already covered by the tests here.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from fina.render import section1_chart
from fina.render.section1_chart import (
    ChartRow,
    _band_polygons,
    _format_eur,
    _lerp,
    _Point,
    _polyline,
    _quad,
    _tooltip_payload,
    _x_scale,
    _y_scale,
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
    assert cls_a == "band-positive"
    assert cls_b == "band-negative"
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
    assert polygons[0][1] == "band-positive"


def test_band_segment_entirely_negative_is_a_single_negative_polygon() -> None:
    a = _Point(x=0.0, real_y=0.0, savings_y=10.0, gap_value=-5.0)
    b = _Point(x=10.0, real_y=1.0, savings_y=9.0, gap_value=-3.0)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 1
    assert polygons[0][1] == "band-negative"


def test_zero_gap_endpoint_counts_as_non_negative_not_a_crossing() -> None:
    """`gap == 0` is treated as the non-negative side (`>= 0`), so a segment from 0 to a
    positive value is one polygon, not a spurious split.
    """
    a = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=0.0)
    b = _Point(x=10.0, real_y=0.0, savings_y=0.0, gap_value=5.0)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 1
    assert polygons[0][1] == "band-positive"


def test_second_endpoint_of_zero_gap_also_counts_as_non_negative() -> None:
    """The `>= 0` check applies to *both* endpoints, not just the first: a segment ending
    exactly at gap == 0, from a positive start, is also one polygon, not a crossing.
    """
    a = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=5.0)
    b = _Point(x=10.0, real_y=0.0, savings_y=0.0, gap_value=0.0)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 1
    assert polygons[0][1] == "band-positive"


def test_second_endpoint_threshold_is_zero_not_one() -> None:
    """The non-negative threshold is exactly 0, not silently 1: a segment from a small
    positive value (0.5) to a larger one must not be seen as a "sign change" from the first
    endpoint's perspective.
    """
    a = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=0.0)
    b = _Point(x=10.0, real_y=0.0, savings_y=0.0, gap_value=0.5)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 1
    assert polygons[0][1] == "band-positive"


def test_crossing_segment_classifies_a_boundary_start_point_correctly() -> None:
    """A crossing segment starting exactly at gap == 0 (the non-negative boundary) must
    classify that side as `band-positive`, not `band-negative` -- and the *other* side, well
    below zero, as `band-negative`.
    """
    a = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=0.0)
    b = _Point(x=10.0, real_y=0.0, savings_y=0.0, gap_value=-10.0)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 2
    assert polygons[0][1] == "band-positive"
    assert polygons[1][1] == "band-negative"


def test_crossing_segment_classifies_a_negative_start_point_correctly() -> None:
    a = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=-5.0)
    b = _Point(x=10.0, real_y=0.0, savings_y=0.0, gap_value=5.0)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 2
    assert polygons[0][1] == "band-negative"
    assert polygons[1][1] == "band-positive"


def test_crossing_segment_classifies_a_boundary_end_point_correctly() -> None:
    """The end point of a crossing segment exactly at gap == 0 must also classify as
    `band-positive`, not `band-negative`.
    """
    a = _Point(x=0.0, real_y=0.0, savings_y=0.0, gap_value=-5.0)
    b = _Point(x=10.0, real_y=0.0, savings_y=0.0, gap_value=0.0)
    polygons = _band_polygons([a, b])
    assert len(polygons) == 2
    assert polygons[0][1] == "band-negative"
    assert polygons[1][1] == "band-positive"


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
    assert polygons[0][1] == "band-positive"
    assert polygons[1][1] == "band-positive"
    assert polygons[2][1] == "band-negative"


# ---------------------------------------------------------------------------
# T-703 / R-10.2: click-only tooltip, no hover handler
# ---------------------------------------------------------------------------


def test_t703_no_hover_event_handler_is_emitted() -> None:
    html_doc = render_section1_chart([_row(), _row(month_label="Apr 2027")])
    lowered = html_doc.lower()
    for hover_token in ("mouseover", "mouseenter", "mousemove", "onmouseover", ":hover"):
        assert hover_token not in lowered, hover_token


def test_t703_click_and_keydown_handlers_are_emitted() -> None:
    html_doc = render_section1_chart([_row()])
    assert '"click"' in html_doc
    assert '"keydown"' in html_doc


# ---------------------------------------------------------------------------
# T-704 / R-10.5: cash_only qualifier travels with the figure, no separate footer
# ---------------------------------------------------------------------------


def test_t704_cash_only_qualifier_is_rendered_on_the_value_label() -> None:
    html_doc = render_section1_chart([_row(completeness="cash_only")])
    value_label_match = re.search(r'class="value-label real"[^>]*>([^<]*)</text>', html_doc)
    assert value_label_match is not None
    assert value_label_match.group(1) == "100.00€ (cash_only)"


def test_value_labels_sit_at_the_last_points_x_plus_8_exactly() -> None:
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
    real_x_match = re.search(r'class="value-label real" x="([^"]*)"', html_doc)
    savings_x_match = re.search(r'class="value-label savings" x="([^"]*)"', html_doc)
    assert real_x_match is not None
    assert savings_x_match is not None
    assert real_x_match.group(1) == expected_x
    assert savings_x_match.group(1) == expected_x


def test_savings_value_label_shows_the_exact_savings_display_value() -> None:
    row = _row(savings_only_display="12345.67", completeness="other")  # type: ignore[arg-type]
    html_doc = render_section1_chart([row])
    savings_label_match = re.search(r'class="value-label savings"[^>]*>([^<]*)</text>', html_doc)
    assert savings_label_match is not None
    assert savings_label_match.group(1) == "12345.67€"


def test_t704_qualifier_omitted_when_completeness_is_not_cash_only() -> None:
    html_doc = render_section1_chart([_row(completeness="other")])  # type: ignore[arg-type]
    value_label_match = re.search(r'class="value-label real"[^>]*>([^<]*)</text>', html_doc)
    assert value_label_match is not None
    assert value_label_match.group(1) == "100.00€"


def test_no_separate_explanatory_footer_element() -> None:
    """R-10.2: no explanatory footer -- there must be no `<footer>` element, and the only
    paragraph-shaped text on the page is the (removed) empty-series placeholder, never
    present alongside a real chart.
    """
    html_doc = render_section1_chart([_row()])
    assert "<footer" not in html_doc
    assert "<p>" not in html_doc
    assert "<p " not in html_doc


def test_no_legend_element_for_band_colours() -> None:
    html_doc = render_section1_chart([_row(), _row(gap_position=-1.0, gap_display="-1.00")])
    assert "legend" not in html_doc.lower()


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
    x = _x_scale(0, 1)
    assert x == section1_chart._PAD_LEFT + section1_chart._PLOT_WIDTH / 2


def test_x_scale_spans_the_full_plot_width_for_first_and_last() -> None:
    first = _x_scale(0, 5)
    last = _x_scale(4, 5)
    assert first == section1_chart._PAD_LEFT
    assert last == section1_chart._PAD_LEFT + section1_chart._PLOT_WIDTH


def test_x_scale_with_exactly_two_points_spans_the_full_width() -> None:
    """The single-point centering branch triggers only for `count <= 1`, not `<= 2`: with
    exactly two points, they must sit at the two ends of the plot, not both at the centre.
    """
    first = _x_scale(0, 2)
    second = _x_scale(1, 2)
    assert first == section1_chart._PAD_LEFT
    assert second == section1_chart._PAD_LEFT + section1_chart._PLOT_WIDTH
    assert first != second


def test_x_scale_exact_value_at_an_interior_index() -> None:
    # index 1 of 4 (count=4, so 3 gaps): PAD_LEFT + PLOT_WIDTH * 1/3.
    x = _x_scale(1, 4)
    assert x == section1_chart._PAD_LEFT + section1_chart._PLOT_WIDTH / 3


def test_y_scale_flat_series_is_centered() -> None:
    y = _y_scale(5.0, 5.0, 5.0)
    assert y == section1_chart._PAD_TOP + section1_chart._PLOT_HEIGHT / 2


def test_y_scale_exact_values_at_min_max_and_quarter_points() -> None:
    pad_top = section1_chart._PAD_TOP
    plot_height = section1_chart._PLOT_HEIGHT
    # value == min_value -> fraction 0 -> bottom of the plot (y = pad_top + plot_height).
    assert _y_scale(0.0, 0.0, 100.0) == pad_top + plot_height
    # value == max_value -> fraction 1 -> top of the plot (y = pad_top).
    assert _y_scale(100.0, 0.0, 100.0) == pad_top
    # value a quarter of the way up -> fraction 0.25 -> y = pad_top + plot_height * 0.75.
    assert _y_scale(25.0, 0.0, 100.0) == pad_top + plot_height * 0.75
    # A negative-value span, to rule out any bug that only shows up with min_value < 0.
    assert _y_scale(-10.0, -20.0, 0.0) == pad_top + plot_height * 0.5


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


def test_format_eur_appends_symbol_with_no_space() -> None:
    assert _format_eur("100.00") == "100.00€"


def test_tooltip_payload_has_no_wrapping_risk_lines_are_short_labels() -> None:
    payload = _tooltip_payload(_row(is_partial=True))
    assert "(parcial)" in payload
    lines = payload.split("\n")
    # Every *label* line (odd design: label, then value, alternating after the header) is
    # short and fixed regardless of the money values -- a coarse proxy for T-706's real
    # rendered-width check.
    assert all(len(line) < 40 for line in lines)


def test_tooltip_payload_exact_text_with_four_distinct_values() -> None:
    """Four genuinely distinct display values pin down that each one appears in its own,
    correct line -- a value silently swapped for another (or dropped to `None€`) would
    otherwise be easy to miss with same-valued fixture data.
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
    assert _tooltip_payload(row) == (
        "Mar 2027\n"
        "a fecha de 2027-03-31\n"
        "Patrimonio (cash_only)\n111.11€\n"
        "Solo ahorro\n222.22€\n"
        "Diferencia (gap)\n333.33€\n"
        "Ahorro del mes\n444.44€"
    )


def test_tooltip_payload_partial_suffix_exact_text() -> None:
    payload = _tooltip_payload(_row(month_label="Mar 2027", is_partial=True))
    assert payload.startswith("Mar 2027 (parcial)\n")


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


def test_render_empty_series_returns_placeholder_without_raising() -> None:
    html_doc = render_section1_chart([])
    assert "No hay datos" in html_doc
    assert "<svg" not in html_doc


def test_render_is_a_pure_function_of_its_input() -> None:
    rows = [_row(), _row(month_label="Abr 2027", real_net_worth_position=50.0)]
    assert render_section1_chart(rows) == render_section1_chart(rows)


def test_real_and_savings_polylines_contain_actual_coordinates() -> None:
    """Each polyline's `points` attribute is a real coordinate list, not a dropped/`None`
    value silently stringified into the template.
    """
    rows = [_row(real_net_worth_position=10.0), _row(real_net_worth_position=20.0)]
    html_doc = render_section1_chart(rows)
    real_match = re.search(r'class="line-real" points="([^"]*)"', html_doc)
    savings_match = re.search(r'class="line-savings" points="([^"]*)"', html_doc)
    assert real_match is not None
    assert savings_match is not None
    coord_re = re.compile(r"^-?\d+\.\d{2},-?\d+\.\d{2}(?: -?\d+\.\d{2},-?\d+\.\d{2})*$")
    assert coord_re.match(real_match.group(1))
    assert coord_re.match(savings_match.group(1))
    assert real_match.group(1) != savings_match.group(1)


def test_band_svg_polygons_are_newline_joined_not_concatenated() -> None:
    """With two segments (three rows), `band_svg` must contain two `<polygon>` tags
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


def test_double_quote_in_month_label_is_escaped_in_the_aria_label_attribute() -> None:
    """`html.escape(row.month_label, quote=True)` -- not `False`/`None`/omitted: a literal
    `"` in the label must never be able to break out of the `aria-label="..."` attribute.
    """
    row = _row(month_label='Mar" data-injected="1')
    html_doc = render_section1_chart([row])
    aria_match = re.search(r'aria-label="([^"]*)"', html_doc)
    assert aria_match is not None
    assert aria_match.group(1) == "Mar&quot; data-injected=&quot;1"


def test_double_quote_in_tooltip_payload_is_escaped_in_the_data_tooltip_attribute() -> None:
    """Same as above, for `data-tooltip`, which is built from `_tooltip_payload` (itself
    built from `row.completeness`, an otherwise-free-form field).
    """
    row = _row(completeness='cash_only" data-injected="1')  # type: ignore[arg-type]
    html_doc = render_section1_chart([row])
    tooltip_match = re.search(r'data-tooltip="([^"]*)"', html_doc)
    assert tooltip_match is not None
    assert "&quot;" in tooltip_match.group(1)
    assert 'data-injected="1"' not in html_doc


def test_multiple_rows_produce_one_hit_circle_each() -> None:
    rows = [_row(month_label=f"M{i}") for i in range(4)]
    html_doc = render_section1_chart(rows)
    assert html_doc.count('class="hit"') == 4


def test_hit_circles_are_newline_joined_not_concatenated() -> None:
    rows = [_row(month_label=f"M{i}") for i in range(3)]
    html_doc = render_section1_chart(rows)
    hits_section = html_doc.split('<g class="hits">')[1].split("</g>")[0]
    assert "</circle>\n<circle" in hits_section
