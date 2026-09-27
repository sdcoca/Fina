"""Real-browser verification: the warning and rejected-file banners can be dismissed with their
own close cross (owner's request, 2026-09-27), at a real phone width (CLAUDE.md rule 19).

A pick of a garbage file plus a bank export whose header balance is 5.00 € off (an R-8.5
warning, as on the owner's real statement) shows both banners. Each cross hides its banner. A
dismissed warning stays hidden on reload (remembered in this browser only), while a warning
with any other text still shows.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from openpyxl.worksheet.worksheet import Worksheet

from browser_support import launch_chromium
from builders import bank_xlsx_with
from test_pwa_shell import (
    _MOBILE_VIEWPORT,
    _open_shell_page,
    _pick_files,
    _wait_for_settled,
    shell_server,  # noqa: F401 -- imported for its side effect as a pytest fixture
)
from test_pwa_storage import _run_seq, _wait_for_next_run

_GENEROUS_TIMEOUT_MS = 180_000


def _header_off_by(amount: str) -> Callable[[Worksheet], None]:
    def mutate(ws: Worksheet) -> None:
        ws["D4"] = f"{amount}€ EUR"

    return mutate


def test_banners_close_with_their_cross_and_dismissed_warnings_stay_hidden(
    tmp_path: Path,
    shell_server: str,  # noqa: F811 -- pytest fixture param, not a redefinition
) -> None:
    bank = bank_xlsx_with(tmp_path, _header_off_by("6.188,75"), filename="banco_header.xlsx")
    other = bank_xlsx_with(tmp_path, _header_off_by("6.193,75"), filename="banco_other.xlsx")
    garbage = tmp_path / "garbage.txt"
    garbage.write_bytes(b"not a statement")

    with launch_chromium() as browser:
        context, page = _open_shell_page(
            browser, shell_server, viewport=_MOBILE_VIEWPORT, color_scheme="light"
        )
        page.set_default_timeout(_GENEROUS_TIMEOUT_MS)
        try:
            page.wait_for_function("window.__finaChecklistReady === true")
            _pick_files(page, [bank, garbage])
            _wait_for_settled(page)

            warnings = page.locator("#warnings")
            rejected = page.locator("#rejected-files")
            assert not warnings.is_hidden()
            assert "banco_header.xlsx" in warnings.inner_text()
            assert not rejected.is_hidden()
            assert page.evaluate("document.documentElement.scrollWidth") <= 390

            rejected.locator(".banner-close").click()
            assert rejected.is_hidden()
            close = warnings.locator(".banner-close")
            assert close.get_attribute("aria-label") == "Dismiss"
            close.click()
            assert warnings.is_hidden()

            # Reload: the same warning, from the cache, stays dismissed.
            page.goto(f"{shell_server}/index.html")
            page.wait_for_function("window.__finaChecklistReady === true")
            _wait_for_settled(page)
            assert warnings.is_hidden()

            # A warning with other text still shows: untick the dismissed file, add another.
            page.evaluate(
                "() => document.querySelectorAll('#accounts-section, #accounts-section details')"
                ".forEach((d) => { d.open = true; })"
            )
            seq = _run_seq(page)
            page.locator(".stored-file", has_text="banco_header.xlsx").locator(
                ".stored-file-checkbox"
            ).uncheck()
            _wait_for_next_run(page, seq)
            _pick_files(page, [other])
            _wait_for_settled(page)
            page.wait_for_function(
                "() => !document.getElementById('warnings').hidden", timeout=_GENEROUS_TIMEOUT_MS
            )
            assert "banco_other.xlsx" in warnings.inner_text()
            assert "banco_header.xlsx" not in warnings.inner_text()
        finally:
            context.close()
