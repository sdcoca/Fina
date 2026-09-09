"""Offline smoke test for the vendored Pyodide runtime + wheels (WP-11).

`web/vendor/` is populated by `web/vendor/build.py`, never by hand -- see that script's own
module docstring for what is pinned and why. This test is the "verify" half of WP-11's spec:

    "an offline micropip.install([...], deps=False) smoke test succeeds with network
    disabled"

It proves, with a real headless browser and real network blocking (not a mock, not a reading
of the JS source), that:

1. The vendored Pyodide core runtime boots from local files alone.
2. `micropip` (itself vendored as an ordinary wheel, not a network fetch) can be loaded and
   used to `micropip.install([...], deps=False)` the vendored `openpyxl`, `et_xmlfile` and
   `fina` wheels, entirely from local files.
3. `import fina, openpyxl` then succeeds inside that Pyodide runtime.
4. None of the above requires `SharedArrayBuffer` / cross-origin isolation (COOP/COEP) --
   the local static server below deliberately sets no such headers, and the test asserts
   `self.crossOriginIsolated` is `false` throughout. This is the evidence behind this WP's
   written COOP/COEP determination in `docs/plan/mobile-pyodide.md`.

A `Page.route` interceptor aborts every request whose URL is not the local test server's own
origin, so a passing test is real proof of offline capability, not merely "should work
offline" reasoning about the JS.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, Route

from browser_support import launch_chromium

_VENDOR_DIR = Path(__file__).resolve().parents[1] / "web" / "vendor"
_PYODIDE_DIR = _VENDOR_DIR / "pyodide"
_WHEELS_DIR = _VENDOR_DIR / "wheels"

_MICROPIP_WHEEL = "micropip-0.11.1-py3-none-any.whl"
_OPENPYXL_WHEEL = "openpyxl-3.1.5-py2.py3-none-any.whl"
_ET_XMLFILE_WHEEL = "et_xmlfile-2.0.0-py3-none-any.whl"
_FINA_WHEEL = "fina-0.1.0-py3-none-any.whl"

_INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>fina pyodide vendor smoke test</title></head>
<body>
<script src="/vendor/pyodide/pyodide.js"></script>
<script>
async function runSmokeTest() {
  const result = { ok: false };
  try {
    result.crossOriginIsolatedBefore = self.crossOriginIsolated;
    result.hasSharedArrayBuffer = typeof SharedArrayBuffer !== "undefined";

    const pyodide = await loadPyodide({ indexURL: "/vendor/pyodide/" });

    result.crossOriginIsolatedAfterLoad = self.crossOriginIsolated;

    // micropip is vendored as an ordinary wheel (see build.py's module docstring for why its
    // sha256 intentionally does not match pyodide-lock.json's own repackaged copy) -- load it
    // as an arbitrary local wheel, skipping the lock-file integrity check meant for
    // lock-listed packages.
    await pyodide.loadPackage("/vendor/wheels/__MICROPIP_WHEEL__", { checkIntegrity: false });

    const code = `
import micropip
await micropip.install([
    "/vendor/wheels/__ET_XMLFILE_WHEEL__",
    "/vendor/wheels/__OPENPYXL_WHEEL__",
    "/vendor/wheels/__FINA_WHEEL__",
], deps=False)
import fina
import openpyxl
f"{fina.__name__}:{openpyxl.__version__}"
`;
    result.installedVersionString = await pyodide.runPythonAsync(code);
    result.ok = true;
  } catch (err) {
    result.error = (err && err.stack) ? err.stack : String(err);
  }
  window.__finaSmokeResult = result;
  window.__finaSmokeDone = true;
}
runSmokeTest();
</script>
</body></html>
"""
_INDEX_HTML = (
    _INDEX_HTML.replace("__MICROPIP_WHEEL__", _MICROPIP_WHEEL)
    .replace("__ET_XMLFILE_WHEEL__", _ET_XMLFILE_WHEEL)
    .replace("__OPENPYXL_WHEEL__", _OPENPYXL_WHEEL)
    .replace("__FINA_WHEEL__", _FINA_WHEEL)
)


class _VendorRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serves `<serve_root>/vendor/...` from the real vendored assets, plus the in-memory
    test page at `/index.html` -- everything the browser needs, same-origin, no network."""

    def __init__(self, *args: object, serve_root: Path, **kwargs: object) -> None:
        self._serve_root = serve_root
        super().__init__(*args, directory=str(serve_root), **kwargs)  # type: ignore[arg-type]

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        pass  # keep pytest output quiet; failures surface via the test's own assertions


@pytest.fixture
def vendor_server() -> Iterator[str]:
    """A local HTTP server (no COOP/COEP headers) rooted so `/vendor/...` serves the real
    `web/vendor/` tree and `/index.html` serves the in-memory smoke-test page.

    `web/vendor/` is a committed build artifact (see `web/vendor/build.py`), not something
    generated on the fly -- a missing file here is a real repository defect (G-8 forbids
    turning that into a silent skip), so this asserts loudly rather than skipping.
    """
    assert (_PYODIDE_DIR / "pyodide.asm.wasm").is_file(), (
        "web/vendor/pyodide/ is not populated -- run `python3 web/vendor/build.py`"
    )
    for wheel in (_MICROPIP_WHEEL, _OPENPYXL_WHEEL, _ET_XMLFILE_WHEEL, _FINA_WHEEL):
        assert (_WHEELS_DIR / wheel).is_file(), (
            f"web/vendor/wheels/{wheel} is missing -- run `python3 web/vendor/build.py`"
        )

    import shutil
    import tempfile

    with tempfile.TemporaryDirectory(prefix="fina-pyodide-vendor-serve-") as tmp:
        serve_root = Path(tmp)
        (serve_root / "vendor").symlink_to(_VENDOR_DIR, target_is_directory=True)
        (serve_root / "index.html").write_text(_INDEX_HTML, encoding="utf-8")

        def handler_factory(*args: object, **kwargs: object) -> _VendorRequestHandler:
            return _VendorRequestHandler(*args, serve_root=serve_root, **kwargs)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_factory)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)
        shutil.rmtree(serve_root, ignore_errors=True)


def _block_everything_off_origin(origin: str) -> object:
    def handler(route: Route) -> None:
        if route.request.url.startswith(origin + "/"):
            route.continue_()
        else:
            route.abort()

    return handler


def test_vendored_pyodide_installs_wheels_fully_offline(vendor_server: str) -> None:
    """WP-11 verify step: with every non-local request aborted, the vendored Pyodide core
    loads, `micropip` (also vendored) installs the vendored `openpyxl`/`et_xmlfile`/`fina`
    wheels via `micropip.install([...], deps=False)`, and `import fina, openpyxl` succeeds --
    entirely from `web/vendor/`, no PyPI, no CDN, no jsdelivr.
    """
    origin = vendor_server
    with launch_chromium() as browser:
        context = browser.new_context()
        context.route("**/*", _block_everything_off_origin(origin))
        page: Page = context.new_page()
        page.goto(f"{origin}/index.html")
        page.wait_for_function("window.__finaSmokeDone === true", timeout=60_000)
        result = page.evaluate("window.__finaSmokeResult")
        context.close()

    assert result.get("ok") is True, result.get("error")
    assert result["installedVersionString"] == "fina:3.1.5"

    # COOP/COEP determination (docs/plan/mobile-pyodide.md): this server sets neither header,
    # so the page is never cross-origin isolated, and the vendored core still worked above --
    # confirming SharedArrayBuffer/COOP-COEP is NOT a hosting requirement for this build.
    assert result["crossOriginIsolatedBefore"] is False
    assert result["crossOriginIsolatedAfterLoad"] is False


def test_unrecognized_wheel_url_is_not_silently_ignored(vendor_server: str) -> None:
    """Negative control: installing a wheel that genuinely isn't vendored (and that the
    network block makes unreachable) must fail loudly, not silently no-op -- otherwise the
    positive test above could be passing for the wrong reason (e.g. a typo'd URL that
    micropip just skips).
    """
    origin = vendor_server
    broken_page = _INDEX_HTML.replace(_OPENPYXL_WHEEL, "openpyxl-99.99.99-py3-none-any.whl")

    with launch_chromium() as browser:
        context = browser.new_context()

        def serve_broken_page(route: Route) -> None:
            if route.request.url == f"{origin}/index.html":
                route.fulfill(status=200, content_type="text/html", body=broken_page)
            elif route.request.url.startswith(origin + "/"):
                route.continue_()
            else:
                route.abort()

        context.route("**/*", serve_broken_page)
        page: Page = context.new_page()
        page.goto(f"{origin}/index.html")
        page.wait_for_function("window.__finaSmokeDone === true", timeout=60_000)
        result = page.evaluate("window.__finaSmokeResult")
        context.close()

    assert result.get("ok") is False
    assert result.get("error")
