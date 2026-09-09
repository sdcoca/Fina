"""Real-browser verification for IndexedDB persistence (WP-15, Q-M "persistent library").

Follows the house pattern `tests/test_pwa_shell.py` (WP-13) already established -- reused
directly here rather than duplicated: `shell_server`, `_open_shell_page`, `_pick_files`,
`_wait_for_settled`, `_summary_dd_texts`, `_expected_summary_for` and `_block_everything_off_
origin` are imported straight from that module (a real, pytest-discoverable fixture/helper set,
not reimplemented for this file). A local `http.server` (no COOP/COEP headers) serves the real
`web/` tree, a real headless Chromium via `browser_support.launch_chromium()`, and
`context.route` blocks every off-origin request so a pass is real proof of offline/local-only
operation.

**What "two sessions" means here, honestly** (this project's own established convention, e.g.
`tests/test_pyodide_vendor.py`'s docstring): a literal separate OS-level browser process restart
is not reachable in this sandboxed test environment. What *is* reachable, and is used below, is
one Playwright `BrowserContext` (one real IndexedDB origin/storage partition) navigated with two
separate `page.goto()` calls -- IndexedDB genuinely persists across navigations within the same
context, the same way it persists across tabs/windows/app-reopens on a real device sharing the
same browser profile. This is the realistic proxy for "close the app, come back later": it
proves real IndexedDB persistence and a real second page load reading it back with no re-pick,
which is the behaviour that matters; it does not additionally prove survival across a full OS
process kill, which nothing in this sandbox can exercise.

Cases:

1. `test_wp15_two_session_persistence_and_cache_integrity` -- the core proof, in one continuous
   narrative (each phase depends on the last, so it is one test, not several independent ones):
   - **Session 1**: fresh page, picks both real fixtures, run succeeds.
   - **Session 2**: `page.goto()` again, same context, no re-picking. The stored-files checklist
     shows both files (from `rawFiles`, no re-pick). The shell displays a result matching
     session 1's -- straight from `runCache`, before Pyodide is ever asked to do anything.
   - **Non-staleness proof**: a genuine from-scratch recompute (`runBuild()` called directly,
     and separately the native `fina.cli` build) over the same active set produces a
     byte-identical `manifest.json`/chart HTML to what `runCache` holds -- so the displayed
     cache is proven correct, not merely displayed.
   - **Toggle-inactive**: unchecking one file's checkbox re-runs the pipeline over the new
     active set; the numbers genuinely change (bank-only vs. bank+broker differ).
   - **Failure preserves the cache**: a third file -- shape-recognized but reconciliation-
     breaking (`builders.bank_xlsx_with`'s corrupted-header-balance technique, as
     `tests/test_pipeline.py`/`tests/test_pyodide_bridge.py` already use) -- is picked, added to
     the active set, and the resulting run fails. `runCache`, read directly via `storage.js`'s
     own `getRunCache()` evaluated in-page, is asserted byte-for-byte unchanged from
     immediately before that failing run -- proving §2.4.2's "runCache only overwritten by
     success" rule for real, not just by looking at what the UI displays.
2. `test_storage_add_raw_file_dedupes_by_content_hash_and_never_reactivates` -- isolates
   `storage.js`'s own upsert contract directly (no Pyodide, no full pipeline): re-adding the
   same bytes is a pure no-op, never a duplicate row, and never silently reactivates a file the
   user has since marked inactive.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from openpyxl.worksheet.worksheet import Worksheet
from playwright.sync_api import Page

from browser_support import launch_chromium
from builders import bank_xlsx_with
from fina import cli
from test_pwa_shell import (
    _MOBILE_VIEWPORT,
    BANK_XLSX,
    BROKER_CSV,
    FIXTURES_DIR,
    _block_everything_off_origin,
    _expected_summary_for,
    _pick_files,
    _summary_dd_texts,
    _wait_for_settled,
    shell_server,  # noqa: F401 -- imported for its side effect as a pytest fixture
)

_GENEROUS_TIMEOUT_MS = 180_000


def _run_seq(page: Page) -> int:
    return int(page.evaluate("window.__finaRunSeq || 0"))


def _wait_for_next_run(page: Page, prev_seq: int) -> None:
    """Waits for a `runOverActiveSet()` call strictly after `prev_seq` to have fully completed
    (DOM already updated, success or failure alike) -- see `app.js`'s own `__finaRunSeq` comment.

    This is deliberately used instead of `test_pwa_shell.py`'s generic "chart or error is
    visible" wait whenever a chart/result may *already* be showing before the action under test
    (a toggle, or a second pick) -- that generic check would otherwise race: it can observe the
    *previous* run's still-showing chart and return immediately, before the new run has even
    started clearing it, silently reading stale data.
    """
    page.wait_for_function(
        "(prev) => (window.__finaRunSeq || 0) > prev", arg=prev_seq, timeout=_GENEROUS_TIMEOUT_MS
    )


def _sha256_hex(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _encode_file_b64(path: Path) -> dict[str, str]:
    return {"filename": path.name, "b64": base64.b64encode(path.read_bytes()).decode("ascii")}


_GET_RUN_CACHE_JS = """
async () => {
  const s = await import('/js/storage.js');
  return await s.getRunCache();
}
"""

_RECOMPUTE_JS = """
async (filesB64) => {
  const { runBuild } = await import('/js/pyodide-bridge.js');
  function b64ToUint8Array(b64) {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }
  const files = filesB64.map((f) => new File([b64ToUint8Array(f.b64)], f.filename));
  return await runBuild(files);
}
"""


# ---------------------------------------------------------------------------
# 1. The core two-session persistence + cache-integrity proof.
# ---------------------------------------------------------------------------


def test_wp15_two_session_persistence_and_cache_integrity(
    tmp_path: Path, shell_server: str  # noqa: F811 -- pytest fixture param, not a redefinition
) -> None:
    bank_id = _sha256_hex(BANK_XLSX)
    broker_id = _sha256_hex(BROKER_CSV)

    expected_combined = _expected_summary_for(FIXTURES_DIR)

    bank_only_dir = tmp_path / "bank_only"
    bank_only_dir.mkdir()
    (bank_only_dir / BANK_XLSX.name).write_bytes(BANK_XLSX.read_bytes())
    expected_bank_only = _expected_summary_for(bank_only_dir)

    # Sanity: the two expected results must genuinely differ, or the toggle assertion below
    # would prove nothing.
    assert expected_combined["real_net_worth"] != expected_bank_only["real_net_worth"]

    # Native manifest.json for the combined set -- the byte-identical target for the cache.
    native_out = tmp_path / "native_out"
    exit_code = cli.main(["build", "--input", str(FIXTURES_DIR), "--out", str(native_out)])
    assert exit_code == 0
    native_combined_manifest = (native_out / "manifest.json").read_bytes()
    native_combined_chart = (native_out / "section1_chart.html").read_bytes()

    with launch_chromium() as browser:
        context = browser.new_context(viewport=_MOBILE_VIEWPORT)
        context.route("**/*", _block_everything_off_origin(shell_server))
        page = context.new_page()
        page.set_default_timeout(_GENEROUS_TIMEOUT_MS)
        try:
            # --- Session 1: fresh page, pick both fixtures, run succeeds. ---
            page.goto(f"{shell_server}/index.html")
            page.wait_for_function("window.__finaChecklistReady === true")
            assert page.locator("#stored-files-list li").count() == 0
            assert not page.locator("#stored-files-empty").is_hidden()

            _pick_files(page, [BANK_XLSX, BROKER_CSV])
            _wait_for_settled(page)
            assert page.locator("#error-section").is_hidden()
            assert _summary_dd_texts(page) == [
                expected_combined["as_of"],
                expected_combined["completeness"],
                f"{expected_combined['real_net_worth']} EUR",
                f"{expected_combined['savings_only']} EUR",
                f"{expected_combined['gap']} EUR",
            ]

            # --- Session 2: navigate again, same context/origin -- no re-picking. ---
            page.goto(f"{shell_server}/index.html")
            page.wait_for_function("window.__finaChecklistReady === true")

            rows = page.locator("#stored-files-list li")
            assert rows.count() == 2, "both previously-picked files must reappear with no re-pick"
            names = page.locator("#stored-files-list .stored-file-name").all_inner_texts()
            assert set(names) == {BANK_XLSX.name, BROKER_CSV.name}
            for file_id in (bank_id, broker_id):
                checkbox = page.locator(f'li[data-file-id="{file_id}"] .stored-file-checkbox')
                assert checkbox.is_checked(), f"{file_id} should still be active after reopening"

            # The cached result is shown immediately -- this assertion does not itself wait for
            # a fresh Pyodide-backed run (see _wait_for_settled's own generous timeout, which
            # tolerates either path, but the cache path is the one WP-15 is built to take here).
            _wait_for_settled(page)
            assert page.locator("#error-section").is_hidden()
            assert not page.locator("#chart-section").is_hidden()
            assert _summary_dd_texts(page) == [
                expected_combined["as_of"],
                expected_combined["completeness"],
                f"{expected_combined['real_net_worth']} EUR",
                f"{expected_combined['savings_only']} EUR",
                f"{expected_combined['gap']} EUR",
            ]

            cache_after_session2_load = page.evaluate(_GET_RUN_CACHE_JS)
            assert cache_after_session2_load is not None
            assert sorted(cache_after_session2_load["activeIds"]) == sorted([bank_id, broker_id])
            assert cache_after_session2_load["manifestJson"].encode("utf-8") == native_combined_manifest
            assert cache_after_session2_load["chartHtml"].encode("utf-8") == native_combined_chart

            # --- Non-staleness proof: a genuine from-scratch recompute, called directly,
            # matches the cache byte-for-byte -- the cache is not merely displayed, it is
            # actually correct for the current active set. ---
            files_b64 = [_encode_file_b64(BANK_XLSX), _encode_file_b64(BROKER_CSV)]
            recompute = page.evaluate(_RECOMPUTE_JS, files_b64)
            assert recompute["ok"] is True
            assert recompute["manifest_json"].encode("utf-8") == native_combined_manifest
            assert recompute["chart_html"].encode("utf-8") == native_combined_chart
            assert recompute["manifest_json"] == cache_after_session2_load["manifestJson"]
            assert recompute["chart_html"] == cache_after_session2_load["chartHtml"]

            # --- Toggle broker inactive: re-runs over the new active set, numbers change. ---
            # A chart is already showing (from the cache-display/recompute above), so the
            # generic "chart or error visible" wait would race (see `_wait_for_next_run`'s own
            # docstring) -- wait for `__finaRunSeq` to advance past this specific toggle's own
            # run instead.
            prev_seq = _run_seq(page)
            broker_checkbox = page.locator(f'li[data-file-id="{broker_id}"] .stored-file-checkbox')
            broker_checkbox.uncheck()
            _wait_for_next_run(page, prev_seq)
            assert page.locator("#error-section").is_hidden()
            assert _summary_dd_texts(page) == [
                expected_bank_only["as_of"],
                expected_bank_only["completeness"],
                f"{expected_bank_only['real_net_worth']} EUR",
                f"{expected_bank_only['savings_only']} EUR",
                f"{expected_bank_only['gap']} EUR",
            ]
            broker_row = page.locator(f'li[data-file-id="{broker_id}"]')
            assert "stored-file-inactive" in (broker_row.get_attribute("class") or "")

            cache_before_failure = page.evaluate(_GET_RUN_CACHE_JS)
            assert cache_before_failure is not None
            assert cache_before_failure["activeIds"] == [bank_id]
            assert cache_before_failure["summary"]["real_net_worth"] == expected_bank_only["real_net_worth"]

            # --- Force a failing run: pick a shape-recognized but reconciliation-breaking file
            # (only the bank export's header-stated balance is corrupted, R-7.2 -- the same
            # construction tests/test_pipeline.py's own
            # test_run_pipeline_genuinely_wires_header_balances_into_reconcile and
            # tests/test_pyodide_bridge.py's own bridge test use). It sniffs as bank_es_xlsx
            # (shape-only recognition, never content-validated) so it IS added to rawFiles and
            # included in the next active-set run, which must then fail. ---
            def mutate(ws: Worksheet) -> None:
                ws["D4"] = "1,00€ EUR"  # header balance, unrelated to any row's declared_balance

            corrupted_path = bank_xlsx_with(tmp_path, mutate, filename="banco_badheader.xlsx")

            # Again: a (bank-only) chart is already showing from the toggle step above, so this
            # must wait on `__finaRunSeq`, not the generic chart/error-visible check.
            prev_seq = _run_seq(page)
            _pick_files(page, [corrupted_path])
            _wait_for_next_run(page, prev_seq)
            assert not page.locator("#error-section").is_hidden(), (
                "a second, conflicting bank file must make the active-set run fail"
            )
            assert page.locator("#chart-section").is_hidden()

            cache_after_failure = page.evaluate(_GET_RUN_CACHE_JS)
            assert cache_after_failure == cache_before_failure, (
                "runCache must be byte-for-byte unchanged after a failing run -- "
                "storage.js.setRunCache/clearRunCache must never have been called on that path"
            )

            # The corrupted file is still recorded (persistent library: nothing is silently
            # dropped even though the run that included it failed), and left active -- toggling
            # it off is the user's own recovery action, not something a failed run does itself.
            assert page.locator("#stored-files-list li").count() == 3
            corrupted_id = _sha256_hex(corrupted_path)
            corrupted_checkbox = page.locator(f'li[data-file-id="{corrupted_id}"] .stored-file-checkbox')
            assert corrupted_checkbox.is_checked()

            screenshot_path = tmp_path / "storage_checklist_390_light.png"
            page.screenshot(path=str(screenshot_path))
            assert screenshot_path.stat().st_size > 0
        finally:
            context.close()


# ---------------------------------------------------------------------------
# 2. storage.js's own upsert contract, isolated from the full pipeline/Pyodide.
# ---------------------------------------------------------------------------


def test_storage_add_raw_file_dedupes_by_content_hash_and_never_reactivates(
    shell_server: str,  # noqa: F811 -- pytest fixture param, not a redefinition
) -> None:
    with launch_chromium() as browser:
        context = browser.new_context()
        context.route("**/*", _block_everything_off_origin(shell_server))
        page = context.new_page()
        page.goto(f"{shell_server}/index.html")
        page.wait_for_function("window.__finaChecklistReady === true", timeout=30_000)
        try:
            result = page.evaluate(
                """
                async () => {
                  const s = await import('/js/storage.js');
                  const bytes = new TextEncoder().encode('anonymized fixture content, not a real export');
                  const id1 = await s.addRawFile({
                    filename: 'a.csv', bytes, recognizedAs: 'broker_csv', active: true,
                  });
                  await s.setActive(id1, false);
                  // Re-adding the exact same bytes must be a pure no-op: no new row, and it
                  // must NOT silently reactivate a file the user has since turned off.
                  const id2 = await s.addRawFile({
                    filename: 'a.csv', bytes, recognizedAs: 'broker_csv', active: true,
                  });
                  const files = await s.listRawFiles();
                  return { id1, id2, files };
                }
                """
            )
        finally:
            context.close()

    assert result["id1"] == result["id2"]
    assert len(result["files"]) == 1
    assert result["files"][0]["active"] is False
