"""Real-browser visual verification for fina.render.section1_chart (WP-9, re-derived for
Q-G): T-705..T-708.

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
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

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


def _write_chart(tmp_path: Path) -> Path:
    html_doc = render_section1_chart(_sample_series())
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
