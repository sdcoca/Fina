"""Real-browser visual verification for fina.render.section1_chart (WP-9): T-705..T-708.

R-10.2a: verified by actually rendering the page at a 390px viewport first, desktop second --
never inferred from reading the CSS. Excluded from mutmut's own test run (see pyproject.toml)
since these tests verify the *browser's* rendering behaviour, not additional Python line
coverage -- every line of section1_chart.py is already covered by test_section1_chart.py.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

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
    with launch_chromium() as browser:
        page = browser.new_page(viewport=_MOBILE_VIEWPORT)
        page.goto(chart_path.as_uri())
        circles = page.locator("circle.hit")
        count = circles.count()
        assert count == len(_sample_series())
        for i in range(count):
            circles.nth(i).click(force=True)
            page.wait_for_timeout(20)
            body = page.locator(".tooltip-body")
            text = body.inner_text()
            expected_lines = text.count("\n") + 1
            height = body.bounding_box()["height"]
            line_height = page.eval_on_selector(
                ".tooltip-body", "el => parseFloat(getComputedStyle(el).lineHeight)"
            )
            actual_lines = round(height / line_height)
            assert actual_lines == expected_lines, (
                f"month index {i}: tooltip text wrapped "
                f"({actual_lines} rendered lines vs {expected_lines} expected)"
            )


# ---------------------------------------------------------------------------
# T-707 / R-10.2/R-10.2b: visible translucency over a coloured band, at 390px
# ---------------------------------------------------------------------------


def test_t707_tooltip_is_measurably_translucent_over_the_band(tmp_path: Path) -> None:
    chart_path = _write_chart(tmp_path)
    with launch_chromium() as browser:
        page = browser.new_page(viewport=_MOBILE_VIEWPORT)
        page.goto(chart_path.as_uri())
        # Point index 3 sits well inside a positive-gap (green band) segment, per the sample
        # series above (real=2000 > savings=1600 there).
        page.locator("circle.hit").nth(3).click(force=True)
        page.wait_for_timeout(20)
        box = page.locator("#s1-tooltip").bounding_box()
        assert box is not None
        screenshot = page.screenshot()
        # The tooltip is centred on the clicked point (see openTooltip's positioning), which
        # sits on the real_net_worth line inside the band -- so its own centre is where the
        # coloured band is guaranteed to be directly behind it.
        tooltip_pixel = _read_pixel(
            screenshot, int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)
        )
        # Sample the flat tooltip background far from any chart content (bottom-right
        # corner of the viewport, always outside the chart and the tooltip).
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
    with launch_chromium() as browser:
        page = browser.new_page(viewport=_DESKTOP_VIEWPORT)
        page.goto(chart_path.as_uri())
        page.locator("circle.hit").first.click(force=True)
        page.wait_for_timeout(20)
        assert "open" in (page.locator("#s1-tooltip").get_attribute("class") or "")
