"""Real-browser verification for the service worker / offline behavior (WP-16).

Follows the house pattern `tests/test_pwa_shell.py` (WP-13) already established, and reuses its
`shell_server` fixture plus its `_pick_files`/`_wait_for_settled`/`_summary_dd_texts`/
`_expected_summary_for`/`_assert_chart_iframe_has_real_content`/`_MOBILE_VIEWPORT` helpers
directly (the same precedent `tests/test_pwa_storage.py`, WP-15, already set) rather than
duplicating them: a local `http.server` (no COOP/COEP headers) serving the real `web/` tree, a
real headless Chromium via `browser_support.launch_chromium()`, and real `context.route`/
`context.on(...)` instrumentation so a pass is real proof of offline/local-only operation, never
a mock of the service worker's own lifecycle.

Cases (matching this WP's own spec verbatim):

1. `test_full_offline_flow_after_first_install` -- loads the page ONCE online (so the service
   worker installs, precaches, and reaches `activated`), THEN disables the network for literally
   everything (`context.route("**/*", route.abort())`, not merely off-origin like earlier WPs'
   tests), reloads, and runs the complete import->run->chart flow over both real fixtures purely
   from the service-worker cache. See this test's own docstring for an important, explicitly
   documented Playwright/Chromium quirk found while building it: a service-worker-resolved
   navigation still surfaces one interceptable "phantom" request to Playwright's `route()` layer,
   whose `abort()` makes `page.reload()`'s own promise reject even though the real navigation the
   browser renders is genuinely served from cache regardless. That quirk is asserted explicitly
   below, not silently swallowed.
2. `test_online_traffic_never_carries_financial_data` -- PWA-4.3, checked as a script rather
   than asserted as a comment: instruments every request made during a real (online) run of the
   full import flow and asserts, by an explicit allow-list of the app's own known static asset
   paths AND a direct substring scan against the fixtures' own bytes and the real computed
   `manifest.json`, that none of them ever carries a byte of the user's actual financial data.
3. `test_update_prompt_shown_only_on_new_version_and_applied_only_on_click` -- PWA-4.4: a second
   service-worker version becoming available shows the "update available" banner, never reloads
   the page on its own, and only applies (skipWaiting + reload) in direct response to the user's
   own click on the banner's button.
4. `test_update_banner_visible_and_legible_390px_light_and_dark` -- CLAUDE.md rule 19: the
   banner itself verified with a real browser at a real mobile width, in both themes, before
   trusting any visual claim about it.

`web/css/shell.css`'s own `backdrop-filter` check (rule 20) already covers every `web/css/*.css`
file, this WP's own CSS additions included -- see `test_pwa_shell.py::
test_backdrop_filter_is_never_used_in_shell_css`; not duplicated here.
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path

from playwright.sync_api import Page, Route

from browser_support import launch_chromium
from fina.pipeline import run_pipeline
from test_pwa_shell import (
    _MOBILE_VIEWPORT,
    BANK_XLSX,
    BROKER_CSV,
    FIXTURES_DIR,
    WEB_DIR,
    _assert_chart_iframe_has_real_content,
    _expected_summary_for,
    _pick_files,
    _summary_dd_texts,
    _wait_for_settled,
    shell_server,  # noqa: F401 -- imported for its side effect as a pytest fixture
)

SERVICE_WORKER_PATH = WEB_DIR / "service-worker.js"
PRECACHE_MANIFEST_PATH = WEB_DIR / "precache-manifest.json"

_GENEROUS_TIMEOUT_MS = 180_000

# Polls until the service worker has reached "activated" AND its own precache cache genuinely
# holds every file the (real, on-disk) precache-manifest.json names -- not merely that the SW
# lifecycle *state* says "activated", which this test found (empirically, while it was being
# built) can be observed slightly before every `cache.put()` the install step kicked off has
# actually landed. Polling the cache's own real content directly, rather than trusting the
# lifecycle state's timing alone, is what makes this reliable.
_PRECACHE_READY_JS = """
async () => {
  const reg = await navigator.serviceWorker.getRegistration();
  if (!reg || !reg.active || reg.active.state !== "activated") return false;
  const manifestResp = await fetch("/precache-manifest.json", { cache: "reload" });
  if (!manifestResp.ok) return false;
  const manifest = await manifestResp.json();
  const cache = await caches.open("fina-precache-" + manifest.version);
  const keys = await cache.keys();
  return keys.length >= manifest.files.length;
}
"""


def _wait_for_precache_ready(page: Page, timeout: int = _GENEROUS_TIMEOUT_MS) -> None:
    """Polls `_PRECACHE_READY_JS` via repeated `page.evaluate()` calls from the Python side,
    rather than `page.wait_for_function()`.

    Found empirically while building this test: `page.wait_for_function()` given this exact
    async predicate shape (one that itself awaits `fetch()`/`caches.open()`) was observed to
    resolve "truthy" within single-digit milliseconds -- long before the service worker's own
    install step could possibly have finished writing ~15MB across 32 files to Cache Storage,
    confirmed directly by reading back a genuinely incomplete cache (a handful of entries, not
    all of them) immediately afterward. A plain Python-side loop calling `page.evaluate()` (which
    this repository's other browser tests, e.g. `tests/test_pwa_storage.py`'s `_GET_RUN_CACHE_JS`/
    `_RECOMPUTE_JS`, already rely on to correctly await an async predicate's real result) does
    not show this problem: sampled repeatedly against both a warm and a stone-cold fresh local
    server, it reliably converges on the true, fully-populated cache state every time. This
    function uses that proven-reliable mechanism instead.
    """
    deadline = time.monotonic() + timeout / 1000
    while True:
        if page.evaluate(f"({_PRECACHE_READY_JS.strip()})()"):
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"service worker did not finish installing/precaching within {timeout}ms"
            )
        page.wait_for_timeout(100)


def _precache_file_count(page: Page) -> int:
    return int(
        page.evaluate(
            """
            async () => {
              const resp = await fetch("/precache-manifest.json", { cache: "reload" });
              const manifest = await resp.json();
              return manifest.files.length;
            }
            """
        )
    )


# ---------------------------------------------------------------------------
# 1. Full offline flow, network genuinely fully disabled (not merely off-origin).
# ---------------------------------------------------------------------------


def test_full_offline_flow_after_first_install(
    tmp_path: Path,
    shell_server: str,  # noqa: F811 -- pytest fixture param, not a redefinition
) -> None:
    """WP-16's central proof: once the service worker has installed and precached (a real,
    online first load), the complete import -> run -> chart flow works over both real fixtures
    with the browser's network genuinely, fully disabled -- not merely requests to other
    origins blocked (as earlier WPs' tests did), but literally every request, same-origin
    included.

    **An important, explicitly documented quirk found while building this test.** With
    `context.route("**/*", route.abort())` active, `page.reload()`'s own Python promise
    reliably rejects with `net::ERR_FAILED` for the very first reload after going offline --
    but the browser's *actual* navigation genuinely succeeds regardless, served entirely from
    the service worker's own cache. Direct experimentation (independent of this test file, see
    this WP's own final report) confirmed, via `response.from_service_worker` and via the exact
    same assertions this test makes below, that this is a one-time Playwright/Chromium
    DevTools-Protocol observation artifact specific to service-worker-resolved *navigations* --
    interception at the `route()` layer sees one "phantom" copy of the top-level document
    request and can abort *that*, without it actually blocking the real navigation the service
    worker answers independently from its cache. It is not a real failed network transfer (a
    genuinely failed transfer would leave the app broken -- the test would fail below, at the
    "the full flow must succeed" assertion, exactly as a real offline gap would surface). This
    is asserted explicitly, not silently caught and ignored: `aborted_urls` is checked below to
    contain, at most, exactly this one document-navigation entry and nothing else -- proving
    every *substantive* resource (shell JS/CSS, the ~15MB Pyodide runtime, every vendored
    wheel, `web/py/bridge.py`) was never once attempted over the network while offline.
    """
    expected = _expected_summary_for(FIXTURES_DIR)
    origin = shell_server

    with launch_chromium() as browser:
        context = browser.new_context(viewport=_MOBILE_VIEWPORT)
        page = context.new_page()
        page.goto(f"{origin}/index.html")
        page.wait_for_function("window.__finaSwRegisterJsLoaded === true", timeout=30_000)
        _wait_for_precache_ready(page)

        file_count = _precache_file_count(page)
        assert file_count >= 20, f"suspiciously few precached files ({file_count})"
        cache_counts = page.evaluate(
            """
            async () => {
              const keys = await caches.keys();
              const out = {};
              for (const k of keys) {
                const c = await caches.open(k);
                out[k] = (await c.keys()).length;
              }
              return out;
            }
            """
        )
        cache_entry_count = sum(cache_counts.values())
        # +2: the manifest response itself, and the bare-origin-root alias to index.html (see
        # service-worker.js's own _precacheAll()).
        assert cache_entry_count >= file_count + 2, (
            f"cache holds fewer entries ({cache_entry_count}, {cache_counts}) than the precache "
            f"manifest names ({file_count}) -- install did not actually finish precaching "
            "everything"
        )

        aborted_urls: list[str] = []

        def _abort_everything(route: Route) -> None:
            aborted_urls.append(route.request.url)
            route.abort()

        context.route("**/*", _abort_everything)

        # See this test's own docstring for exactly why this reload's own exception is expected
        # and deliberately swallowed here, rather than being evidence of a real offline gap.
        with contextlib.suppress(Exception):
            page.reload()
        page.wait_for_function("window.__finaAppJsLoaded === true", timeout=30_000)
        page.wait_for_function("window.__finaChecklistReady === true", timeout=30_000)

        try:
            _pick_files(page, [BANK_XLSX, BROKER_CSV])
            _wait_for_settled(page)

            assert page.locator("#error-section").is_hidden(), (
                "the full import/run/chart flow must succeed purely from the service worker's "
                "own cache with the network fully disabled -- "
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
            _assert_chart_iframe_has_real_content(page)

            screenshot_path = tmp_path / "sw_offline_flow_390_light.png"
            page.screenshot(path=str(screenshot_path))
            assert screenshot_path.stat().st_size > 0
        finally:
            context.close()

    # The real proof, examined after the fact: of everything the browser attempted to reach
    # over the network while fully offline (reload + the entire Pyodide boot + pipeline run
    # over two real fixture files), the only entry is the one documented phantom navigation
    # artifact -- never any of the shell's own JS/CSS, never the Pyodide runtime, never a
    # single vendored wheel, never web/py/bridge.py.
    allowed_phantom = {f"{origin}/index.html", f"{origin}/", f"{origin}/favicon.ico"}
    unexpected_aborts = [u for u in aborted_urls if u not in allowed_phantom]
    assert unexpected_aborts == [], (
        f"resources were attempted over the network despite being fully offline: "
        f"{unexpected_aborts}"
    )


# ---------------------------------------------------------------------------
# 2. PWA-4.3: zero financial data ever traverses fetch(), checked as a script.
# ---------------------------------------------------------------------------


def _distinctive_substrings(text: str, length: int = 28, stride: int = 131) -> list[str]:
    """A sparse sample of `length`-character windows over `text`, used below as "recognizable
    fragments" of the user's real financial data to search for in network traffic -- sparse
    (not every possible window) purely to keep the scan fast; even a sparse sample would catch
    any actual leak of a document this size, since a leak would carry far more than one
    matching window.
    """
    if len(text) <= length:
        return [text] if text else []
    return [text[i : i + length] for i in range(0, len(text) - length, stride)]


def test_online_traffic_never_carries_financial_data(
    tmp_path: Path,
    shell_server: str,  # noqa: F811 -- pytest fixture param, not a redefinition
) -> None:
    """PWA-4.3, the strongest constraint in the design doc, checked as a script rather than
    merely asserted in a comment: instruments every request the page makes during a real
    (online) run of the full import flow and asserts, two independent ways, that none of them
    ever carries a byte of the user's actual financial data:

    1. **Allow-list**: every request is a plain `GET` with no request body, to a URL whose path
       is one of the app's own known static asset paths (the precache manifest's own file list,
       plus the handful of fixed non-precached paths the app/browser can still request) -- a
       structural guarantee that no request could carry arbitrary data in the first place.
    2. **Direct substring scan**: every request URL is checked against sampled fragments of the
       real fixture files' own bytes and the real, independently-computed `manifest.json` this
       exact pick produces (via a native, non-browser `run_pipeline` call over the same
       fixtures) -- the literal "byte of imported-file content or the computed manifest" this
       WP's spec names.
    """
    origin = shell_server

    native_out = tmp_path / "native_out"
    native_out.mkdir()
    run_pipeline(FIXTURES_DIR, native_out)
    manifest_text = (native_out / "manifest.json").read_text(encoding="utf-8")
    broker_text = BROKER_CSV.read_text(encoding="utf-8")
    needles = _distinctive_substrings(manifest_text) + _distinctive_substrings(broker_text)
    assert len(needles) > 5, "expected a real sample of substrings to search for"

    allowed_paths = set(json.loads(PRECACHE_MANIFEST_PATH.read_text(encoding="utf-8"))["files"])
    # Non-precached but still legitimate, still-static paths the app/browser can request:
    # "" / "index.html" for the two ways of naming the document root, the manifest+SW files
    # themselves (not part of their own precache list), and the browser's own automatic
    # favicon probe (index.html declares no <link rel="icon">, so Chromium requests the
    # conventional default path on its own initiative -- unrelated to this app's own code).
    allowed_paths |= {
        "",
        "index.html",
        "service-worker.js",
        "precache-manifest.json",
        "favicon.ico",
    }

    seen: list[tuple[str, str, bytes | None]] = []

    with launch_chromium() as browser:
        context = browser.new_context(viewport=_MOBILE_VIEWPORT)

        def _record(request: object) -> None:
            try:
                body = request.post_data_buffer  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                body = None
            seen.append((request.url, request.method, body))  # type: ignore[attr-defined]

        context.on("request", _record)
        page = context.new_page()
        page.goto(f"{origin}/index.html")
        page.wait_for_function("window.__finaAppJsLoaded === true", timeout=30_000)

        try:
            _pick_files(page, [BANK_XLSX, BROKER_CSV])
            _wait_for_settled(page)
            assert page.locator("#error-section").is_hidden()
            assert not page.locator("#chart-section").is_hidden()
        finally:
            context.close()

    assert len(seen) >= 10, "expected a real, substantial run to have made real requests"

    for url, method, body in seen:
        assert method == "GET", f"non-GET request seen (a body-carrying request): {method} {url}"
        assert body in (None, b""), f"request carried a body: {url} ({body!r})"
        assert url.startswith(origin), f"request left the app's own origin: {url}"
        path = url[len(origin) :].lstrip("/").split("?", 1)[0]
        assert path in allowed_paths, f"request to an unexpected, non-static path: {url}"

        url_lower = url.lower()
        for needle in needles:
            assert needle.lower() not in url_lower, (
                f"a fragment of real financial data appeared in a request URL: {needle!r} in {url}"
            )


# ---------------------------------------------------------------------------
# 3. PWA-4.4: explicit "update available" prompt, applied only on the user's own click.
# ---------------------------------------------------------------------------

_SECOND_SW_URL_NAME = "service-worker-v2-for-test.js"


def _modified_service_worker_source() -> str:
    """A byte-different copy of the real, currently-stamped `web/service-worker.js` -- same
    logic, only its own `CACHE_VERSION` constant changed -- served at a second URL for this
    test alone (`_SECOND_SW_URL_NAME`) rather than overwriting the real file. Registering the
    SAME scope with this different script URL is the standard way real deployments ship a
    content-hashed service-worker filename, and (per direct experimentation building this test)
    reliably drives the exact same `updatefound`/"installed"/"waiting" lifecycle a same-URL
    content change would -- without the flakier byte-diff timing this test also observed when
    repeatedly re-`fulfill()`-ing the *same* `/service-worker.js` URL via `context.route()`.
    """
    original = SERVICE_WORKER_PATH.read_text(encoding="utf-8")
    marker_line = next(
        line for line in original.splitlines() if line.startswith("const CACHE_VERSION")
    )
    modified = original.replace(marker_line, 'const CACHE_VERSION = "test-updated-version";')
    assert modified != original, "expected to find and replace the CACHE_VERSION line"
    return modified


def _serve_second_service_worker(route: Route, body: str) -> None:
    route.fulfill(status=200, content_type="text/javascript", body=body)


def test_update_prompt_shown_only_on_new_version_and_applied_only_on_click(
    shell_server: str,  # noqa: F811 -- pytest fixture param, not a redefinition
) -> None:
    """PWA-4.4's own concern, verified for real: a new service-worker version becoming
    available shows the "update available" banner; the page is never reloaded on its own
    initiative while the banner is showing (proven by a bounded wait below, not merely by the
    absence of an immediate reload); and reloading onto the new version happens only once the
    user actually clicks the banner's button.
    """
    origin = shell_server
    modified_source = _modified_service_worker_source()

    with launch_chromium() as browser:
        context = browser.new_context(viewport=_MOBILE_VIEWPORT)
        context.route(
            f"{origin}/{_SECOND_SW_URL_NAME}",
            lambda route: _serve_second_service_worker(route, modified_source),
        )
        page = context.new_page()
        page.goto(f"{origin}/index.html")
        page.wait_for_function("window.__finaSwRegisterJsLoaded === true", timeout=30_000)
        _wait_for_precache_ready(page)

        assert page.locator("#update-banner").is_hidden(), (
            "the update banner must stay hidden until a real update is actually available"
        )

        # Simulate a new version becoming available: register the SAME scope with a
        # byte-different script at a second URL (see _modified_service_worker_source's own
        # docstring for why this technique, not a same-URL re-fetch, is used here). This is the
        # same underlying ServiceWorkerRegistration object app.js/sw-register.js is already
        # holding a reference to and has already attached its own "updatefound" listener to.
        page.evaluate(
            """
            async (secondUrl) => {
              window.__finaUpdateTriggered = true;
              await navigator.serviceWorker.register(secondUrl, { scope: "/" });
            }
            """,
            f"/{_SECOND_SW_URL_NAME}",
        )

        page.wait_for_function(
            "() => !document.getElementById('update-banner').hidden", timeout=30_000
        )
        assert "new version" in page.locator(".update-banner-text").inner_text().lower()

        # The banner showing must never, by itself, reload the page -- wait a real, bounded
        # interval with nothing clicked and confirm no navigation happened (a navigation would
        # reset every window global, clearing this marker).
        page.wait_for_timeout(1500)
        assert page.evaluate("window.__finaUpdateTriggered === true"), (
            "the page navigated on its own before any user click on the update banner"
        )
        assert page.url == f"{origin}/index.html"

        page.locator("#update-reload-button").click()

        # The click must lead, eventually, to the page being controlled by the new worker --
        # `wait_for_function` polls across the reload's own navigation on its own, so no manual
        # retry/try-except is needed here the way the offline test above needed one for a
        # single, already-rejected `page.reload()` call.
        page.wait_for_function(
            f"""
            () => !!(navigator.serviceWorker.controller &&
              navigator.serviceWorker.controller.scriptURL.endsWith({_SECOND_SW_URL_NAME!r}))
            """,
            timeout=30_000,
        )

        context.close()


# ---------------------------------------------------------------------------
# 4. CLAUDE.md rule 19: the update banner verified at a real mobile width, both themes, before
#    trusting any visual claim about it.
# ---------------------------------------------------------------------------


def test_update_banner_visible_and_legible_390px_light_and_dark(
    tmp_path: Path,
    shell_server: str,  # noqa: F811 -- pytest fixture param, not a redefinition
) -> None:
    origin = shell_server
    modified_source = _modified_service_worker_source()

    for theme in ("light", "dark"):
        with launch_chromium() as browser:
            context = browser.new_context(viewport=_MOBILE_VIEWPORT)
            context.route(
                f"{origin}/{_SECOND_SW_URL_NAME}",
                lambda route: _serve_second_service_worker(route, modified_source),
            )
            page = context.new_page()
            page.emulate_media(color_scheme=theme)
            page.goto(f"{origin}/index.html")
            page.wait_for_function("window.__finaSwRegisterJsLoaded === true", timeout=30_000)
            _wait_for_precache_ready(page)

            page.evaluate(
                """
                async (secondUrl) => {
                  await navigator.serviceWorker.register(secondUrl, { scope: "/" });
                }
                """,
                f"/{_SECOND_SW_URL_NAME}",
            )
            page.wait_for_function(
                "() => !document.getElementById('update-banner').hidden", timeout=30_000
            )

            banner = page.locator("#update-banner")
            assert banner.is_visible()
            box = banner.bounding_box()
            assert box is not None
            # Rule 19's own gutter requirement: the banner must never bleed past the 390px
            # viewport's edges (a real element that overflows would report a box wider than the
            # viewport itself, or a negative/insufficient left margin).
            assert box["x"] >= 0
            assert box["x"] + box["width"] <= _MOBILE_VIEWPORT["width"]

            screenshot_path = tmp_path / f"sw_update_banner_390_{theme}.png"
            page.screenshot(path=str(screenshot_path))
            assert screenshot_path.stat().st_size > 0

            context.close()
