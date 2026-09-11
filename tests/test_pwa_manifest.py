"""Manifest + icon verification (WP-14).

This is the automated "gate that shouldn't silently regress" for `web/manifest.json` and the
icon set it references, matching this project's house style (`tests/test_pwa_shell.py`'s
`backdrop-filter` grep is the model). It is NOT a claim that a real Chrome install prompt was
observed -- Chrome's actual `beforeinstallprompt` firing requires a registered service worker
with a `fetch` handler, which is WP-16's job and does not exist yet. What this file checks is
everything that genuinely does not depend on that:

1. `manifest.json` is valid JSON and every field the W3C manifest spec / Chrome's Android
   installability criteria require is present, correctly typed, and (where the plan pins a
   literal value) matches `src/fina/render/section1_chart.py`'s own light-palette tokens
   verbatim -- never a value re-typed by hand and left to drift.
2. Every icon file the manifest references actually exists and is genuinely the pixel size the
   manifest claims (opened with Pillow, not trusted from the filename).
3. The maskable icon's artwork genuinely sits inside the center ~80% safe zone: a concrete
   geometric check (corner pixels sampled and asserted to be the background color, not the mark
   color), not "drawn in the middle, trust me".
4. `apple-touch-icon.png` is fully opaque (no alpha channel) at 180x180.
5. A real headless-Chromium render of the real `web/index.html`, at a 390px mobile viewport
   first (CLAUDE.md rule 19) in both `prefers-color-scheme` values, then desktop, proves the new
   `<link rel="manifest">` / `<link rel="apple-touch-icon">` tags and the install-button markup
   did not break the page: it still loads, the theme-color meta tags still track the live
   color scheme, and a screenshot is saved for a human to actually look at.
"""

from __future__ import annotations

import http.server
import json
import re
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from playwright.sync_api import Browser, BrowserContext, Page, Route

from browser_support import launch_chromium

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"
MANIFEST_PATH = WEB_DIR / "manifest.json"
ICONS_DIR = WEB_DIR / "icons"
SECTION1_CHART_PATH = REPO_ROOT / "src" / "fina" / "render" / "section1_chart.py"

_INSTALLABLE_DISPLAY_VALUES = {"standalone", "fullscreen", "minimal-ui"}
_CSS_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_MOBILE_VIEWPORT = {"width": 390, "height": 844}
_DESKTOP_VIEWPORT = {"width": 1280, "height": 800}


def _light_palette_token(token: str) -> str:
    """Extracts a light-palette CSS custom property's literal hex value directly from
    `section1_chart.py`'s own <style> block -- the single source of truth this WP's manifest and
    icons are required to reuse verbatim (docs/plan/mobile-pwa-shell.md PWA-1.2a / PWA-1.3.3).
    The *first* match in the file is always the light (`:root`) definition; the dark-mode
    redefinition lives further down, after `@media (prefers-color-scheme: dark)`.
    """
    text = SECTION1_CHART_PATH.read_text(encoding="utf-8")
    match = re.search(rf"--{re.escape(token)}:\s*(#[0-9a-fA-F]{{6}});", text)
    assert match, f"could not find --{token} in {SECTION1_CHART_PATH}"
    return match.group(1)


LIGHT_PAGE = _light_palette_token("page")
LIGHT_LINE_REAL = _light_palette_token("line-real")


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    result: dict[str, Any] = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return result


# ---------------------------------------------------------------------------
# 1. JSON validity + required-field / installability-criteria checks.
# ---------------------------------------------------------------------------


def test_manifest_is_valid_json() -> None:
    json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))  # raises ValueError if malformed


def test_manifest_has_name_and_short_name(manifest: dict[str, Any]) -> None:
    # Chrome/W3C only require one of the two; this project sets both (PWA-1.1.1/1.1.2).
    assert manifest.get("name") or manifest.get("short_name")
    assert manifest["name"] == "Fina"
    assert manifest["short_name"] == "Fina"


def test_manifest_start_url_matches_wp13_convention(manifest: dict[str, Any]) -> None:
    # WP-13's own browser tests (tests/test_pwa_shell.py) always navigate to the explicit
    # "index.html" path, never bare "/". Deliberately relative, not root-relative ("/index.html"):
    # the Web App Manifest spec resolves a relative start_url against the manifest's own URL, so
    # this works whether the app is served from an origin's root or, as a GitHub Pages *project*
    # site actually does, from a subpath ("/<repo-name>/") -- a root-relative value would point
    # at the server's true root instead and 404 under that real deployment target (found on a
    # real device; see tests/test_pwa_subpath_deployment.py, the regression test for this).
    assert manifest["start_url"] == "index.html"
    assert (WEB_DIR / "index.html").is_file()


def test_manifest_scope_is_whole_origin(manifest: dict[str, Any]) -> None:
    # "." (not "/"), for the same subpath-safety reason as start_url above: a manifest-relative
    # scope covers "wherever this app's own files live", which is the origin's root only when
    # that happens to be where the app is deployed.
    assert manifest["scope"] == "."


def test_manifest_display_is_an_installable_value(manifest: dict[str, Any]) -> None:
    assert manifest["display"] in _INSTALLABLE_DISPLAY_VALUES
    # PWA-1.1.5's specific choice: standalone, not fullscreen (keeps the OS status bar visible).
    assert manifest["display"] == "standalone"


def test_manifest_colors_are_valid_css_hex_and_match_chart_light_palette(
    manifest: dict[str, Any],
) -> None:
    # PWA-1.2a: both fields are single static values (the manifest spec has no dark-mode variant
    # for them), set from the *light* palette -- the value shown during the cold-start splash,
    # before any page CSS has painted -- and reused literally from section1_chart.py's own
    # --page token rather than re-typed by hand.
    for field in ("background_color", "theme_color"):
        value = manifest[field]
        assert _CSS_HEX_COLOR_RE.match(value), f"{field}={value!r} is not a #rrggbb color"
        assert value == LIGHT_PAGE == "#f6f5f1"


def test_manifest_icons_cover_required_sizes_and_purposes(manifest: dict[str, Any]) -> None:
    icons = manifest["icons"]
    assert isinstance(icons, list) and icons, "manifest must declare a non-empty icons array"

    def _square_side(sizes: str) -> int:
        width_str, _, height_str = sizes.partition("x")
        assert width_str == height_str, f"non-square icon 'sizes' entry: {sizes!r}"
        return int(width_str)

    any_sides = {
        _square_side(icon["sizes"]) for icon in icons if icon.get("purpose", "any") == "any"
    }
    maskable_sides = {
        _square_side(icon["sizes"]) for icon in icons if icon.get("purpose") == "maskable"
    }

    # Chrome's Android installability criteria: an "any"-purpose icon >=192px AND one >=512px.
    assert any(side >= 192 for side in any_sides), any_sides
    assert any(side >= 512 for side in any_sides), any_sides
    assert 512 in maskable_sides, maskable_sides

    for icon in icons:
        assert icon["type"] == "image/png"
        # Relative, not root-relative: a manifest-relative icon src resolves against the
        # manifest's own URL regardless of what subpath the app is deployed under (see
        # test_manifest_scope_is_whole_origin's own comment for why root-relative paths broke a
        # real deployment).
        assert not icon["src"].startswith("/"), f"icon src must be a relative path: {icon['src']}"
        icon_path = WEB_DIR / icon["src"]
        assert icon_path.is_file(), f"manifest references a missing icon file: {icon['src']}"


# ---------------------------------------------------------------------------
# 2. Icons actually load, at the exact pixel sizes the manifest claims.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected_size"),
    [
        ("icon-192.png", (192, 192)),
        ("icon-512.png", (512, 512)),
        ("icon-512-maskable.png", (512, 512)),
        ("apple-touch-icon.png", (180, 180)),
    ],
)
def test_icon_file_pixel_dimensions(filename: str, expected_size: tuple[int, int]) -> None:
    with Image.open(ICONS_DIR / filename) as img:
        assert img.size == expected_size


def test_manifest_icon_sizes_field_matches_the_real_png_dimensions(
    manifest: dict[str, Any],
) -> None:
    """The 'sizes' string in manifest.json is easy to hand-edit out of sync with the actual PNG
    -- open every referenced file for real and cross-check, rather than trusting the string."""
    for icon in manifest["icons"]:
        icon_path = WEB_DIR / icon["src"].lstrip("/")
        with Image.open(icon_path) as img:
            actual = f"{img.width}x{img.height}"
        assert actual == icon["sizes"], (
            f"{icon['src']}: manifest claims {icon['sizes']}, file is actually {actual}"
        )


def test_apple_touch_icon_is_fully_opaque_no_alpha_channel() -> None:
    # iOS composites this onto an opaque background and does not honor transparency; storing it
    # with an alpha channel at all would be silently wrong on-device even if it looks fine here.
    with Image.open(ICONS_DIR / "apple-touch-icon.png") as img:
        assert img.mode in ("RGB", "L"), f"apple-touch-icon.png has an alpha channel: {img.mode}"


def test_any_purpose_icons_are_drawn_in_line_real_blue_on_page_background() -> None:
    """A concrete pixel check that the 'any'-purpose icons actually used the two required
    palette tokens (rather than trusting the generator script's own claims): the exact corner
    pixel must be the --page background, and at least one pixel along the mark's drawn path must
    be the exact --line-real accent.
    """
    page_rgb = tuple(int(LIGHT_PAGE[i : i + 2], 16) for i in (1, 3, 5))
    line_rgb = tuple(int(LIGHT_LINE_REAL[i : i + 2], 16) for i in (1, 3, 5))
    for filename in ("icon-192.png", "icon-512.png"):
        with Image.open(ICONS_DIR / filename) as img:
            rgb = img.convert("RGB")
            assert rgb.getpixel((2, 2)) == page_rgb, filename
            width, height = rgb.size
            # Sample along the mark's own diagonal path (drawn from ~10%,72% to ~90%,14% of the
            # canvas, per web/icons/generate.py) -- at least one sampled point must be the exact
            # accent color, proving the mark itself was actually drawn in --line-real, not left
            # as a background-only square.
            samples = [rgb.getpixel((int(width * t), int(height * (0.72 - 0.58 * t)))) for t in
                       (0.15, 0.3, 0.45, 0.6, 0.75)]
            assert line_rgb in samples, (filename, samples)


def test_maskable_icon_corners_are_background_not_mark_within_safe_zone() -> None:
    """Geometric proof the maskable icon's artwork sits inside the center ~80% safe zone: every
    sampled corner region (well outside that safe zone) must be exactly the --page background
    color -- if the mark ever bled into the outer ring, at least one of these would pick up the
    accent color instead.
    """
    page_rgb = tuple(int(LIGHT_PAGE[i : i + 2], 16) for i in (1, 3, 5))
    with Image.open(ICONS_DIR / "icon-512-maskable.png") as img:
        rgb = img.convert("RGB")
        size = rgb.size[0]
        assert size == 512
        margin = 8  # well inside the ~10% (51px) masked-out ring, well outside the safe zone
        corners = [
            (margin, margin),
            (size - 1 - margin, margin),
            (margin, size - 1 - margin),
            (size - 1 - margin, size - 1 - margin),
        ]
        for point in corners:
            assert rgb.getpixel(point) == page_rgb, (point, rgb.getpixel(point))


# ---------------------------------------------------------------------------
# 3. Real render check: the shell itself still loads clean with the new tags/markup.
# ---------------------------------------------------------------------------


class _ManifestRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the real `web/` tree at its real root, matching tests/test_pwa_shell.py's own
    server harness so absolute paths (`/manifest.json`, `/icons/...`) resolve exactly as they
    would on a real static host."""

    def __init__(self, *args: object, serve_root: Path, **kwargs: object) -> None:
        self._serve_root = serve_root
        super().__init__(*args, directory=str(serve_root), **kwargs)  # type: ignore[arg-type]

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        pass


@pytest.fixture
def manifest_server() -> Iterator[str]:
    assert (WEB_DIR / "index.html").is_file()
    assert (WEB_DIR / "manifest.json").is_file()
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *a, **kw: _ManifestRequestHandler(*a, serve_root=WEB_DIR, **kw),
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


def _open_page(
    browser: Browser, origin: str, *, viewport: dict[str, int], color_scheme: str
) -> tuple[BrowserContext, Page]:
    context = browser.new_context(viewport=viewport)
    context.route("**/*", _block_everything_off_origin(origin))
    page = context.new_page()
    page.emulate_media(color_scheme=color_scheme)
    page.goto(f"{origin}/index.html")
    page.wait_for_function("window.__finaAppJsLoaded === true", timeout=30_000)
    page.wait_for_function("window.__finaInstallJsLoaded === true", timeout=30_000)
    return context, page


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_index_html_still_renders_clean_at_390px_with_manifest_and_icon_tags(
    tmp_path: Path, manifest_server: str, theme: str
) -> None:
    with launch_chromium() as browser:
        context, page = _open_page(
            browser, manifest_server, viewport=_MOBILE_VIEWPORT, color_scheme=theme
        )
        try:
            # <link rel="manifest"> is present, points at a real, fetchable, same-origin file.
            # Deliberately relative ("./manifest.json"), not root-relative -- see
            # test_pwa_subpath_deployment.py for why a root-relative href 404s under a real
            # (subpath) deployment. Read the resolved `.href` DOM property (an absolute URL the
            # browser itself computed), not the raw attribute string, so this assertion exercises
            # the same resolution a real browser depends on rather than string-matching source.
            manifest_href = page.locator('link[rel="manifest"]').get_attribute("href")
            assert manifest_href == "./manifest.json"
            manifest_resolved = page.eval_on_selector('link[rel="manifest"]', "el => el.href")
            assert manifest_resolved == f"{manifest_server}/manifest.json"
            manifest_response = page.request.get(manifest_resolved)
            assert manifest_response.ok
            assert manifest_response.json()["name"] == "Fina"

            # apple-touch-icon <link>, same-origin fetchable.
            apple_icon_href = page.locator('link[rel="apple-touch-icon"]').get_attribute("href")
            assert apple_icon_href == "./icons/apple-touch-icon.png"
            apple_icon_resolved = page.eval_on_selector(
                'link[rel="apple-touch-icon"]', "el => el.href"
            )
            assert apple_icon_resolved == f"{manifest_server}/icons/apple-touch-icon.png"
            apple_icon_response = page.request.get(apple_icon_resolved)
            assert apple_icon_response.ok

            # Theme-color meta tags still present and correctly tracking the live color scheme
            # (PWA-1.2b) -- the browser resolves exactly one of the two `media` queries active.
            active_theme_color = page.eval_on_selector_all(
                'meta[name="theme-color"]',
                "els => els.filter(e => matchMedia(e.media).matches).map(e => e.content)",
            )
            expected = "#f6f5f1" if theme == "light" else "#0d0d0d"
            assert active_theme_color == [expected], active_theme_color

            # The rest of the WP-13 shell markup is still intact -- a malformed manifest link or
            # broken icon path did not silently break page load.
            assert page.locator("#file-input").count() == 1
            assert page.locator("#install-button").count() == 1
            assert page.locator("#install-button").is_hidden(), (
                "install button must stay hidden -- no beforeinstallprompt fires in this build"
            )

            screenshot_path = tmp_path / f"pwa_manifest_390_{theme}.png"
            page.screenshot(path=str(screenshot_path))
            assert screenshot_path.stat().st_size > 0
        finally:
            context.close()


def test_index_html_still_renders_clean_at_desktop_after_mobile(
    tmp_path: Path, manifest_server: str
) -> None:
    """Additional, run only after the 390px cases above -- mirrors test_pwa_shell.py's own
    desktop-after-mobile convention (rule 19)."""
    with launch_chromium() as browser:
        context, page = _open_page(
            browser, manifest_server, viewport=_DESKTOP_VIEWPORT, color_scheme="light"
        )
        try:
            assert page.locator('link[rel="manifest"]').count() == 1
            assert page.locator("#install-button").is_hidden()
            screenshot_path = tmp_path / "pwa_manifest_desktop_light.png"
            page.screenshot(path=str(screenshot_path))
            assert screenshot_path.stat().st_size > 0
        finally:
            context.close()
