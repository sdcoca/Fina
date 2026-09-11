"""Real-browser regression test: the app must work when served under a non-root subpath.

Found the hard way, not by design review: every automated test up to this point (WP-13..WP-17)
serves `web/` at a local `http.server`'s own root (`http://127.0.0.1:port/`), so `index.html`'s
own root-relative asset paths (`/js/app.js`, `/vendor/...`, `/manifest.json`, ...) and
`sw-register.js`'s `navigator.serviceWorker.register("/service-worker.js")` all happened to
resolve correctly by coincidence. The project owner's real deployment target, a GitHub Pages
*project* site, serves the app under `/<repo-name>/` instead -- and every one of those
root-relative paths 404s there, silently: `app.js` itself never loads, so picking a file does
nothing (no console-visible app error, no exception the user could report beyond "I pick a file
and nothing happens" -- exactly what surfaced this bug in the first place, on the project
owner's own phone).

The fix (see `web/index.html`, `web/manifest.json`, `web/js/pyodide-bridge.js`,
`web/js/sw-register.js`): every one of those paths is now relative (`./...`) or resolved via
`new URL(..., import.meta.url)`, matching the pattern `pyodide-bridge.js`'s own `BRIDGE_PY_URL`
already used correctly before this fix, just not consistently everywhere else.

This test serves the *whole repo root* (not `web/` directly) via a local `http.server`, so the
real app naturally lands at `/web/index.html` -- a genuine non-root subpath, not a synthetic one
-- and drives the full import -> run -> chart flow through it, exactly as WP-13's own
`test_shell_end_to_end_both_fixtures_390px_light_and_dark` does at the root. This is the
regression test that closes the gap: it fails immediately (confirmed by hand against the
pre-fix code, see this session's own history) if any root-relative path is ever reintroduced.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page

from browser_support import launch_chromium
from test_pwa_shell import (
    _MOBILE_VIEWPORT,
    BANK_XLSX,
    BROKER_CSV,
    FIXTURES_DIR,
    REPO_ROOT,
    _expected_summary_for,
    _pick_files,
    _summary_dd_texts,
    _wait_for_settled,
)

_SUBPATH = "web"  # the real subpath a GitHub Pages *project* site serves this app under.


class _RepoRootRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the whole repository root -- not `web/` directly -- so the app lands at
    `/web/index.html`, a genuine non-root subpath, not a root the app happens to occupy."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(REPO_ROOT), **kwargs)  # type: ignore[arg-type]

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


@pytest.fixture
def subpath_server() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RepoRootRequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/{_SUBPATH}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_app_js_loads_and_full_flow_works_under_a_non_root_subpath(
    tmp_path: Path, subpath_server: str
) -> None:
    """The core regression proof: with the app served at `.../web/` (not the server root),
    `app.js` must still load (root-relative `/js/app.js` would 404 here) and the complete
    import -> run -> chart flow must still succeed over both real fixtures.
    """
    expected = _expected_summary_for(FIXTURES_DIR)
    origin = subpath_server

    with launch_chromium() as browser:
        context = browser.new_context(viewport=_MOBILE_VIEWPORT)
        failed_requests: list[str] = []

        def _record_failure(response: object) -> None:
            status = response.status  # type: ignore[attr-defined]
            url = response.url  # type: ignore[attr-defined]
            # The browser's own automatic favicon.ico probe fires at the true origin root,
            # independent of this app's own subpath -- a Chromium behaviour, not an app bug
            # (already a known, allow-listed artifact in test_pwa_service_worker.py).
            if status >= 400 and not url.endswith("/favicon.ico"):
                failed_requests.append(f"{status} {url}")

        context.on("response", _record_failure)
        page: Page = context.new_page()
        page.goto(f"{origin}/index.html")

        # This is the line that hangs/times out on the pre-fix code: app.js itself 404s under
        # a subpath, so this flag is never set.
        page.wait_for_function("window.__finaAppJsLoaded === true", timeout=30_000)
        page.wait_for_function("window.__finaChecklistReady === true", timeout=30_000)

        try:
            _pick_files(page, [BANK_XLSX, BROKER_CSV])
            _wait_for_settled(page)

            assert page.locator("#error-section").is_hidden(), (
                "the full flow must succeed under a non-root subpath -- "
                f"error shown: {page.locator('#error-message').inner_text()!r}"
            )
            assert not page.locator("#chart-section").is_hidden()
            assert _summary_dd_texts(page) == [
                expected["as_of"],
                expected["completeness"],
                f"{expected['real_net_worth']} EUR",
                f"{expected['savings_only']} EUR",
                f"{expected['gap']} EUR",
            ]

            screenshot_path = tmp_path / "subpath_deploy_390_light.png"
            page.screenshot(path=str(screenshot_path))
            assert screenshot_path.stat().st_size > 0
        finally:
            context.close()

    assert failed_requests == [], (
        f"requests 404'd under the subpath deployment (a root-relative path regressed): "
        f"{failed_requests}"
    )


def test_manifest_paths_resolve_relative_to_the_manifest_not_the_origin_root(
    subpath_server: str,
) -> None:
    """`web/manifest.json`'s own `start_url`/`scope`/`icons` must be relative (resolved against
    the manifest's own URL, per spec) -- a root-relative `"/index.html"` would point at the
    server's true root, not this app's subpath, breaking installability under a project-site
    deployment even though the page itself loads fine (this is the one part of the subpath bug
    a plain page-load test would NOT catch, since the manifest is inert until a browser
    actually tries to install the app).
    """
    with launch_chromium() as browser:
        context = browser.new_context(viewport=_MOBILE_VIEWPORT)
        page = context.new_page()
        page.goto(f"{subpath_server}/index.html")
        page.wait_for_function("window.__finaChecklistReady === true", timeout=30_000)

        resolved = page.evaluate(
            """
            async () => {
              const link = document.querySelector('link[rel="manifest"]');
              const resp = await fetch(link.href);
              const manifest = await resp.json();
              const manifestUrl = new URL(link.href, document.baseURI);
              return {
                startUrl: new URL(manifest.start_url, manifestUrl).href,
                scope: new URL(manifest.scope, manifestUrl).href,
                iconUrls: manifest.icons.map((i) => new URL(i.src, manifestUrl).href),
              };
            }
            """
        )
        context.close()

    assert resolved["startUrl"] == f"{subpath_server}/index.html"
    assert resolved["scope"] == f"{subpath_server}/"
    for icon_url in resolved["iconUrls"]:
        assert icon_url.startswith(f"{subpath_server}/icons/"), icon_url
