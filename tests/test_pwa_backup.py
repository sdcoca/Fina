"""Real-browser verification for backup/export (WP-17, storage-eviction mitigation).

Follows the house pattern `tests/test_pwa_storage.py` (WP-15) already established -- reused
directly here rather than duplicated: `shell_server`, `_pick_files`, `_wait_for_settled`,
`_summary_dd_texts`, `_expected_summary_for` and `_block_everything_off_origin` are imported
straight from `test_pwa_shell.py`, the same real, pytest-discoverable fixture/helper set every
other `web/` Playwright suite in this project already builds on. A local `http.server` (no
COOP/COEP headers) serves the real `web/` tree, a real headless Chromium via
`browser_support.launch_chromium()`, and `context.route` blocks every off-origin request so a
pass is real proof of offline/local-only operation.

The core proof, in one continuous narrative (`test_export_wipe_restore_round_trip_is_byte_identical`):

1. Pick both real fixtures, let the run succeed, confirm `rawFiles` has 2 entries.
2. Toggle the broker file inactive -- re-runs over the bank-only active set -- so the backup
   round trip below has to preserve a genuinely non-default `active` flag, not just bytes.
3. Click "Export backup"; capture the downloaded archive's real bytes via
   `page.expect_download()`.
4. **Wipe IndexedDB for real**: close `storage.js`'s own cached connection (`storage.closeDb()`,
   added for exactly this purpose) so `indexedDB.deleteDatabase("fina")` is not left blocked by
   this module's own open connection, then await that delete's own `onsuccess` -- a genuine,
   awaited deletion, not a fire-and-forget call. Reload the page and confirm the stored-files
   checklist is now empty: proof the wipe was real, not merely visually cleared.
5. Feed the captured backup bytes back through "Restore from backup" (`set_input_files` against
   a real temp file, since Playwright needs a real path to upload from).
6. Confirm: both files reappear, both still recognized as the correct adapter, and the
   broker file is still inactive (its pre-export state) while the bank file is still active --
   Q-M's persistent-library model makes `active` meaningfully part of what a backup restores,
   not just the bytes.
7. Confirm the post-restore bank-only run (the current active set right after restore) is
   byte-identical to the pre-wipe bank-only `runCache` record, AND to a fresh native
   `fina.cli` build over the bank fixture alone.
8. Re-activate the broker file and confirm the resulting combined run is *also* byte-identical
   to a fresh native `fina.cli` build over both fixtures -- proof the broker file's own bytes,
   not only the bank file's, survived the export/wipe/restore round trip intact.

A second case (`test_restore_rejects_a_foreign_zip_without_touching_existing_files`) proves the
restore path fails loudly and non-destructively on a zip that is not a Fina backup archive: the
existing stored files and `runCache` are left completely untouched.

A third case (`test_backup_export_button_and_restore_input_render_at_390px`) is the mobile-first
visual check (CLAUDE.md rule 19): a real screenshot of the backup UI at 390px, both themes --
looked at, not merely asserted on computed styles -- taken with the whole viewport in frame (not
`full_page=True`; see this WP's own task instructions on the sandboxed chart iframe's known
`full_page` screenshot artifact) so nothing here mistakes an unrelated, already-documented issue
for a defect in this WP's own new UI.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from playwright.sync_api import Page

from browser_support import launch_chromium
from fina import cli
from test_pwa_shell import (
    _MOBILE_VIEWPORT,
    BANK_XLSX,
    BROKER_CSV,
    FIXTURES_DIR,
    _expected_summary_for,
    _open_shell_page,
    _pick_files,
    _summary_dd_texts,
    _wait_for_settled,
    shell_server,  # noqa: F401 -- imported for its side effect as a pytest fixture
)

_GENEROUS_TIMEOUT_MS = 180_000
_DB_NAME = "fina"  # web/js/storage.js's own DB_NAME -- confirmed by reading that module.

_GARBAGE_ZIP_CONTENT = b"this zip is not a Fina backup archive at all\n"


def _sha256_hex(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_seq(page: Page) -> int:
    return int(page.evaluate("window.__finaRunSeq || 0"))


def _backup_seq(page: Page) -> int:
    return int(page.evaluate("window.__finaBackupSeq || 0"))


def _wait_for_next_run(page: Page, prev_seq: int) -> None:
    """See `test_pwa_storage.py`'s identical helper -- waits for a `runOverActiveSet()` call
    strictly after `prev_seq` to have fully settled, rather than racing the generic
    chart/error-visible check against a chart that may already be showing from a prior run.
    """
    page.wait_for_function(
        "(prev) => (window.__finaRunSeq || 0) > prev", arg=prev_seq, timeout=_GENEROUS_TIMEOUT_MS
    )


def _wait_for_next_backup_action(page: Page, prev_seq: int) -> None:
    """Waits for one export-click or restore-pick action strictly after `prev_seq` to have fully
    settled (`app.js`'s own `__finaBackupSeq`, bumped once per completed export/restore attempt,
    success or failure alike, after the status line and -- for a restore that added files -- the
    following run have both already updated the DOM).
    """
    page.wait_for_function(
        "(prev) => (window.__finaBackupSeq || 0) > prev",
        arg=prev_seq,
        timeout=_GENEROUS_TIMEOUT_MS,
    )


def _get_run_cache(page: Page) -> dict[str, object] | None:
    return page.evaluate(
        "async () => { const s = await import('/js/storage.js'); return await s.getRunCache(); }"
    )


def _list_raw_files(page: Page) -> list[dict[str, object]]:
    return page.evaluate(
        "async () => { const s = await import('/js/storage.js'); return await s.listRawFiles(); }"
    )


def _wipe_indexeddb_for_real(page: Page, origin: str) -> None:
    """Genuinely deletes the `fina` IndexedDB database (not merely clearing the UI), then
    reloads the shell page from scratch -- the real-eviction simulation this WP's own verify
    instruction asks for.

    `storage.closeDb()` closes `storage.js`'s own cached connection first so the subsequent
    `indexedDB.deleteDatabase(...)` is not left pending/"blocked" by this same page's own open
    connection (ordinary usage never has a reason to close it itself, which is why that helper
    exists at all -- see its own docstring in `web/js/storage.js`). The delete's own `onsuccess`
    is awaited for real, not fired-and-forgotten.
    """
    page.evaluate(
        """
        async (dbName) => {
          const s = await import('/js/storage.js');
          await s.closeDb();
          await new Promise((resolve, reject) => {
            const req = indexedDB.deleteDatabase(dbName);
            req.onsuccess = () => resolve();
            req.onerror = () => reject(req.error);
            req.onblocked = () => resolve();
          });
        }
        """,
        _DB_NAME,
    )
    page.goto(f"{origin}/index.html")
    page.wait_for_function("window.__finaChecklistReady === true", timeout=30_000)


# ---------------------------------------------------------------------------
# 1. The core proof: export -> real IndexedDB wipe -> restore -> byte-identical state.
# ---------------------------------------------------------------------------


def test_export_wipe_restore_round_trip_is_byte_identical(
    tmp_path: Path, shell_server: str  # noqa: F811 -- pytest fixture param, not a redefinition
) -> None:
    bank_id = _sha256_hex(BANK_XLSX)
    broker_id = _sha256_hex(BROKER_CSV)

    bank_only_dir = tmp_path / "bank_only"
    bank_only_dir.mkdir()
    (bank_only_dir / BANK_XLSX.name).write_bytes(BANK_XLSX.read_bytes())
    expected_bank_only = _expected_summary_for(bank_only_dir)
    expected_combined = _expected_summary_for(FIXTURES_DIR)
    assert expected_combined["real_net_worth"] != expected_bank_only["real_net_worth"]

    # Native byte-identical targets, computed independently of anything the browser produces.
    native_bank_out = tmp_path / "native_bank_out"
    assert cli.main(["build", "--input", str(bank_only_dir), "--out", str(native_bank_out)]) == 0
    native_bank_manifest = (native_bank_out / "manifest.json").read_bytes()
    native_bank_chart = (native_bank_out / "section1_chart.html").read_bytes()

    native_combined_out = tmp_path / "native_combined_out"
    assert cli.main(["build", "--input", str(FIXTURES_DIR), "--out", str(native_combined_out)]) == 0
    native_combined_manifest = (native_combined_out / "manifest.json").read_bytes()
    native_combined_chart = (native_combined_out / "section1_chart.html").read_bytes()

    with launch_chromium() as browser:
        context, page = _open_shell_page(
            browser, shell_server, viewport=_MOBILE_VIEWPORT, color_scheme="light"
        )
        try:
            # --- 1. Pick both fixtures; run succeeds; both stored. ---
            _pick_files(page, [BANK_XLSX, BROKER_CSV])
            _wait_for_settled(page)
            assert page.locator("#error-section").is_hidden()
            assert len(_list_raw_files(page)) == 2

            # --- 2. Toggle broker inactive -- a genuinely non-default state to round-trip. ---
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

            pre_wipe_cache = _get_run_cache(page)
            assert pre_wipe_cache is not None
            assert pre_wipe_cache["activeIds"] == [bank_id]
            assert pre_wipe_cache["manifestJson"].encode("utf-8") == native_bank_manifest
            assert pre_wipe_cache["chartHtml"].encode("utf-8") == native_bank_chart

            # --- 3. Export backup; capture the real downloaded bytes. ---
            # The download now happens in a separate popup window (opened synchronously in the
            # click handler, written to once the async Pyodide/zip work finishes) -- a real-
            # device bug fix (see backup.js's own _downloadInWindow docstring): a plain
            # anchor.click() issued after that async work was silently ignored on a real mobile
            # browser, with no error and no download. The download event now fires on that
            # popup page, not the main `page`, so it must be awaited there.
            with context.expect_page() as popup_info:
                page.locator("#export-backup-button").click()
            popup = popup_info.value
            download = popup.wait_for_event("download", timeout=60_000)
            backup_path = tmp_path / "captured_backup.zip"
            download.save_as(str(backup_path))
            assert backup_path.stat().st_size > 0
            # Sanity: it really is a well-formed zip, not merely non-empty bytes.
            assert zipfile.is_zipfile(backup_path)

            # --- 4. Wipe IndexedDB for real; reload; confirm a genuinely empty checklist. ---
            _wipe_indexeddb_for_real(page, shell_server)
            assert page.locator("#stored-files-list li").count() == 0
            assert not page.locator("#stored-files-empty").is_hidden()
            assert _get_run_cache(page) is None

            # --- 5. Restore from the captured backup. ---
            prev_backup_seq = _backup_seq(page)
            page.locator("#restore-backup-input").set_input_files(str(backup_path))
            _wait_for_next_backup_action(page, prev_backup_seq)

            # --- 6. Both files reappear, correctly recognized, with active state preserved. ---
            rows = page.locator("#stored-files-list li")
            assert rows.count() == 2
            restored_files = _list_raw_files(page)
            by_id = {f["id"]: f for f in restored_files}
            assert set(by_id) == {bank_id, broker_id}
            assert by_id[bank_id]["recognizedAs"] == "bank_es_xlsx"
            assert by_id[bank_id]["active"] is True
            assert by_id[broker_id]["recognizedAs"] == "trade_republic_broker_csv"
            assert by_id[broker_id]["active"] is False, (
                "the broker file's pre-export inactive state must survive the backup round trip"
            )

            bank_checkbox = page.locator(f'li[data-file-id="{bank_id}"] .stored-file-checkbox')
            assert bank_checkbox.is_checked()
            broker_checkbox = page.locator(f'li[data-file-id="{broker_id}"] .stored-file-checkbox')
            assert not broker_checkbox.is_checked()

            # --- 7. The post-restore run (bank-only active set) is byte-identical to both the
            # pre-wipe cache and a fresh native build. ---
            assert page.locator("#error-section").is_hidden()
            assert _summary_dd_texts(page) == [
                expected_bank_only["as_of"],
                expected_bank_only["completeness"],
                f"{expected_bank_only['real_net_worth']} EUR",
                f"{expected_bank_only['savings_only']} EUR",
                f"{expected_bank_only['gap']} EUR",
            ]
            post_restore_cache = _get_run_cache(page)
            assert post_restore_cache is not None
            assert post_restore_cache["activeIds"] == [bank_id]
            assert post_restore_cache["manifestJson"] == pre_wipe_cache["manifestJson"]
            assert post_restore_cache["chartHtml"] == pre_wipe_cache["chartHtml"]
            assert post_restore_cache["manifestJson"].encode("utf-8") == native_bank_manifest
            assert post_restore_cache["chartHtml"].encode("utf-8") == native_bank_chart

            # --- 8. Re-activate the broker file: the combined run must ALSO be byte-identical
            # to a fresh native build -- proof the broker file's own bytes (not only the bank
            # file's) survived the round trip intact, not merely its metadata. ---
            prev_seq = _run_seq(page)
            broker_checkbox.check()
            _wait_for_next_run(page, prev_seq)
            assert page.locator("#error-section").is_hidden()
            assert _summary_dd_texts(page) == [
                expected_combined["as_of"],
                expected_combined["completeness"],
                f"{expected_combined['real_net_worth']} EUR",
                f"{expected_combined['savings_only']} EUR",
                f"{expected_combined['gap']} EUR",
            ]
            final_cache = _get_run_cache(page)
            assert final_cache is not None
            assert sorted(final_cache["activeIds"]) == sorted([bank_id, broker_id])
            assert final_cache["manifestJson"].encode("utf-8") == native_combined_manifest
            assert final_cache["chartHtml"].encode("utf-8") == native_combined_chart
        finally:
            context.close()


# ---------------------------------------------------------------------------
# 2. Restoring a foreign/corrupt archive fails loudly and touches nothing existing.
# ---------------------------------------------------------------------------


def test_restore_rejects_a_foreign_zip_without_touching_existing_files(
    tmp_path: Path, shell_server: str  # noqa: F811 -- pytest fixture param, not a redefinition
) -> None:
    with launch_chromium() as browser:
        context, page = _open_shell_page(
            browser, shell_server, viewport=_MOBILE_VIEWPORT, color_scheme="light"
        )
        try:
            _pick_files(page, [BANK_XLSX])
            _wait_for_settled(page)
            assert page.locator("#error-section").is_hidden()
            cache_before = _get_run_cache(page)
            assert cache_before is not None
            files_before = _list_raw_files(page)
            assert len(files_before) == 1

            # A zip that has nothing to do with Fina's own backup format (no manifest.json).
            foreign_zip = tmp_path / "not_a_fina_backup.zip"
            with zipfile.ZipFile(foreign_zip, mode="w") as zf:
                zf.writestr("readme.txt", _GARBAGE_ZIP_CONTENT)

            prev_backup_seq = _backup_seq(page)
            page.locator("#restore-backup-input").set_input_files(str(foreign_zip))
            _wait_for_next_backup_action(page, prev_backup_seq)

            assert not page.locator("#backup-status").is_hidden()
            status_text = page.locator("#backup-status").inner_text()
            assert "backup" in status_text.lower()
            assert "backup-status--error" in (
                page.locator("#backup-status").get_attribute("class") or ""
            )

            # Nothing existing was disturbed by the failed restore attempt.
            assert _list_raw_files(page) == files_before
            assert _get_run_cache(page) == cache_before
            assert page.locator("#error-section").is_hidden()
        finally:
            context.close()


# ---------------------------------------------------------------------------
# 3. Mobile-first visual check (rule 19): 390px, both themes, looked at.
# ---------------------------------------------------------------------------


def test_backup_export_button_and_restore_input_render_at_390px(
    tmp_path: Path, shell_server: str  # noqa: F811 -- pytest fixture param, not a redefinition
) -> None:
    """Screenshots the backup section at a real phone width in both themes. Not `full_page=True`
    -- this project's own established artifact means a sandboxed chart iframe can render
    misleadingly blank in a full-page capture that requires scrolling; the backup section itself
    sits above the chart in document order and is fully in the initial viewport at this size, so
    a plain (non-full-page) screenshot is both sufficient and the one that avoids that artifact.
    """
    for theme in ("light", "dark"):
        with launch_chromium() as browser:
            context, page = _open_shell_page(
                browser, shell_server, viewport=_MOBILE_VIEWPORT, color_scheme=theme
            )
            try:
                assert not page.locator("#backup-section").is_hidden()
                assert not page.locator("#export-backup-button").is_hidden()
                assert not page.locator("#restore-backup-input").is_hidden()

                screenshot_path = tmp_path / f"backup_section_390_{theme}.png"
                page.screenshot(path=str(screenshot_path))
                assert screenshot_path.stat().st_size > 0
            finally:
                context.close()
