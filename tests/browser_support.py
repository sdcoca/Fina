"""Shared helper for real-browser visual verification tests (R-10.2a: verified by rendering,
not by reading the CSS).

Playwright's own browser auto-download is unavailable in this environment (the CDN it uses is
blocked by network policy); a chromium build is pre-installed at a fixed path instead. This
module isolates that one environment-specific detail so the actual test files stay portable.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, sync_playwright

_CHROMIUM_PATH = "/opt/pw-browsers/chromium"


@contextlib.contextmanager
def launch_chromium() -> Iterator[Browser]:
    """A headless Chromium instance, or a skipped test if none is available here.

    Only failures from *launching* the browser are turned into a skip -- an assertion or any
    other error from the caller's own test body, using the already-launched browser, must
    propagate as a genuine test failure, never be silently reclassified as "browser missing".
    """
    try:
        playwright_ctx = sync_playwright().start()
    except Exception as exc:  # pragma: no cover - environment-dependent, not exercised in CI
        pytest.skip(f"no usable Playwright driver in this environment: {exc}")
        return
    try:
        try:
            browser = playwright_ctx.chromium.launch(executable_path=_CHROMIUM_PATH)
        except Exception as exc:  # pragma: no cover - environment-dependent
            pytest.skip(f"no usable headless Chromium in this environment: {exc}")
            return
        try:
            yield browser
        finally:
            browser.close()
    finally:
        playwright_ctx.stop()
