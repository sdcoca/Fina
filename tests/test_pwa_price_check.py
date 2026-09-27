"""Real-browser verification of `web/price-check.html`, the diagnostic behind the owner's
decision to test a free price provider the app can call directly (2026-09-27).

Providers are simulated with Playwright routes, never contacted: one answers with the
cross-origin header a page needs, every other request is refused -- exactly what a browser does
with a provider that does not allow pages to read it. Checked at a real phone width (CLAUDE.md
rule 19): the probe tells the two apart, a keyed history request is summarized (count, first
and last date, last close), and the copied results never contain the key.
"""

from __future__ import annotations

import json

from playwright.sync_api import Route

from browser_support import launch_chromium
from test_pwa_shell import _MOBILE_VIEWPORT, shell_server  # noqa: F401 -- pytest fixture

_KEY = "k3y-under-test"
_SERIES = {
    "data": [
        {"date": "2026-08-29T00:00:00+0000", "close": 101.5, "symbol": "TEST.XETRA"},
        {"date": "2026-06-30T00:00:00+0000", "close": 97.25, "symbol": "TEST.XETRA"},
        {"date": "2026-07-31T00:00:00+0000", "close": 99.0, "symbol": "TEST.XETRA"},
    ]
}


def test_probe_keyed_history_and_copy_without_the_key(
    shell_server: str,  # noqa: F811 -- pytest fixture param, not a redefinition
) -> None:
    def handler(route: Route) -> None:
        url = route.request.url
        if url.startswith(shell_server + "/"):
            route.continue_()
        elif url.startswith("https://api.marketstack.com/"):
            body = _SERIES if "access_key=" in url else {"error": {"code": "missing_access_key"}}
            route.fulfill(
                status=200 if "access_key=" in url else 401,
                headers={"Access-Control-Allow-Origin": "*", "Content-Type": "application/json"},
                body=json.dumps(body),
            )
        else:
            route.abort()

    with launch_chromium() as browser:
        context = browser.new_context(viewport=_MOBILE_VIEWPORT)
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=shell_server)
        context.route("**/*", handler)
        page = context.new_page()
        try:
            page.goto(f"{shell_server}/price-check.html")
            page.wait_for_function("window.__finaPriceCheckReady === true")

            page.locator("#probe-button").click()
            page.wait_for_function(
                "() => document.querySelectorAll('#probe-results li').length === 14"
            )
            results = page.locator("#probe-results li strong").all_inner_texts()
            assert "marketstack v2: answered (HTTP 401)" in results
            assert "Yahoo Finance: blocked by the browser, or unreachable" in results
            assert sum("answered" in r for r in results) == 2  # marketstack v1 and v2
            assert results[0].startswith("marketstack v2") and results[-1].startswith("Frankfurter")

            page.locator("#provider").select_option("marketstack v2")
            page.locator("#api-key").fill(_KEY)
            page.locator("#query").fill("TEST.XETRA")
            page.locator("#history-button").click()
            item = page.locator("#key-results li")
            item.wait_for()
            assert item.locator("strong").inner_text() == (
                'marketstack v2 history "TEST.XETRA": HTTP 200'
            )
            assert item.locator(".pc-raw").inner_text().startswith(
                "3 prices from 2026-06-30 to 2026-08-29; last close 101.5"
            )

            page.locator("#copy-button").click()
            page.wait_for_function("() => !document.getElementById('copy-status').hidden")
            copied = page.evaluate("navigator.clipboard.readText()")
            assert "marketstack v2 history" in copied
            assert "access_key=***" in copied
            assert _KEY not in copied
            assert page.evaluate("document.documentElement.scrollWidth") <= 390
        finally:
            context.close()
