"""web/icons/generate.py -- WP-14 placeholder PWA icon generator.

Draws a simple ascending-line mark in `--line-real` blue (`#2a78d6`, the *light*-palette value
from `src/fina/render/section1_chart.py`'s own `<style>` block -- a manifest icon is not
theme-aware, so one fixed value is used, the same one `manifest.json` uses for its static
`theme_color`) on the `--page` background (`#f6f5f1`, that module's light-palette ground token).
The mark itself -- a rising line ending in a filled dot -- deliberately echoes the chart's own
`.line-real` / `.dot-real` visual grammar (the "real net worth" series, the number this whole app
exists to get right) rather than inventing an unrelated brand shape.

Run directly: `python3 web/icons/generate.py`. Writes straight into this directory. Standalone
asset-generation script -- imports nothing from `src/fina` and is not part of the gated `mypy`
`files` list, run ad hoc against this file instead (see WP-14's own verification report).

Outputs (per docs/plan/mobile-pwa-shell.md PWA-1.3):
  icon-192.png             192x192, manifest purpose "any"
  icon-512.png             512x512, manifest purpose "any"
  icon-512-maskable.png    512x512, manifest purpose "maskable" -- mark kept inside the center
                           ~80% safe zone (Android adaptive-icon masking guidance); the
                           background fills the full canvas so whatever shape the launcher
                           crops the icon to, the cropped ring is never transparent.
  apple-touch-icon.png     180x180, fully opaque (RGB, no alpha channel) -- iOS composites this
                           onto an opaque background and does not honor transparency.

All four are drawn on a fully opaque `--page` background (RGB, no alpha channel at all) -- there
is no case here where transparency would help and the maskable/apple-touch icons explicitly
require its absence, so every output uses the same opaque convention for consistency.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

PAGE_COLOR = "#f6f5f1"  # section1_chart.py light-palette --page token
LINE_COLOR = "#2a78d6"  # section1_chart.py light-palette --line-real token

ICONS_DIR = Path(__file__).resolve().parent

# Supersampling factor: the mark is drawn at SS times the target size, then downsampled with a
# high-quality filter, so curves/circles anti-alias cleanly instead of showing jagged pixel
# edges -- Pillow's ImageDraw itself does not anti-alias.
_SUPERSAMPLE = 4

# Mark geometry: an ascending 4-point line in a 0..1 unit square, matching the chart's own rising
# "real net worth" line.
_MARK_POINTS: list[tuple[float, float]] = [
    (0.10, 0.72),
    (0.36, 0.48),
    (0.58, 0.62),
    (0.90, 0.14),
]


def _draw_mark(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float]) -> None:
    """Draws the ascending-line mark scaled into `box` (left, top, right, bottom in pixels)."""
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    points = [(left + px * width, top + py * height) for px, py in _MARK_POINTS]
    stroke_width = max(2, round(width * 0.085))
    draw.line(points, fill=LINE_COLOR, width=stroke_width, joint="curve")
    # draw.line's own joints/caps are square/mitred; capping each vertex with a filled circle
    # of the same width rounds them so the mark reads cleanly at small sizes.
    joint_radius = stroke_width / 2
    for x, y in points:
        draw.ellipse(
            (x - joint_radius, y - joint_radius, x + joint_radius, y + joint_radius),
            fill=LINE_COLOR,
        )
    # A slightly larger dot at the mark's end point, echoing the chart's own end-of-line marker
    # (`.dot-real`).
    end_x, end_y = points[-1]
    end_radius = stroke_width * 1.35
    draw.ellipse(
        (end_x - end_radius, end_y - end_radius, end_x + end_radius, end_y + end_radius),
        fill=LINE_COLOR,
    )


def _make_icon(size: int, *, safe_zone_fraction: float) -> Image.Image:
    """Renders one square icon at `size` px, with the mark confined to the center
    `safe_zone_fraction` of the canvas, supersampled and downsampled for clean anti-aliasing.
    """
    big = size * _SUPERSAMPLE
    img = Image.new("RGB", (big, big), PAGE_COLOR)
    draw = ImageDraw.Draw(img)
    margin = big * (1 - safe_zone_fraction) / 2
    _draw_mark(draw, (margin, margin, big - margin, big - margin))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def generate() -> None:
    # "any" purpose: the OS applies no mask, so a smaller inset than the maskable safe zone is
    # fine -- kept well clear of the edge without reading tiny.
    _ANY_SAFE_ZONE = 0.70
    # "maskable": Android's adaptive-icon guidance -- artwork must stay inside the center ~80%,
    # since the launcher may crop the outer ring to a circle/squircle/rounded-square.
    _MASKABLE_SAFE_ZONE = 0.80

    _make_icon(192, safe_zone_fraction=_ANY_SAFE_ZONE).save(ICONS_DIR / "icon-192.png")
    _make_icon(512, safe_zone_fraction=_ANY_SAFE_ZONE).save(ICONS_DIR / "icon-512.png")
    _make_icon(512, safe_zone_fraction=_MASKABLE_SAFE_ZONE).save(
        ICONS_DIR / "icon-512-maskable.png"
    )
    _make_icon(180, safe_zone_fraction=_ANY_SAFE_ZONE).save(ICONS_DIR / "apple-touch-icon.png")


if __name__ == "__main__":
    generate()
    print(f"Wrote 4 PNG icons under {ICONS_DIR}")
