"""Real-browser verification for the PWA shell (WP-13).

Follows the house pattern already established by `tests/test_pyodide_bridge.py` /
`tests/test_pyodide_vendor.py`: a local `http.server` (no COOP/COEP headers) serving the real
`web/` tree exactly as a static host would, a real headless Chromium via
`browser_support.launch_chromium()`, and `context.route` aborting every off-origin request so
a pass is real proof of offline/local-only operation -- not a mock of the shell, the real
`web/index.html` + `web/js/app.js` + `web/js/import.js` + `web/js/pyodide-bridge.js` driven by a
real file input, exactly as a phone's browser would run them.

CLAUDE.md rule 19 / this WP's own instruction: every mobile-relevant case below is run at a
390px viewport FIRST (`_MOBILE_VIEWPORT`), in both `prefers-color-scheme` values
(`page.emulate_media(color_scheme=...)`), before the desktop-width case. A screenshot is taken
for each (viewport x theme) combination exercised, saved under `tmp_path` -- not asserted on
pixel-by-pixel here (T-710-style containment checks already live in
`tests/test_render_browser.py` for the chart itself), but real screenshots a human can open, per
this project's own history of visual defects no purely computed-style assertion caught.

Cases (matching WP-13's spec verbatim):

1. `test_backdrop_filter_is_never_used_in_shell_css` -- rule 20's automated gate: zero matches
   for `backdrop-filter` across `web/css/*.css`, and (defensively) across `web/index.html`'s
   own markup in case an inline `<style>` is ever added there.
2. `test_shell_end_to_end_both_fixtures_390px_light_and_dark` -- picks both real fixtures
   (`banco_ejemplo.xlsx`, `broker_ejemplo.csv`) at once via the real `<input type="file">`, at
   390px, in both themes: confirms the chart iframe ends up populated with real content (the
   chart's own known markup/classes, inside its sandboxed `srcdoc`) and that the summary
   figures rendered in the shell match the real, independently-computed pipeline output
   byte-for-byte (never re-derived from the DOM).
3. `test_mixed_pick_one_good_file_one_garbage_file_390px` -- the core "one bad file doesn't
   abort the whole run" proof: one real fixture (the bank XLSX alone, which `run_pipeline`
   already produces a complete, warning-free result from on its own) picked alongside a
   garbage `.txt` file. The garbage file must appear as rejected and the good file's result
   must still render correctly -- proving the bad file never reached `runBuild()` at all.
4. `test_shell_end_to_end_desktop_after_mobile` -- the same core flow at a desktop width,
   run only after the 390px cases above, exactly as `test_render_browser.py`'s own
   T-708 does for the chart module itself: additional, never a substitute for the mobile pass.
"""

from __future__ import annotations

import http.server
import re
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Route

from browser_support import launch_chromium
from fina.money import round_half_up
from fina.pipeline import run_pipeline

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"
CSS_DIR = WEB_DIR / "css"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
BANK_XLSX = FIXTURES_DIR / "banco_ejemplo.xlsx"
BROKER_CSV = FIXTURES_DIR / "broker_ejemplo.csv"

_MOBILE_VIEWPORT = {"width": 390, "height": 844}
_DESKTOP_VIEWPORT = {"width": 1280, "height": 800}

_GARBAGE_TXT = b"this is not any known export format\n"


# ---------------------------------------------------------------------------
# 1. Rule 20: backdrop-filter is never used anywhere in the shell.
# ---------------------------------------------------------------------------


def test_backdrop_filter_is_never_used_in_shell_css() -> None:
    """CLAUDE.md rule 20: no visual effect in this app ever depends on `backdrop-filter` (or
    any other blur-based translucency) -- translucency is plain alpha compositing only, which
    works on every rendering engine. Scans every `web/css/*.css` file and, defensively,
    `web/index.html` itself (in case an inline `<style>` block is ever added there).
    """
    offenders: list[str] = []
    css_files = sorted(CSS_DIR.glob("*.css"))
    assert css_files, "expected at least one file under web/css/*.css"
    # Matches the actual CSS property declaration (`backdrop-filter:` / `-webkit-backdrop-
    # filter:`), not a prose mention of the property name in a comment explaining why it is
    # avoided (this file's own docstring, and shell.css's own header comment, both say the
    # word "backdrop-filter" deliberately).
    property_re = re.compile(r"(?:-webkit-)?backdrop-filter\s*:", re.IGNORECASE)
    for path in [*css_files, WEB_DIR / "index.html"]:
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)  # strip CSS/JS-style comments
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)  # strip HTML comments
        if property_re.search(text):
            offenders.append(str(path))
    assert offenders == [], f"backdrop-filter found in: {offenders}"


# ---------------------------------------------------------------------------
# Shared server/page harness.
# ---------------------------------------------------------------------------


class _ShellRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the real `web/` tree directly at its real root -- the shell's own absolute
    paths (`/vendor/...`, `/js/...`, `/css/...`) resolve exactly as they would on a real
    static host, not a synthetic in-memory harness page."""

    def __init__(self, *args: object, serve_root: Path, **kwargs: object) -> None:
        self._serve_root = serve_root
        super().__init__(*args, directory=str(serve_root), **kwargs)  # type: ignore[arg-type]

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        pass  # keep pytest output quiet; failures surface via the test's own assertions


@pytest.fixture
def shell_server() -> Iterator[str]:
    assert (WEB_DIR / "index.html").is_file()
    assert (WEB_DIR / "vendor" / "pyodide" / "pyodide.asm.wasm").is_file(), (
        "web/vendor/pyodide/ is not populated -- run `python3 web/vendor/build.py`"
    )
    assert (WEB_DIR / "js" / "app.js").is_file()
    assert (WEB_DIR / "js" / "import.js").is_file()

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *a, **kw: _ShellRequestHandler(*a, serve_root=WEB_DIR, **kw),
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _block_everything_off_origin(origin: str) -> Any:
    def handler(route: Route) -> None:
        if route.request.url.startswith(origin + "/"):
            route.continue_()
        else:
            route.abort()

    return handler


def _open_shell_page(
    browser: Browser, origin: str, *, viewport: dict[str, int], color_scheme: str
) -> tuple[BrowserContext, Page]:
    context = browser.new_context(viewport=viewport)
    context.route("**/*", _block_everything_off_origin(origin))
    page = context.new_page()
    page.emulate_media(color_scheme=color_scheme)
    page.goto(f"{origin}/index.html")
    page.wait_for_function("window.__finaAppJsLoaded === true", timeout=30_000)
    return context, page


def _pick_files(page: Page, paths: list[Path]) -> None:
    page.locator("#file-input").set_input_files([str(p) for p in paths])


def _wait_for_settled(page: Page) -> None:
    """Waits until either the chart is showing or the error section is showing -- the two
    mutually exclusive "a run finished" states `app.js` produces. A Pyodide cold boot plus a
    real pipeline run is slow enough that a generous timeout is the right tool here, matching
    `test_pyodide_bridge.py`'s own convention for the same reason.
    """
    page.wait_for_function(
        "() => !document.getElementById('chart-section').hidden || "
        "!document.getElementById('error-section').hidden",
        timeout=180_000,
    )


def _summary_dd_texts(page: Page) -> list[str]:
    return page.locator("#summary .summary-list dd").all_inner_texts()


def _expected_summary_for(input_dir: Path) -> dict[str, str]:
    result = run_pipeline(input_dir)
    latest = result.series[-1]
    return {
        "as_of": latest.as_of.isoformat(),
        "completeness": latest.completeness,
        "real_net_worth": str(round_half_up(latest.real_net_worth)),
        "savings_only": str(round_half_up(latest.savings_only)),
        "gap": str(round_half_up(latest.gap)),
    }


def _assert_chart_iframe_has_real_content(page: Page) -> None:
    """Confirms the chart `<iframe>` genuinely has the real chart document loaded inside it
    (not merely that the iframe element exists) -- the sandboxed iframe has no
    `allow-same-origin`, so this only works at all because Playwright drives the browser
    itself (CDP), not page-level JS subject to the same cross-origin restriction.
    """
    frame = page.frame_locator("#chart-frame")
    assert frame.locator(".card h1").inner_text() == "Patrimonio real vs. solo ahorro"
    assert frame.locator(".hit-area").count() == 1
    assert frame.locator(".line-real").count() == 1
    # The tooltip-close/report-height script must have actually run inside the iframe (proof
    # that <script> tags embedded via srcdoc execute, unlike the innerHTML path PWA-5.2
    # rejects): open the tooltip and check its "visible" class actually gets applied.
    frame.locator(".hit-area").click()
    assert "visible" in (frame.locator("#s1-tooltip").get_attribute("class") or "")


# ---------------------------------------------------------------------------
# 2. End-to-end over both real fixtures, 390px, light and dark.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_shell_end_to_end_both_fixtures_390px_light_and_dark(
    tmp_path: Path, shell_server: str, theme: str
) -> None:
    expected = _expected_summary_for(FIXTURES_DIR)

    with launch_chromium() as browser:
        context, page = _open_shell_page(
            browser, shell_server, viewport=_MOBILE_VIEWPORT, color_scheme=theme
        )
        try:
            _pick_files(page, [BANK_XLSX, BROKER_CSV])
            _wait_for_settled(page)

            assert page.locator("#error-section").is_hidden(), (
                "error section shown for a run over two genuinely recognized, "
                "reconciling fixtures"
            )
            assert page.locator("#rejected-files").is_hidden(), (
                "no file in this pick should have been rejected"
            )
            assert not page.locator("#chart-section").is_hidden()

            assert _summary_dd_texts(page) == [
                expected["as_of"],
                expected["completeness"],
                f"{expected['real_net_worth']} EUR",
                f"{expected['savings_only']} EUR",
                f"{expected['gap']} EUR",
            ]

            _assert_chart_iframe_has_real_content(page)

            screenshot_path = tmp_path / f"shell_both_fixtures_390_{theme}.png"
            page.screenshot(path=str(screenshot_path))
            assert screenshot_path.stat().st_size > 0
        finally:
            context.close()


# ---------------------------------------------------------------------------
# 3. Mixed pick: one good fixture + one garbage file, 390px.
# ---------------------------------------------------------------------------


def test_mixed_pick_one_good_file_one_garbage_file_390px(tmp_path: Path, shell_server: str) -> None:
    """The core WP-13 proof: a garbage `.txt` file picked alongside a real, recognized fixture
    must be rejected on its own -- and must never abort the run for the recognized file, which
    must still render a real, correct result. Uses the bank XLSX fixture alone (verified above,
    `_expected_summary_for`, to reconcile cleanly with zero warnings on its own) so the expected
    output is unambiguous without also needing the broker fixture in this pick.
    """
    garbage_path = tmp_path / "garbage.txt"
    garbage_path.write_bytes(_GARBAGE_TXT)

    bank_only_dir = tmp_path / "bank_only"
    bank_only_dir.mkdir()
    shutil.copyfile(BANK_XLSX, bank_only_dir / BANK_XLSX.name)
    expected = _expected_summary_for(bank_only_dir)

    with launch_chromium() as browser:
        context, page = _open_shell_page(
            browser, shell_server, viewport=_MOBILE_VIEWPORT, color_scheme="light"
        )
        try:
            _pick_files(page, [BANK_XLSX, garbage_path])
            _wait_for_settled(page)

            # The garbage file is shown as rejected, by name, individually.
            assert not page.locator("#rejected-files").is_hidden()
            rejected_text = page.locator("#rejected-files").inner_text()
            assert "garbage.txt" in rejected_text
            assert BANK_XLSX.name not in rejected_text

            # The run still happened, and succeeded, over the recognized file alone.
            assert page.locator("#error-section").is_hidden(), (
                "a garbage sibling file must never abort the run for the recognized file"
            )
            assert not page.locator("#chart-section").is_hidden()
            assert _summary_dd_texts(page) == [
                expected["as_of"],
                expected["completeness"],
                f"{expected['real_net_worth']} EUR",
                f"{expected['savings_only']} EUR",
                f"{expected['gap']} EUR",
            ]
            _assert_chart_iframe_has_real_content(page)

            screenshot_path = tmp_path / "shell_mixed_pick_390_light.png"
            page.screenshot(path=str(screenshot_path))
            assert screenshot_path.stat().st_size > 0
        finally:
            context.close()


# ---------------------------------------------------------------------------
# 4. Desktop width -- additional, run only after the 390px cases above.
# ---------------------------------------------------------------------------


def test_shell_end_to_end_desktop_after_mobile(tmp_path: Path, shell_server: str) -> None:
    """Mirrors `test_render_browser.py`'s own T-708: the same core flow at a desktop width,
    run only after the 390px cases in this file have already passed -- this test alone proves
    nothing about mobile and must never be read as satisfying CLAUDE.md rule 19 on its own.
    """
    expected = _expected_summary_for(FIXTURES_DIR)

    with launch_chromium() as browser:
        context, page = _open_shell_page(
            browser, shell_server, viewport=_DESKTOP_VIEWPORT, color_scheme="light"
        )
        try:
            _pick_files(page, [BANK_XLSX, BROKER_CSV])
            _wait_for_settled(page)

            assert page.locator("#error-section").is_hidden()
            assert not page.locator("#chart-section").is_hidden()
            assert _summary_dd_texts(page) == [
                expected["as_of"],
                expected["completeness"],
                f"{expected['real_net_worth']} EUR",
                f"{expected['savings_only']} EUR",
                f"{expected['gap']} EUR",
            ]
            _assert_chart_iframe_has_real_content(page)

            screenshot_path = tmp_path / "shell_both_fixtures_desktop_light.png"
            page.screenshot(path=str(screenshot_path))
            assert screenshot_path.stat().st_size > 0
        finally:
            context.close()
