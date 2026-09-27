"""Real-browser verification for WP-22b/WP-23: the "Accounts" section (R-3.8).

Same house pattern as `tests/test_pwa_storage.py`: the real `web/` tree over a local server,
headless Chromium at a real phone width (CLAUDE.md rule 19), every off-origin request blocked.

1. `test_accounts_section_might_be_yours_rename_and_files` -- one narrative: loading the broker
   fixture alone opens the Accounts section and its "Might be yours" group on its own (the
   statement account is listed directly, in no group); the account in the owner's name with no statement
   is listed there with its transfers and an "Import its statement" button that opens the file
   picker, and no "Mine / Not mine" choice; the figures are the native CLI's; renaming the
   statement account stores the name in the account-names file (version 3, names only) and
   shows it; its file sits inside it; a reload shows everything from the cache, collapsed. No
   horizontal scroll at 390px, in light and dark themes.
2. `test_a_result_from_another_engine_is_recomputed_not_shown` -- a cached result stamped with
   another engine is never shown: reopening the app recomputes it once (CLAUDE.md rule 12).
3. `test_names_file_helpers` -- `own-accounts.js`'s pure helpers, as renames and a backup
   restore use them, including reading a retired version 1/2 file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import Page

from browser_support import launch_chromium
from test_pwa_shell import (
    _MOBILE_VIEWPORT,
    BROKER_CSV,
    _expected_legend,
    _expected_summary_for,
    _open_shell_page,
    _pick_files,
    _summary_dd_texts,
    _wait_for_settled,
    shell_server,  # noqa: F401 -- imported for its side effect as a pytest fixture
)
from test_pwa_storage import _run_seq, _wait_for_next_run

_GENEROUS_TIMEOUT_MS = 180_000
_IBAN = "ES0000000000000000000202"
_CARD = f'#accounts-groups li[data-iban="{_IBAN}"]'
_BROKER_CARD = '#accounts-groups li[data-account-key="trade_republic"]'

_NAMES_FILES_JS = """
async () => {
  const s = await import('/js/storage.js');
  const files = await s.getOwnAccountsFiles();
  return files.map((f) => new TextDecoder().decode(f.bytes));
}
"""

_OPEN_GROUPS_JS = """
() => [...document.querySelectorAll('#accounts-groups details')]
  .filter((d) => d.open).map((d) => d.dataset.group)
"""


def _open(page: Page, group: str) -> None:
    page.locator(f'#accounts-groups details[data-group="{group}"] > summary').click()


def _assert_no_horizontal_scroll(page: Page) -> None:
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= _MOBILE_VIEWPORT["width"], width


def _broker_only_dir(tmp_path: Path) -> Path:
    input_dir = tmp_path / "native"
    input_dir.mkdir()
    (input_dir / BROKER_CSV.name).write_bytes(BROKER_CSV.read_bytes())
    return input_dir


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_accounts_section_might_be_yours_rename_and_files(
    tmp_path: Path,
    shell_server: str,  # noqa: F811 -- pytest fixture param, not a redefinition
    color_scheme: str,
) -> None:
    expected = _expected_legend(_expected_summary_for(_broker_only_dir(tmp_path)))

    with launch_chromium() as browser:
        context, page = _open_shell_page(
            browser, shell_server, viewport=_MOBILE_VIEWPORT, color_scheme=color_scheme
        )
        page.set_default_timeout(_GENEROUS_TIMEOUT_MS)
        try:
            page.wait_for_function("window.__finaChecklistReady === true")
            # Nothing imported yet: the section is open on the way to start.
            assert page.locator("#accounts-section").evaluate("(d) => d.open")
            assert not page.locator("#accounts-empty").is_hidden()

            _pick_files(page, [BROKER_CSV])
            _wait_for_settled(page)

            # A file was loaded and one account might be the owner's: the section and that
            # group open by themselves. The statement account is listed directly, in no group.
            assert page.locator("#accounts-section").evaluate("(d) => d.open")
            assert page.evaluate(_OPEN_GROUPS_JS) == ["maybe"]
            assert _summary_dd_texts(page) == expected
            card = page.locator(_CARD)
            assert card.get_attribute("class") == "account own-account"
            assert card.locator(".account-alias").inner_text() == "FERNANDEZ ORTIZ LUCIA"
            assert card.locator(".badge").inner_text() == "No statement"
            assert card.locator(".iban").inner_text() == "ES00 0000 0000 0000 0000 0202"
            assert card.locator(".own-account-meta").inner_text() == (
                "4 transfers · in 25,000.00 € · out 80.00 € · 2023-03-10 – 2023-10-10"
            )
            note = page.locator('#accounts-groups details[data-group="maybe"] .group-note')
            assert note.inner_text() == (
                "Your statements show transfers to or from these accounts, under the same holder "
                "name (probably you)."
            )
            assert card.locator("button").all_inner_texts() == ["Import its statement"]
            assert _IBAN not in page.locator("#status-section").inner_text()
            _assert_no_horizontal_scroll(page)
            page.screenshot(path=str(tmp_path / f"maybe_{color_scheme}.png"), full_page=True)

            # "Import its statement" opens the same file picker as the Import button.
            with page.expect_file_chooser() as chooser:
                card.locator(".own-account-import").click()
            assert chooser.value.is_multiple()

            # The statement account holds its own file; the names file is not listed.
            assert page.locator(".accounts-list--statements > li").count() == 1
            assert page.locator("#accounts-groups .accounts-description").count() == 0
            broker = page.locator(_BROKER_CARD)
            assert broker.locator(".account-alias").inner_text() == "Trade Republic"
            assert broker.locator(".stored-file-name").all_inner_texts() == [BROKER_CSV.name]
            assert broker.locator(".balance").inner_text() == f"Balance: {expected[0]}"

            # Rename: stored in the names file (version 3, names only), shown after the re-run.
            broker.locator(".acc-rename").click()
            broker.locator(".alias-input").fill("Broker")
            seq = _run_seq(page)
            broker.locator(".alias-input").press("Enter")
            _wait_for_next_run(page, seq)
            assert page.locator("#error-section").is_hidden()
            assert page.locator(f"{_BROKER_CARD} .account-alias").inner_text() == "Broker"
            (stored,) = page.evaluate(_NAMES_FILES_JS)
            assert json.loads(stored) == {
                "format": "fina-own-accounts",
                "version": 3,
                "aliases": {"trade_republic": "Broker"},
            }
            names = page.locator("#accounts-groups .stored-file-name").all_inner_texts()
            assert names == [BROKER_CSV.name]
            assert _summary_dd_texts(page) == expected  # a name changes no figure
            _assert_no_horizontal_scroll(page)
            page.screenshot(path=str(tmp_path / f"named_{color_scheme}.png"), full_page=True)

            # Reload: everything comes back from the cache, collapsed (no file was loaded).
            page.goto(f"{shell_server}/index.html")
            page.wait_for_function("window.__finaChecklistReady === true")
            _wait_for_settled(page)
            assert _run_seq(page) == 0  # shown from the cache, nothing recomputed
            assert not page.locator("#accounts-section").evaluate("(d) => d.open")
            assert page.evaluate(_OPEN_GROUPS_JS) == []
            page.locator("#accounts-section > summary h2").click()
            _open(page, "maybe")
            assert page.locator(_CARD).count() == 1
            assert page.locator(f"{_BROKER_CARD} .account-alias").inner_text() == "Broker"
        finally:
            context.close()


def test_a_result_from_another_engine_is_recomputed_not_shown(
    tmp_path: Path,
    shell_server: str,  # noqa: F811 -- pytest fixture param, not a redefinition
) -> None:
    expected = _expected_legend(_expected_summary_for(_broker_only_dir(tmp_path)))
    with launch_chromium() as browser:
        context, page = _open_shell_page(
            browser, shell_server, viewport=_MOBILE_VIEWPORT, color_scheme="light"
        )
        page.set_default_timeout(_GENEROUS_TIMEOUT_MS)
        try:
            page.wait_for_function("window.__finaChecklistReady === true")
            _pick_files(page, [BROKER_CSV])
            _wait_for_settled(page)
            engine = page.evaluate("async () => (await import('/js/pyodide-bridge.js')).engineId()")
            assert len(engine) == 64
            cache = page.evaluate("async () => (await import('/js/storage.js')).getRunCache()")
            assert cache["engine"] == engine

            # Stamp the cached result as another engine's, with a chart that must never show.
            page.evaluate(
                """async () => {
                  const s = await import('/js/storage.js');
                  const cache = await s.getRunCache();
                  await s.setRunCache({...cache, engine: 'previous-engine',
                                       chartHtml: '<html><body>stale</body></html>'});
                }"""
            )
            page.goto(f"{shell_server}/index.html")
            page.wait_for_function("window.__finaChecklistReady === true")
            _wait_for_next_run(page, 0)
            assert page.locator("#error-section").is_hidden()
            assert _summary_dd_texts(page) == expected
            recomputed = page.evaluate("async () => (await import('/js/storage.js')).getRunCache()")
            assert recomputed["engine"] == engine
            assert "stale" not in recomputed["chartHtml"]
        finally:
            context.close()


def test_names_file_helpers(
    shell_server: str,  # noqa: F811 -- pytest fixture param, not a redefinition
) -> None:
    with launch_chromium() as browser:
        context, page = _open_shell_page(
            browser, shell_server, viewport=_MOBILE_VIEWPORT, color_scheme="light"
        )
        try:
            result = page.evaluate(
                """async () => {
                  const m = await import('/js/own-accounts.js');
                  const enc = (doc) => new TextEncoder().encode(JSON.stringify(doc));
                  const decision = {iban: 'A', holder_name: 'X', owned: true, decided_on: '2026-09-20'};
                  const merged = m.mergeDocuments([
                    {aliases: {A: 'One'}},
                    {aliases: {A: 'Old', C: 'Three'}},
                  ]);
                  const renamed = m.withAlias(m.withAlias(merged.aliases, 'B', ' Two '), 'C', ' ');
                  const bytes = m.serializeDocument({aliases: renamed});
                  return {
                    merged: merged.aliases,
                    written: JSON.parse(new TextDecoder().decode(bytes)),
                    roundTrip: m.parseDocument(bytes),
                    garbage: m.parseDocument(new TextEncoder().encode('not json')),
                    otherFormat: m.parseDocument(enc({format: 'x', aliases: {A: 'One'}})),
                    version1: m.parseDocument(enc(
                      {format: 'fina-own-accounts', version: 1, accounts: [decision]})),
                    version2: m.parseDocument(enc({format: 'fina-own-accounts', version: 2,
                      accounts: [decision], aliases: {A: 'Kept', B: ' ', C: 5}})),
                  };
                }"""
            )
        finally:
            context.close()
    assert result == {
        "merged": {"A": "One", "C": "Three"},
        "written": {
            "format": "fina-own-accounts",
            "version": 3,
            "aliases": {"A": "One", "B": "Two"},
        },
        "roundTrip": {"aliases": {"A": "One", "B": "Two"}},
        "garbage": {"aliases": {}},
        "otherFormat": {"aliases": {}},
        "version1": {"aliases": {}},
        "version2": {"aliases": {"A": "Kept"}},
    }
