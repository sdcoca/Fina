#!/usr/bin/env python3
"""Generates ``web/precache-manifest.json`` and stamps ``web/service-worker.js``'s cache-version
constant (WP-16).

Stdlib-only by design, matching ``web/vendor/build.py``'s own preference for stdlib-first
tooling. This script does exactly two things:

1. Walks the real ``web/`` tree -- never a hand-written literal list that could silently drift
   from what WP-11 (``web/vendor/``) or later shell edits actually ship -- to enumerate every
   file the app needs precached for a fully offline first run (PWA-4.2): ``index.html``,
   ``manifest.json``, everything under ``web/css/*.css``, ``web/js/*.js``, ``web/icons/*.png``,
   ``web/py/bridge.py`` (fetched at runtime by ``web/js/pyodide-bridge.js`` to seed the Pyodide
   interpreter -- easy to forget since it is Python source, not an obvious "web asset", but it
   is a real same-origin ``fetch()`` the app makes and must work offline), and everything under
   ``web/vendor/pyodide/`` + ``web/vendor/wheels/`` (the Pyodide runtime + vendored wheels --
   by far the largest payload, per WP-11). Writes this list, each file's own sha256, and a
   short ``version`` hash derived from all of it plus ``fina.__version__``, to
   ``web/precache-manifest.json``.
2. Stamps that same ``version`` string into ``web/service-worker.js``'s ``CACHE_VERSION``
   constant (PWA-4.5's interface point: "changing the precached file set naturally produces a
   new cache name so old caches get cleaned up on activate"). Because this also changes
   ``service-worker.js``'s own bytes, the browser's standard service-worker update algorithm
   (which detects updates by byte-diffing the registered script itself, not by inspecting
   whatever it fetches) reliably notices a real content change too -- not only the cache-name
   bookkeeping on the activate path.

Deliberately excluded from the precache list, on purpose, not by oversight: ``web/vendor/
build.py`` and ``web/icons/generate.py`` (build-time tooling, never fetched by the running
app), and this script itself / ``web/service-worker.js`` / ``web/precache-manifest.json``
(the service worker's own script is managed by the browser's own SW update mechanism, not by
this app-level cache list; the manifest is a build output, not an input to itself).

Usage::

    python3 web/build_precache_manifest.py

Run this whenever any precached file changes -- a new WP-11 vendor bundle, an edited shell
file, a bumped ``fina.__version__`` -- and commit the regenerated ``precache-manifest.json``
alongside the change, exactly like ``web/vendor/CHECKSUMS.txt``'s own convention.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = Path(__file__).resolve().parent
FINA_INIT_PATH = REPO_ROOT / "src" / "fina" / "__init__.py"
SERVICE_WORKER_PATH = WEB_DIR / "service-worker.js"
MANIFEST_PATH = WEB_DIR / "precache-manifest.json"

_FINA_VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')
_CACHE_VERSION_RE = re.compile(r'(const CACHE_VERSION = ")[^"]*(";)')

# A sanity floor, not a magic exact count: catches a badly misconfigured WEB_DIR (e.g. run from
# the wrong cwd) producing a near-empty manifest, without hard-coding the real count so this
# script does not need editing every time a file is added.
_MIN_EXPECTED_FILES = 10


def _fina_version() -> str:
    match = _FINA_VERSION_RE.search(FINA_INIT_PATH.read_text(encoding="utf-8"))
    if not match:
        raise RuntimeError(f"could not find __version__ in {FINA_INIT_PATH}")
    return match.group(1)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collect_precache_files() -> list[Path]:
    """Every file this WP requires precached, from a real directory listing (see this module's
    own docstring for the exact rationale and exclusions)."""
    files: list[Path] = []

    for name in ("index.html", "manifest.json"):
        path = WEB_DIR / name
        if not path.is_file():
            raise RuntimeError(f"expected {path} to exist")
        files.append(path)

    files.extend(sorted((WEB_DIR / "css").glob("*.css")))
    files.extend(sorted((WEB_DIR / "js").glob("*.js")))
    files.extend(sorted((WEB_DIR / "icons").glob("*.png")))

    bridge_py = WEB_DIR / "py" / "bridge.py"
    if not bridge_py.is_file():
        raise RuntimeError(f"expected {bridge_py} to exist")
    files.append(bridge_py)

    for sub in ("pyodide", "wheels"):
        root = WEB_DIR / "vendor" / sub
        if not root.is_dir():
            raise RuntimeError(f"expected {root} to exist -- run web/vendor/build.py first")
        vendored = sorted(p for p in root.rglob("*") if p.is_file())
        if not vendored:
            raise RuntimeError(f"{root} is empty -- run web/vendor/build.py first")
        files.extend(vendored)

    if len(files) < _MIN_EXPECTED_FILES:
        raise RuntimeError(
            f"suspiciously few precache files found ({len(files)}) -- check WEB_DIR ({WEB_DIR})"
        )
    return files


def _relpath(path: Path) -> str:
    return path.relative_to(WEB_DIR).as_posix()


def build_manifest() -> dict[str, object]:
    files = _collect_precache_files()
    entries = sorted((_relpath(p), _sha256_file(p)) for p in files)
    fina_version = _fina_version()

    # The version hash covers every precached file's own content plus fina's own version --
    # PWA-4.5: any change to either naturally produces a different version, and therefore a
    # different cache name, so activate's cleanup step actually has an old cache to delete
    # rather than silently reusing (and never refreshing) the same one forever.
    version_input = fina_version + "\n" + "\n".join(f"{rel}:{digest}" for rel, digest in entries)
    version = hashlib.sha256(version_input.encode("utf-8")).hexdigest()[:16]

    return {
        "version": version,
        "finaVersion": fina_version,
        "files": [rel for rel, _digest in entries],
    }


def _stamp_service_worker(version: str) -> None:
    text = SERVICE_WORKER_PATH.read_text(encoding="utf-8")
    new_text, count = _CACHE_VERSION_RE.subn(rf"\g<1>{version}\g<2>", text)
    if count != 1:
        raise RuntimeError(
            f"expected exactly one CACHE_VERSION constant in {SERVICE_WORKER_PATH}, "
            f"found {count} -- has its format changed?"
        )
    if new_text != text:
        SERVICE_WORKER_PATH.write_text(new_text, encoding="utf-8")


def main() -> None:
    manifest = build_manifest()
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    version = str(manifest["version"])
    _stamp_service_worker(version)
    file_count = len(manifest["files"])  # type: ignore[arg-type]
    print(f"wrote {MANIFEST_PATH} ({file_count} files, version {version})")
    print(f"stamped {SERVICE_WORKER_PATH} CACHE_VERSION={version}")


if __name__ == "__main__":
    main()
