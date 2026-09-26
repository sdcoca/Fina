"""Real-browser verification for WP-19b: the "Accounts in your name" section (R-3.8/R-3.9).

Same house pattern as `tests/test_pwa_storage.py`: the real `web/` tree over a local server,
headless Chromium at a real phone width (CLAUDE.md rule 19), every off-origin request blocked.

1. `test_deciding_an_account_rewrites_the_confirmation_file_and_recomputes` -- one narrative:
   the broker fixture alone lists its one pending account (not as a warning); "Mine" writes the
   confirmation file, re-runs, and shows the estimate and the missing-data note with the same
   figures the native CLI computes over the same files; a reload shows it from the cache;
   "Change" -> "Not mine" rewrites the same single file. No horizontal scroll at 390px, in
   light and dark themes.
2. `test_confirmation_file_merge_keeps_the_latest_decision_per_account` -- `own-accounts.js`'s
   pure helpers, as a backup restore uses them.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page

from browser_support import launch_chromium
from fina.money import round_half_up
from fina.pipeline import run_pipeline
from test_pwa_shell import (
    _MOBILE_VIEWPORT,
    BROKER_CSV,
    _open_shell_page,
    _pick_files,
    _summary_dd_texts,
    _wait_for_settled,
    shell_server,  # noqa: F401 -- imported for its side effect as a pytest fixture
)
from test_pwa_storage import _run_seq, _wait_for_next_run

_GENEROUS_TIMEOUT_MS = 180_000
_IBAN = "ES0000000000000000000202"
_CARD = f'#own-accounts-list li[data-iban="{_IBAN}"]'

_OWN_ACCOUNTS_FILES_JS = """
async () => {
  const s = await import('/js/storage.js');
  const files = await s.getOwnAccountsFiles();
  return files.map((f) => new TextDecoder().decode(f.bytes));
}
"""


def _native_with_decision(tmp_path: Path, owned: bool) -> dict[str, Any]:
    input_dir = tmp_path / f"native_{owned}"
    input_dir.mkdir()
    shutil.copyfile(BROKER_CSV, input_dir / BROKER_CSV.name)
    doc = {
        "format": "fina-own-accounts",
        "version": 1,
        "accounts": [
            {
                "iban": _IBAN,
                "holder_name": "FERNANDEZ ORTIZ LUCIA",
                "owned": owned,
                "decided_on": "2026-09-26",
            }
        ],
    }
    (input_dir / "cuentas-propias.json").write_text(json.dumps(doc), encoding="utf-8")
    latest = run_pipeline(input_dir).series[-1]
    return {
        "real_net_worth": str(round_half_up(latest.real_net_worth)),
        "estimated": str(round_half_up(latest.estimated_net_worth)),
        "savings_only": str(round_half_up(latest.savings_only)),
        "gap": str(round_half_up(latest.gap)),
    }


def _assert_no_horizontal_scroll(page: Page) -> None:
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= _MOBILE_VIEWPORT["width"], width


def _click_and_wait(page: Page, selector: str) -> None:
    seq = _run_seq(page)
    page.locator(selector).click()
    _wait_for_next_run(page, seq)
    assert page.locator("#error-section").is_hidden()


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_deciding_an_account_rewrites_the_confirmation_file_and_recomputes(
    tmp_path: Path,
    shell_server: str,  # noqa: F811 -- pytest fixture param, not a redefinition
    color_scheme: str,
) -> None:
    owned = _native_with_decision(tmp_path, owned=True)
    not_owned = _native_with_decision(tmp_path, owned=False)
    assert owned["gap"] == not_owned["gap"]  # the core promise, natively

    with launch_chromium() as browser:
        context, page = _open_shell_page(
            browser, shell_server, viewport=_MOBILE_VIEWPORT, color_scheme=color_scheme
        )
        page.set_default_timeout(_GENEROUS_TIMEOUT_MS)
        try:
            page.wait_for_function("window.__finaChecklistReady === true")
            _pick_files(page, [BROKER_CSV])
            _wait_for_settled(page)

            # Pending: listed in its own section, not among the warnings.
            card = page.locator(_CARD)
            assert card.get_attribute("class") == "own-account own-account--pending"
            assert card.locator(".own-account-iban").inner_text() == (
                "ES00 0000 0000 0000 0000 0202"
            )
            assert card.locator(".own-account-meta").inner_text() == (
                "4 transfers · in 25000.00 EUR · out 80.00 EUR · 2023-03-10 – 2023-10-10"
            )
            assert _IBAN not in page.locator("#status-section").inner_text()
            _assert_no_horizontal_scroll(page)
            page.screenshot(path=str(tmp_path / f"pending_{color_scheme}.png"), full_page=True)

            # "Mine": one confirmation file written, figures as the native run computes them.
            _click_and_wait(page, f"{_CARD} .own-account-button--mine")
            card = page.locator(_CARD)
            assert card.locator(".own-account-status").inner_text() == "Marked as yours"
            assert (
                card.locator(".own-account-balance")
                .inner_text()
                .startswith("Estimated balance: 80.00 EUR")
            )
            assert (
                card.locator(".own-account-missing")
                .inner_text()
                .startswith("Missing data: at least 25000.00 EUR")
            )
            assert _summary_dd_texts(page)[2:] == [
                f"{owned['real_net_worth']} EUR",
                f"{owned['estimated']} EUR",
                f"{owned['savings_only']} EUR",
                f"{owned['gap']} EUR",
            ]
            (stored,) = page.evaluate(_OWN_ACCOUNTS_FILES_JS)
            (decision,) = json.loads(stored)["accounts"]
            assert (decision["iban"], decision["owned"]) == (_IBAN, True)
            assert decision["holder_name"] == "FERNANDEZ ORTIZ LUCIA"
            names = page.locator("#stored-files-list .stored-file-name").all_inner_texts()
            assert sorted(names) == sorted([BROKER_CSV.name, "cuentas-propias.json"])
            _assert_no_horizontal_scroll(page)
            page.screenshot(path=str(tmp_path / f"owned_{color_scheme}.png"), full_page=True)

            # Reload: the decision and its figures come back from the cache.
            page.goto(f"{shell_server}/index.html")
            page.wait_for_function("window.__finaChecklistReady === true")
            _wait_for_settled(page)
            assert page.locator(f"{_CARD} .own-account-status").inner_text() == ("Marked as yours")

            # "Change" -> "Not mine": the same single file, rewritten.
            page.locator(f"{_CARD} .own-account-button--change").click()
            _click_and_wait(page, f"{_CARD} .own-account-button--not-mine")
            card = page.locator(_CARD)
            assert card.locator(".own-account-status").inner_text() == "Marked as not yours"
            assert card.locator(".own-account-missing").count() == 0
            assert _summary_dd_texts(page)[2:] == [
                f"{not_owned['real_net_worth']} EUR",
                f"{not_owned['savings_only']} EUR",
                f"{not_owned['gap']} EUR",
            ]
            (stored,) = page.evaluate(_OWN_ACCOUNTS_FILES_JS)
            assert json.loads(stored)["accounts"][0]["owned"] is False
        finally:
            context.close()


def test_confirmation_file_merge_keeps_the_latest_decision_per_account(
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
                  const a = (iban, owned, day) =>
                    ({iban, holder_name: 'X', owned, decided_on: `2026-09-${day}`});
                  const merged = m.mergeDecisions([
                    [a('A', true, '20'), a('B', true, '25')],
                    [a('B', false, '21'), a('A', false, '22'), a('C', true, '20')],
                  ]);
                  const updated = m.withDecision(merged, a('A', true, '26'));
                  const bytes = m.serializeDecisions(updated);
                  return {
                    merged: merged.map((d) => [d.iban, d.owned]),
                    updated: updated.map((d) => [d.iban, d.owned, d.decided_on]),
                    roundTrip: m.parseDecisions(bytes).length,
                    garbage: m.parseDecisions(new TextEncoder().encode('not json')).length,
                    today: m.todayIso(new Date(2026, 0, 5)),
                  };
                }"""
            )
        finally:
            context.close()
    assert result == {
        "merged": [["A", False], ["B", True], ["C", True]],
        "updated": [
            ["A", True, "2026-09-26"],
            ["B", True, "2026-09-25"],
            ["C", True, "2026-09-20"],
        ],
        "roundTrip": 3,
        "garbage": 0,
        "today": "2026-01-05",
    }
