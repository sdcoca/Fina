"""Real-browser visual verification for fina.render.section1_chart (WP-9, re-derived for
Q-G): T-705..T-711.

R-10.2a: verified by actually rendering the page at a 390px viewport first, desktop second --
never inferred from reading the CSS. Excluded from mutmut's own test run (see pyproject.toml)
since these tests verify the *browser's* rendering behaviour, not additional Python line
coverage -- every line of section1_chart.py is already covered by test_section1_chart.py.

Q-G: the port replaced WP-9's original per-point `circle.hit` targets with a single shared
`.hit-area` (matching the approved mock's own single hit-rect + click-position-to-index
mapping), and the tooltip's markup with the mock's own month-header/`.t-row`/bordered-gap-row
structure (`#s1-tooltip`, class `visible` when open) instead of a single `.tooltip-body` text
blob (class `open`). The tests below click by *position* within the shared hit-area rather than
by locating a per-point element, and check each tooltip row individually for wrapping --
the same T-70x rules, verified against the new markup.

T-710/T-711 (G-9, `docs/plan/test-plan.md` -- added per Q-J, `docs/plan/open-questions.md`):
three real visual defects (a tooltip value-column overflow, an SVG end-label overflow, and a
mobile tap-highlight artifact) reached a real device despite every test above being green,
because none of them checked a general-purpose, content-agnostic containment property, and none
of them ever drove a real touch event. T-710/T-711 close that systemic gap.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page

from browser_support import launch_chromium
from fina.render.section1_chart import ChartRow, render_section1_chart

_MOBILE_VIEWPORT = {"width": 390, "height": 844}
_DESKTOP_VIEWPORT = {"width": 1280, "height": 800}


def _sample_series() -> list[ChartRow]:
    rows: list[ChartRow] = []
    start = date(2027, 1, 1)
    values = [
        (1000.0, 1000.0),
        (1500.0, 1200.0),
        (900.0, 1400.0),  # dips below savings_only -> a genuine negative-gap month
        (2000.0, 1600.0),
        (2500.0, 1800.0),
    ]
    for i, (real, savings) in enumerate(values):
        month = start.month + i
        year = start.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        rows.append(
            ChartRow(
                month_label=date(year, month, 1).strftime("%b %Y"),
                as_of=date(year, month, 28).isoformat(),
                is_partial=(i == len(values) - 1),
                completeness="cash_only",
                real_net_worth_position=real,
                savings_only_position=savings,
                gap_position=real - savings,
                real_net_worth_display=f"{real:.2f}",
                savings_only_display=f"{savings:.2f}",
                gap_display=f"{real - savings:.2f}",
                savings_flow_display="123.45",
            )
        )
    return rows


def _worst_case_series() -> list[ChartRow]:
    """A synthetic series with real headroom to fail against (G-9 / Q-J): every figure a
    9-digit euro amount, a genuine loss (minus sign) on the final, partial month, and every
    tooltip row therefore at its longest plausible length -- deliberately not today's fixture
    numbers, so `T-710` proves the fix has margin for *future* content, not just today's.
    """
    rows: list[ChartRow] = []
    start = date(2027, 1, 1)
    # (real_net_worth, savings_only): the last entry is a large loss, the worst case for the
    # signed gap figure and for the end-of-line label's width alike.
    values = [
        (123456789.01, 100000000.00),
        (150000000.50, 120000000.25),
        (-999999999.99, 987654321.12),
    ]
    for i, (real, savings) in enumerate(values):
        month = start.month + i
        year = start.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        rows.append(
            ChartRow(
                month_label=date(year, month, 1).strftime("%b %Y"),
                as_of=date(year, month, 28).isoformat(),
                is_partial=(i == len(values) - 1),
                completeness="cash_only",
                real_net_worth_position=real,
                savings_only_position=savings,
                gap_position=real - savings,
                real_net_worth_display=f"{real:.2f}",
                savings_only_display=f"{savings:.2f}",
                gap_display=f"{real - savings:.2f}",
                savings_flow_display="-999999999.99",
            )
        )
    return rows


def _write_chart(tmp_path: Path, rows: list[ChartRow] | None = None) -> Path:
    html_doc = render_section1_chart(rows if rows is not None else _sample_series())
    path = tmp_path / "chart.html"
    path.write_text(html_doc, encoding="utf-8")
    return path


def _click_month(page: Page, index: int, count: int) -> None:
    """Clicks the shared `.hit-area` at the horizontal position corresponding to month
    `index` of `count` -- the port's interaction model (ported from the mock) maps a single
    click position to the nearest month index in JS, rather than exposing one element per
    point (see this module's own docstring).
    """
    box = page.locator(".hit-area").bounding_box()
    assert box is not None
    fraction = index / (count - 1) if count > 1 else 0.5
    # Clamped 2px in from each edge: a click landing exactly on the SVG rect's own boundary
    # pixel is unreliable across renderers (sub-pixel rounding can place it just outside the
    # element), which is a test-harness precision concern, not a real interaction gap -- every
    # fraction strictly between the edges hits the element consistently.
    x = min(max(box["x"] + box["width"] * fraction, box["x"] + 2), box["x"] + box["width"] - 2)
    y = box["y"] + box["height"] / 2
    page.mouse.click(x, y)


# ---------------------------------------------------------------------------
# T-705 / R-10.1: PDF export smoke test
# ---------------------------------------------------------------------------


def test_t705_pdf_export_produces_a_non_empty_single_page_file(tmp_path: Path) -> None:
    chart_path = _write_chart(tmp_path)
    pdf_path = tmp_path / "chart.pdf"
    with launch_chromium() as browser:
        page = browser.new_page()
        page.goto(chart_path.as_uri())
        page.pdf(path=str(pdf_path))
    assert pdf_path.exists()
    data = pdf_path.read_bytes()
    assert len(data) > 0
    page_count = data.count(b"/Type /Page") - data.count(b"/Type /Pages")
    assert page_count == 1


# ---------------------------------------------------------------------------
# T-706 / R-10.2/R-10.2a: at 390px, tooltip text never wraps, for every month
# ---------------------------------------------------------------------------


def test_t706_tooltip_text_does_not_wrap_at_390px_for_every_month(tmp_path: Path) -> None:
    chart_path = _write_chart(tmp_path)
    count = len(_sample_series())
    with launch_chromium() as browser:
        page = browser.new_page(viewport=_MOBILE_VIEWPORT)
        page.goto(chart_path.as_uri())
        for i in range(count):
            _click_month(page, i, count)
            page.wait_for_timeout(20)
            rows = page.locator("#s1-tooltip .t-month, #s1-tooltip .t-asof, #s1-tooltip .t-row")
            row_count = rows.count()
            assert row_count > 0
            for r in range(row_count):
                row = rows.nth(r)
                height = row.bounding_box()["height"]
                line_height = row.evaluate("el => parseFloat(getComputedStyle(el).lineHeight)")
                actual_lines = round(height / line_height)
                assert actual_lines == 1, (
                    f"month index {i}, row {r}: tooltip text wrapped "
                    f"({actual_lines} rendered lines, expected 1)"
                )
            # Click well outside the chart to close before the next iteration selects a new
            # point -- the open tooltip itself can visually cover the whole plot area at this
            # viewport width, so a second click *inside* the chart is not guaranteed to reach
            # the hit-area underneath it (R-10.2 only requires dismissal via the close control
            # or a click outside, both of which this uses elsewhere -- never a same-point
            # re-click, which this design does not guarantee reaches the hit-area at all).
            page.mouse.click(5, 5)
            page.wait_for_timeout(20)


# ---------------------------------------------------------------------------
# T-707 / R-10.2/R-10.2b: visible translucency over a coloured band, at 390px
# ---------------------------------------------------------------------------


def test_t707_tooltip_is_measurably_translucent_over_the_band(tmp_path: Path) -> None:
    chart_path = _write_chart(tmp_path)
    count = len(_sample_series())
    with launch_chromium() as browser:
        page = browser.new_page(viewport=_MOBILE_VIEWPORT)
        page.goto(chart_path.as_uri())
        # Point index 3 sits well inside a positive-gap (blue "gain" band) segment, per the
        # sample series above (real=2000 > savings=1600 there); the tooltip opens near the top
        # of the chart (ported from the mock), which is within that band's vertical extent.
        _click_month(page, 3, count)
        page.wait_for_timeout(20)
        box = page.locator("#s1-tooltip").bounding_box()
        assert box is not None
        screenshot = page.screenshot()
        tooltip_pixel = _read_pixel(
            screenshot, int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)
        )
        # Sample the flat page background far from any chart content (bottom-right corner of
        # the viewport, always outside the chart card and the tooltip).
        plain_pixel = _read_pixel(screenshot, 385, 840)
        assert tooltip_pixel != plain_pixel, (
            "tooltip pixel over the coloured band is identical to a plain background pixel "
            "-- translucency is not actually visible"
        )


def _read_pixel(png_bytes: bytes, x: int, y: int) -> tuple[int, int, int]:
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(png_bytes)) as img:
        return img.convert("RGB").getpixel((x, y))


# ---------------------------------------------------------------------------
# T-708 / R-10.2a: 390px verified first; desktop is a separate, additional case
# ---------------------------------------------------------------------------


def test_t708_desktop_pass_is_additional_not_a_substitute_for_mobile(tmp_path: Path) -> None:
    """Runs the same tooltip-open/no-crash check at a desktop width, *after* mobile has
    already been verified above by T-706/T-707 -- this test alone proves nothing about
    mobile and must never be read as satisfying R-10.2a on its own.
    """
    chart_path = _write_chart(tmp_path)
    count = len(_sample_series())
    with launch_chromium() as browser:
        page = browser.new_page(viewport=_DESKTOP_VIEWPORT)
        page.goto(chart_path.as_uri())
        _click_month(page, 0, count)
        page.wait_for_timeout(20)
        assert "visible" in (page.locator("#s1-tooltip").get_attribute("class") or "")


# ---------------------------------------------------------------------------
# Additional interaction checks specific to the mock's port (not independently T-numbered,
# but covering R-10.2's "click-only, dismissible" contract at the same rigor as T-706..T-708)
# ---------------------------------------------------------------------------


def test_close_button_dismisses_the_tooltip(tmp_path: Path) -> None:
    chart_path = _write_chart(tmp_path)
    count = len(_sample_series())
    with launch_chromium() as browser:
        page = browser.new_page(viewport=_MOBILE_VIEWPORT)
        page.goto(chart_path.as_uri())
        _click_month(page, 2, count)
        page.wait_for_timeout(20)
        page.locator("#s1-tooltip .tooltip-close").click()
        page.wait_for_timeout(20)
        assert "visible" not in (page.locator("#s1-tooltip").get_attribute("class") or "")


def test_clicking_outside_the_chart_dismisses_the_tooltip(tmp_path: Path) -> None:
    chart_path = _write_chart(tmp_path)
    count = len(_sample_series())
    with launch_chromium() as browser:
        page = browser.new_page(viewport=_MOBILE_VIEWPORT)
        page.goto(chart_path.as_uri())
        _click_month(page, 0, count)
        page.wait_for_timeout(20)
        page.mouse.click(5, 5)
        page.wait_for_timeout(20)
        assert "visible" not in (page.locator("#s1-tooltip").get_attribute("class") or "")


# ---------------------------------------------------------------------------
# T-710 / G-9 / R-10.6: every text-bearing element stays inside its intended container --
# general and content-agnostic, not pinned to today's copy or figure lengths (Q-J).
# ---------------------------------------------------------------------------

# Collects, for every element under <body> whose *own direct* text content (ignoring
# descendant elements) is non-empty, its bounding box and whether it sits inside the tooltip
# (`#s1-tooltip`) or not. Deliberately does not name any specific class/string: any element
# with visible text is checked, so this also catches a defect from text this chart does not
# render today. <script>/<style> are excluded -- their "text content" is source code, not
# rendered content, and would otherwise false-positively dwarf every real element.
_TEXT_LEAF_BOXES_JS = """
() => {
  const out = [];
  function walk(el) {
    if (el.tagName === "SCRIPT" || el.tagName === "STYLE") return;
    for (const child of el.children) walk(child);
    let text = "";
    for (const node of el.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) text += node.textContent;
    }
    if (text.trim().length > 0) {
      const rect = el.getBoundingClientRect();
      out.push({
        tag: el.tagName,
        cls: el.getAttribute("class") || "",
        text: text.trim(),
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
        inTooltip: !!el.closest("#s1-tooltip"),
      });
    }
  }
  walk(document.body);
  return out;
}
"""

# Sub-pixel browser rounding noise only (getBoundingClientRect on a scaled/transformed SVG can
# differ from its container by a fraction of a pixel even with zero real overflow) -- not a
# masking of the containment check itself, which is otherwise zero-tolerance (G-9).
_CONTAINMENT_EPSILON_PX = 1.0


def _assert_within(inner: dict[str, Any], outer: dict[str, Any], *, label: str) -> None:
    eps = _CONTAINMENT_EPSILON_PX
    assert inner["x"] >= outer["x"] - eps, (
        f"{label}: left edge at {inner['x']:.2f} is left of the container's own {outer['x']:.2f}"
    )
    assert inner["y"] >= outer["y"] - eps, (
        f"{label}: top edge at {inner['y']:.2f} is above the container's own {outer['y']:.2f}"
    )
    inner_right = inner["x"] + inner["width"]
    outer_right = outer["x"] + outer["width"]
    assert inner_right <= outer_right + eps, (
        f"{label}: right edge overflows its container by {inner_right - outer_right:.2f}px"
    )
    inner_bottom = inner["y"] + inner["height"]
    outer_bottom = outer["y"] + outer["height"]
    assert inner_bottom <= outer_bottom + eps, (
        f"{label}: bottom edge overflows its container by {inner_bottom - outer_bottom:.2f}px"
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("tooltip_open", [False, True], ids=["closed", "open"])
def test_t710_no_text_overflows_its_container(
    tmp_path: Path, theme: str, tooltip_open: bool
) -> None:
    """G-9: every text-bearing element renders fully inside its intended container -- SVG text
    (grid/axis/end-of-line labels) within the visible chart card (`.card`), tooltip text (month
    header, as-of line, every row's label/value, the gap row) within the tooltip's own box
    (`.tooltip`) -- at a 390px viewport, in both themes, whether the tooltip is open or closed.
    Uses `_worst_case_series` (large multi-digit euro figures, a genuine loss) deliberately
    instead of today's fixture numbers, so this has real headroom to fail against, per R-10.6.
    """
    rows = _worst_case_series()
    chart_path = _write_chart(tmp_path, rows)
    count = len(rows)
    with launch_chromium() as browser:
        page = browser.new_page(viewport=_MOBILE_VIEWPORT)
        page.emulate_media(color_scheme=theme)
        page.goto(chart_path.as_uri())
        if tooltip_open:
            _click_month(page, count - 1, count)  # the largest-loss, partial month
            page.wait_for_timeout(20)

        card_box = page.locator(".card").bounding_box()
        tooltip_box = page.locator("#s1-tooltip").bounding_box()
        assert card_box is not None
        items = page.evaluate(_TEXT_LEAF_BOXES_JS)

        checked_tooltip_text = False
        for item in items:
            label = f"{item['tag']}.{item['cls']} {item['text']!r}"
            if item["inTooltip"]:
                assert tooltip_open, f"{label}: tooltip text present while tooltip is closed"
                assert tooltip_box is not None
                _assert_within(item, tooltip_box, label=label)
                checked_tooltip_text = True
            else:
                _assert_within(item, card_box, label=label)

        if tooltip_open:
            assert checked_tooltip_text, "tooltip was opened but no tooltip text was found"


def test_t710_real_pipeline_output_has_no_overflow(tmp_path: Path) -> None:
    """Regression check against this project's own real fixtures (not the synthetic worst
    case above): `python -m fina build --input tests/fixtures --out <dir>`'s actual chart
    output must also satisfy G-9, at 390px, tooltip open on the final month -- the exact state
    the project owner found overflowing on a real render before this fix. Runs the real CLI
    entry point as a subprocess (not `run_pipeline` directly, which does not write the chart
    file -- `cli.py`'s own `_build` does, per its own docstring) so this is the literal command
    a real invocation would run.
    """
    import os
    import subprocess
    import sys

    out_dir = tmp_path / "out"
    fixtures_dir = Path(__file__).resolve().parent / "fixtures"
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fina",
            "build",
            "--input",
            str(fixtures_dir),
            "--out",
            str(out_dir),
        ],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root / "src")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    chart_path = out_dir / "section1_chart.html"
    assert chart_path.exists()

    with launch_chromium() as browser:
        page = browser.new_page(viewport=_MOBILE_VIEWPORT)
        page.goto(chart_path.as_uri())
        hit = page.locator(".hit-area").bounding_box()
        assert hit is not None
        page.mouse.click(hit["x"] + hit["width"] - 2, hit["y"] + hit["height"] / 2)
        page.wait_for_timeout(20)
        assert "visible" in (page.locator("#s1-tooltip").get_attribute("class") or "")

        card_box = page.locator(".card").bounding_box()
        tooltip_box = page.locator("#s1-tooltip").bounding_box()
        assert card_box is not None
        assert tooltip_box is not None
        items = page.evaluate(_TEXT_LEAF_BOXES_JS)
        for item in items:
            label = f"{item['tag']}.{item['cls']} {item['text']!r}"
            container = tooltip_box if item["inTooltip"] else card_box
            _assert_within(item, container, label=label)


# ---------------------------------------------------------------------------
# T-711 / G-9 / R-10.7: a real touch tap shows no tap-highlight artifact
# ---------------------------------------------------------------------------


def _tap_month(page: Page, index: int, count: int) -> None:
    """Taps (real touch, not a mouse click) the shared `.hit-area` at the position for month
    `index` of `count` -- see `_click_month`'s own docstring for the position-mapping rationale.
    """
    box = page.locator(".hit-area").bounding_box()
    assert box is not None
    fraction = index / (count - 1) if count > 1 else 0.5
    x = min(max(box["x"] + box["width"] * fraction, box["x"] + 2), box["x"] + box["width"] - 2)
    y = box["y"] + box["height"] / 2
    page.touchscreen.tap(x, y)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_t711_real_touch_tap_shows_no_tap_highlight_artifact(tmp_path: Path, theme: str) -> None:
    """Android Chrome's default `-webkit-tap-highlight-color` overlay (confirmed on a real
    phone by the project owner) only ever appears for genuine touch-type pointer input --
    Chromium's tap-highlight rendering path never activates for `page.mouse.click()`, even
    inside a phone-sized viewport, which is why no test in this file before this one (all of
    them mouse-click-based) could ever have caught it; see `docs/plan/open-questions.md` Q-J
    for the full account. This uses Playwright's real touchscreen API instead
    (`browser.new_context(has_touch=True, is_mobile=True)` + `page.touchscreen.tap`).

    Asserts the *computed* `-webkit-tap-highlight-color` is fully transparent on both
    `.hit-area` and `.tooltip-close` (not merely that the CSS rule exists somewhere in the
    stylesheet text -- a more specific selector could still override it), and that a real tap
    still functionally opens and closes the tooltip, so a `touch-action` value that
    accidentally blocked tapping would also fail this test.
    """
    rows = _sample_series()
    chart_path = _write_chart(tmp_path, rows)
    count = len(rows)
    with launch_chromium() as browser:
        context = browser.new_context(viewport=_MOBILE_VIEWPORT, has_touch=True, is_mobile=True)
        page = context.new_page()
        page.emulate_media(color_scheme=theme)
        page.goto(chart_path.as_uri())

        hit_highlight = page.locator(".hit-area").evaluate(
            "el => getComputedStyle(el).webkitTapHighlightColor"
        )
        assert hit_highlight == "rgba(0, 0, 0, 0)", hit_highlight

        _tap_month(page, count - 1, count)
        page.wait_for_timeout(20)
        assert "visible" in (page.locator("#s1-tooltip").get_attribute("class") or "")

        close_button = page.locator("#s1-tooltip .tooltip-close")
        close_highlight = close_button.evaluate(
            "el => getComputedStyle(el).webkitTapHighlightColor"
        )
        assert close_highlight == "rgba(0, 0, 0, 0)", close_highlight

        close_box = close_button.bounding_box()
        assert close_box is not None
        page.touchscreen.tap(
            close_box["x"] + close_box["width"] / 2, close_box["y"] + close_box["height"] / 2
        )
        page.wait_for_timeout(20)
        assert "visible" not in (page.locator("#s1-tooltip").get_attribute("class") or "")
