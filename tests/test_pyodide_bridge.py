"""Differential test for the Pyodide bridge (WP-12).

`web/py/bridge.py` is meant to compute *exactly* what `fina.cli._build` computes natively --
same pipeline, same rendering, run inside a WASM-compiled CPython instead of a native one. The
strongest available proof of that is not reading the JS/Python source and reasoning about it;
it is running both over the same input bytes and diffing their output byte-for-byte. That is
all this file does, following the house pattern `tests/test_pyodide_vendor.py` (WP-11)
established: a local `http.server` (no COOP/COEP headers, matching WP-11's own determination
that neither is required), a real headless Chromium via `browser_support.launch_chromium()`,
`context.route` to abort every off-origin request so a pass is real proof of offline/local-only
operation, and no mocks anywhere in the Python-vs-browser comparison itself.

Four cases, matching WP-12's spec verbatim:

1. `test_pyodide_bridge_js_loads_without_syntax_error` -- `web/js/pyodide-bridge.js` parses
   and its ES module body executes inside a real browser page, independent of whether a
   Pyodide runtime is ever booted (WP-12 task 2/3: "a full UI doesn't exist yet... but it must
   be real, working code... sanity-check that pyodide-bridge.js at least parses/loads without
   a syntax error").
2. `test_bridge_sniff_returns_none_for_an_unrecognized_file` -- `sniff()` never raises, and
   returns `None` for a file matching no known adapter shape (mirrors
   `test_t502_unrecognized_shape_raises_listing_adapters_tried`'s fixture, but through
   `sniff_adapter_name`'s non-raising contract instead of `_select_adapter`'s raising one).
3. `test_bridge_run_is_byte_identical_to_the_native_cli_over_the_real_fixtures` -- the core
   differential proof: `manifest.json` and `section1_chart.html`, and the
   warnings/summary figures, match byte-for-byte / value-for-value between a native
   `fina.cli.main(["build", ...])` run and the browser bridge's `run()` over the same two
   fixtures' bytes.
4. `test_bridge_run_returns_a_structured_error_and_writes_nothing_on_reconciliation_failure`
   -- a fixture built the same way `tests/test_pipeline.py`'s own
   `test_run_pipeline_genuinely_wires_header_balances_into_reconcile` builds one (corrupting
   only the bank export's header-stated balance via `builders.bank_xlsx_with`) makes `run()`
   return a structured error with every success-only field (`manifest_json`, `chart_html`,
   `summary`) absent/`None` -- not merely "unchecked", each is asserted explicitly.
"""

from __future__ import annotations

import base64
import http.server
import shutil
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from openpyxl.worksheet.worksheet import Worksheet
from playwright.sync_api import Browser, BrowserContext, Page, Route

from browser_support import launch_chromium
from builders import BANK_XLSX, bank_xlsx_with
from fina import cli
from fina.errors import ReconciliationError
from fina.money import round_half_up
from fina.pipeline import run_pipeline

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"
FIXTURES_DIR = BANK_XLSX.parent  # tests/fixtures -- exactly banco_ejemplo.xlsx + broker_ejemplo.csv

# The harness page imports the *real* web/js/pyodide-bridge.js as an ES module and drives its
# exported `sniffFile`/`runBuild` -- this is the actual production code path, not a
# reimplementation of it for testing purposes. Every task is started (`__finaStart*`) and
# polled to completion (`window.__finaTask`) rather than relying on Playwright's own
# `evaluate()` promise-await timeout, matching `test_pyodide_vendor.py`'s
# `window.__finaSmokeDone` convention -- a Pyodide cold boot + wheel install is slow enough
# that a single generous `wait_for_function` timeout is the right tool, not `evaluate()`'s.
_HARNESS_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>fina pyodide bridge differential harness</title></head>
<body>
<script src="/vendor/pyodide/pyodide.js"></script>
<script type="module">
import { sniffFile, runBuild } from "/js/pyodide-bridge.js";

function b64ToUint8Array(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function settle(promise) {
  window.__finaTask = { done: false, result: null };
  promise
    .then((result) => {
      window.__finaTask = { done: true, result: result === undefined ? null : result };
    })
    .catch((err) => {
      const message = (err && err.stack) ? err.stack : String(err);
      window.__finaTask = { done: true, result: { __jsError: message } };
    });
}

window.__finaStartSniff = function (filename, b64) {
  const file = new File([b64ToUint8Array(b64)], filename);
  settle(sniffFile(file));
};

window.__finaStartRun = function (filesB64) {
  const files = filesB64.map((f) => new File([b64ToUint8Array(f.b64)], f.filename));
  settle(runBuild(files));
};

window.__finaBridgeReady = true;
</script>
</body></html>
"""


class _ServeHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the temp `serve_root` (symlinked `vendor/`, `js/`, `py/` plus the in-memory
    harness page) -- everything the browser needs, same-origin, no network."""

    def __init__(self, *args: object, serve_root: Path, **kwargs: object) -> None:
        self._serve_root = serve_root
        super().__init__(*args, directory=str(serve_root), **kwargs)  # type: ignore[arg-type]

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        pass  # keep pytest output quiet; failures surface via the test's own assertions


@pytest.fixture
def bridge_server() -> Iterator[str]:
    """A local HTTP server (no COOP/COEP headers) exposing `web/vendor`, `web/js`, `web/py` at
    their real repo-relative URLs (so `web/js/pyodide-bridge.js`'s own relative fetch of
    `../py/bridge.py` resolves correctly), plus `/index.html` serving the harness above.
    """
    assert (WEB_DIR / "vendor" / "pyodide" / "pyodide.asm.wasm").is_file(), (
        "web/vendor/pyodide/ is not populated -- run `python3 web/vendor/build.py`"
    )
    assert (WEB_DIR / "js" / "pyodide-bridge.js").is_file()
    assert (WEB_DIR / "py" / "bridge.py").is_file()

    with tempfile.TemporaryDirectory(prefix="fina-pyodide-bridge-serve-") as tmp:
        serve_root = Path(tmp)
        (serve_root / "vendor").symlink_to(WEB_DIR / "vendor", target_is_directory=True)
        (serve_root / "js").symlink_to(WEB_DIR / "js", target_is_directory=True)
        (serve_root / "py").symlink_to(WEB_DIR / "py", target_is_directory=True)
        (serve_root / "index.html").write_text(_HARNESS_HTML, encoding="utf-8")

        def handler_factory(*args: object, **kwargs: object) -> _ServeHandler:
            return _ServeHandler(*args, serve_root=serve_root, **kwargs)

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


def _block_everything_off_origin(origin: str) -> Any:
    def handler(route: Route) -> None:
        if route.request.url.startswith(origin + "/"):
            route.continue_()
        else:
            route.abort()

    return handler


def _open_bridge_page(browser: Browser, origin: str) -> tuple[BrowserContext, Page]:
    context = browser.new_context()
    context.route("**/*", _block_everything_off_origin(origin))
    page = context.new_page()
    page.goto(f"{origin}/index.html")
    page.wait_for_function("window.__finaBridgeReady === true", timeout=30_000)
    return context, page


def _run_task(page: Page, start_expression: str, arg: object) -> Any:
    """Kicks off one `window.__finaStart*` call and waits for it to settle, returning its
    result -- or failing loudly if the JS side itself threw (an `__jsError` marker), so a
    JS-level exception can never be mistaken for a legitimate falsy Python result.
    """
    page.evaluate(start_expression, arg)
    page.wait_for_function("window.__finaTask && window.__finaTask.done === true", timeout=180_000)
    result = page.evaluate("window.__finaTask.result")
    if isinstance(result, dict) and "__jsError" in result:
        raise AssertionError(f"bridge JS call raised: {result['__jsError']}")
    return result


def _encode_file(path: Path) -> dict[str, str]:
    return {"filename": path.name, "b64": base64.b64encode(path.read_bytes()).decode("ascii")}


# ---------------------------------------------------------------------------
# 1. web/js/pyodide-bridge.js loads as a valid ES module
# ---------------------------------------------------------------------------


def test_pyodide_bridge_js_loads_without_syntax_error(bridge_server: str) -> None:
    """Proves `web/js/pyodide-bridge.js` parses and its module body executes in a real
    browser, independent of ever booting Pyodide: `window.__finaBridgeReady` is only set
    *after* the `import { sniffFile, runBuild } from "/js/pyodide-bridge.js"` line above it
    has succeeded, and the module's own top-level line sets a second, independent flag.
    """
    with launch_chromium() as browser:
        context, page = _open_bridge_page(browser, bridge_server)
        try:
            assert page.evaluate("window.__finaPyodideBridgeJsLoaded") is True
            assert page.evaluate("typeof window.__finaStartSniff") == "function"
            assert page.evaluate("typeof window.__finaStartRun") == "function"
        finally:
            context.close()


# ---------------------------------------------------------------------------
# 2. sniff() on an unrecognized file -> None, no exception
# ---------------------------------------------------------------------------


def test_bridge_sniff_returns_none_for_an_unrecognized_file(bridge_server: str) -> None:
    garbage = base64.b64encode(b"this is not any known export format\n").decode("ascii")
    with launch_chromium() as browser:
        context, page = _open_bridge_page(browser, bridge_server)
        try:
            result = _run_task(
                page,
                "([name, b64]) => window.__finaStartSniff(name, b64)",
                ["garbage.txt", garbage],
            )
        finally:
            context.close()
    assert result is None


# ---------------------------------------------------------------------------
# 3. The core differential proof: run() vs. the native CLI, byte-identical
# ---------------------------------------------------------------------------


def test_bridge_run_is_byte_identical_to_the_native_cli_over_the_real_fixtures(
    tmp_path: Path, bridge_server: str
) -> None:
    fixture_paths = sorted(FIXTURES_DIR.iterdir())
    assert {p.name for p in fixture_paths} == {"banco_ejemplo.xlsx", "broker_ejemplo.csv"}

    # --- native side: the exact same call `fina build --input ... --out ...` makes. ---
    native_out = tmp_path / "native_out"
    exit_code = cli.main(["build", "--input", str(FIXTURES_DIR), "--out", str(native_out)])
    assert exit_code == 0
    native_manifest = (native_out / "manifest.json").read_bytes()
    native_chart = (native_out / "section1_chart.html").read_bytes()

    # Independently recomputed expected warnings/summary (same functions `_build` itself
    # uses), so the browser bridge's structured fields are checked against real figures, not
    # merely "whatever the native run happened to produce" via string reuse.
    native_result = run_pipeline(FIXTURES_DIR)
    expected_warnings = [w.message for w in native_result.warnings]
    latest = native_result.series[-1]
    expected_summary = {
        "completeness": latest.completeness,
        "as_of": latest.as_of.isoformat(),
        "is_partial": latest.is_partial,
        "real_net_worth": str(round_half_up(latest.real_net_worth)),
        "savings_only": str(round_half_up(latest.savings_only)),
        "gap": str(round_half_up(latest.gap)),
    }

    # --- browser side: the same two fixtures' bytes, through the real bridge. ---
    files_b64 = [_encode_file(p) for p in fixture_paths]
    with launch_chromium() as browser:
        context, page = _open_bridge_page(browser, bridge_server)
        try:
            result = _run_task(
                page, "(filesB64) => window.__finaStartRun(filesB64)", files_b64
            )
        finally:
            context.close()

    assert result["ok"] is True
    assert result["error"] is None

    bridge_manifest = result["manifest_json"].encode("utf-8")
    bridge_chart = result["chart_html"].encode("utf-8")

    # The byte-for-byte assertions WP-12 exists to prove.
    assert bridge_manifest == native_manifest
    assert bridge_chart == native_chart

    assert result["warnings"] == expected_warnings
    assert result["summary"] == expected_summary


# ---------------------------------------------------------------------------
# 4. A reconciliation-failing fixture -> structured error, nothing success-shaped returned
# ---------------------------------------------------------------------------


def test_bridge_run_returns_a_structured_error_and_writes_nothing_on_reconciliation_failure(
    tmp_path: Path, bridge_server: str
) -> None:
    """Mirrors `tests/test_pipeline.py::
    test_run_pipeline_genuinely_wires_header_balances_into_reconcile`'s own construction: only
    the header's own stated balance (R-7.2) is corrupted, so a `ReconciliationError` can only
    come from R-8.5's header cross-check genuinely running -- both natively and through the
    bridge.
    """

    def mutate(ws: Worksheet) -> None:
        ws["D4"] = "1,00€ EUR"  # header balance, unrelated to any row's declared_balance

    corrupted = bank_xlsx_with(tmp_path, mutate, filename="banco_badheader.xlsx")

    # Confirm the fixture construction is genuinely reconciliation-breaking, natively, before
    # trusting the browser side's own error report.
    with pytest.raises(ReconciliationError) as exc_info:
        run_pipeline(tmp_path)
    native_expected = exc_info.value.expected
    native_declared = exc_info.value.declared

    with launch_chromium() as browser:
        context, page = _open_bridge_page(browser, bridge_server)
        try:
            result = _run_task(
                page,
                "(filesB64) => window.__finaStartRun(filesB64)",
                [_encode_file(corrupted)],
            )
        finally:
            context.close()

    assert result["ok"] is False
    # Every success-only field is explicitly absent/None -- not merely unchecked.
    assert result["manifest_json"] is None
    assert result["chart_html"] is None
    assert result["summary"] is None
    assert result["warnings"] == []
    assert result["error"] is not None
    assert result["error"]["type"] == "ReconciliationError"
    assert str(native_expected) in result["error"]["message"]
    assert str(native_declared) in result["error"]["message"]
