#!/usr/bin/env python3
"""Vendor build script for Fina's Pyodide-based PWA (WP-11).

Stdlib-only by design (no extra build dependency beyond ``build``, already a `dev` extra in
``pyproject.toml``), matching this repo's general preference for stdlib-first tooling.

What this script does, and nothing else:

1. Downloads the pinned **minimal** Pyodide "core" distribution (never the ~200 MB "full"
   bundle that bakes in numpy/pandas/scipy/etc.) and extracts it into ``web/vendor/pyodide/``.
2. Downloads the ``openpyxl`` and ``et_xmlfile`` wheels (pure-Python, matching this project's
   own ``pyproject.toml`` constraint ``openpyxl>=3.1,<4``) into ``web/vendor/wheels/``.
3. Downloads the ``micropip`` wheel at the exact version this Pyodide release's own
   ``pyodide-lock.json`` names, so the vendored runtime and the vendored installer agree on
   what "this Pyodide release's micropip" means (see NOTE below).
4. Builds ``fina``'s own wheel via ``python -m build`` (using the *current* checkout, not a
   PyPI copy — there is no published ``fina`` package) and copies it into
   ``web/vendor/wheels/``.
5. Writes ``web/vendor/CHECKSUMS.txt`` recording every vendored artifact's filename, version,
   and sha256, so a re-run (or a reviewer) can verify nothing silently drifted.

Every remote download is checked against a *pinned* sha256 recorded in this file (not merely
recorded after the fact) — a mismatch aborts the build loudly rather than vendoring a byte
Fina never asked for.

NOTE on ``micropip``'s sha256: Pyodide's own build re-packages pure-Python "packages" (new
zip timestamps/metadata) before shipping them, so the sha256 Pyodide's ``pyodide-lock.json``
records for ``micropip`` differs from the sha256 of the plain wheel PyPI publishes for the
same *version*. This script vendors the plain PyPI wheel (same code, same version number the
lock file names) and the smoke test (``tests/test_pyodide_vendor.py``) loads it with
``checkIntegrity: false`` for that reason — it is never presented to Pyodide as "the" official
lock-listed micropip package, only as an ordinary local wheel to bootstrap ``micropip.install``
for the actually-relevant packages (``openpyxl``, ``et_xmlfile``, ``fina``).

Usage::

    python3 web/vendor/build.py

Network access is required (PyPI + GitHub release assets over this environment's proxy). No
network access is required afterward -- that is the entire point of vendoring.
"""

from __future__ import annotations

import hashlib
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------------------
# Pins. Every version + sha256 below was resolved against the live PyPI/GitHub APIs on
# 2026-09-09 and is intentionally hard-coded (never "latest") so a build six months from now
# reproduces the exact same vendored bytes until a human deliberately bumps a pin here.
# --------------------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDOR_DIR = Path(__file__).resolve().parent
PYODIDE_DIR = VENDOR_DIR / "pyodide"
WHEELS_DIR = VENDOR_DIR / "wheels"
CHECKSUMS_PATH = VENDOR_DIR / "CHECKSUMS.txt"
CA_BUNDLE = Path("/root/.ccr/ca-bundle.crt")

PYODIDE_VERSION = "314.0.6"
PYODIDE_CORE_URL = (
    f"https://github.com/pyodide/pyodide/releases/download/"
    f"{PYODIDE_VERSION}/pyodide-core-{PYODIDE_VERSION}.tar.bz2"
)
PYODIDE_CORE_SHA256 = "1016c31e39ce3764d9a418cbb491a392c802c1b86ccc1367f009f5c59bf8f5fd"

MICROPIP_VERSION = "0.11.1"  # must match the "micropip" entry in the vendored pyodide-lock.json
MICROPIP_URL = (
    "https://files.pythonhosted.org/packages/17/54/"
    "d737f98fe5da2fd3375de34e9231c56550eefdd33ae9b671a25070792430/"
    "micropip-0.11.1-py3-none-any.whl"
)
MICROPIP_SHA256 = "768de165adb8e66906039d8fec4127a57f522dfdf8e6fbff877a4616941ecfd3"

OPENPYXL_VERSION = "3.1.5"  # satisfies pyproject.toml's "openpyxl>=3.1,<4"
OPENPYXL_URL = (
    "https://files.pythonhosted.org/packages/c0/da/"
    "977ded879c29cbd04de313843e76868e6e13408a94ed6b987245dc7c8506/"
    "openpyxl-3.1.5-py2.py3-none-any.whl"
)
OPENPYXL_SHA256 = "5282c12b107bffeef825f4617dc029afaf41d0ea60823bbb665ef3079dc79de2"

ET_XMLFILE_VERSION = "2.0.0"  # openpyxl's only runtime dependency; also pure-Python
ET_XMLFILE_URL = (
    "https://files.pythonhosted.org/packages/c1/8b/"
    "5fe2cc11fee489817272089c4203e679c63b570a5aaeb18d852ae3cbba6a/"
    "et_xmlfile-2.0.0-py3-none-any.whl"
)
ET_XMLFILE_SHA256 = "7a91720bc756843502c3b7504c77b8fe44217c85c537d85037f0f536151b2caa"


@dataclass(frozen=True)
class VendoredArtifact:
    filename: str
    version: str
    sha256: str
    category: str  # "pyodide-core" | "wheel" | "fina-wheel"


def _ssl_context() -> ssl.SSLContext:
    if CA_BUNDLE.exists():
        return ssl.create_default_context(cafile=str(CA_BUNDLE))
    return ssl.create_default_context()


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_MAX_ATTEMPTS = 3


def _download(url: str, dest: Path, expected_sha256: str) -> None:
    """Download `url` to `dest`, aborting loudly if the bytes don't match the pin.

    Retries a handful of times on a mid-transfer network hiccup (a timeout or reset, not a
    policy denial) -- this environment's proxy README notes that a dropped tunnel surfaces to
    the calling tool as a bare reset/timeout with no distinguishing error, so a short retry is
    the correct response, not a workaround for something structurally broken.
    """
    print(f"downloading {url}")
    context = _ssl_context()
    request = urllib.request.Request(url, headers={"User-Agent": "fina-vendor-build/1.0"})
    last_error: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, context=context, timeout=120) as response:
                dest.write_bytes(response.read())
            last_error = None
            break
        except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
            last_error = exc
            print(f"  attempt {attempt}/{_MAX_ATTEMPTS} failed ({exc!r}); retrying")
            time.sleep(2 * attempt)
    if last_error is not None:
        raise RuntimeError(f"download failed after {_MAX_ATTEMPTS} attempts: {url}") from last_error
    actual = _sha256_of(dest)
    if actual != expected_sha256:
        raise RuntimeError(
            f"sha256 mismatch for {url}\n  expected: {expected_sha256}\n  actual:   {actual}\n"
            "Refusing to vendor an artifact that does not match its pin -- either the pin is "
            "stale (bump it deliberately after re-verifying the new artifact) or the download "
            "was tampered with/corrupted in transit."
        )


def _clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def vendor_pyodide_core(tmp_dir: Path) -> VendoredArtifact:
    archive = tmp_dir / f"pyodide-core-{PYODIDE_VERSION}.tar.bz2"
    _download(PYODIDE_CORE_URL, archive, PYODIDE_CORE_SHA256)

    _clean_dir(PYODIDE_DIR)
    with tarfile.open(archive, mode="r:bz2") as tar:
        # The archive contains one top-level "pyodide/" directory; extract then flatten it so
        # web/vendor/pyodide/ holds the runtime files directly.
        extract_root = tmp_dir / "pyodide-core-extract"
        tar.extractall(extract_root, filter="data")  # noqa: S202 -- trusted, sha256-pinned archive
    inner = extract_root / "pyodide"
    if not inner.is_dir():
        raise RuntimeError(f"unexpected archive layout: no 'pyodide/' directory under {inner}")
    for item in inner.iterdir():
        shutil.move(str(item), str(PYODIDE_DIR / item.name))

    print(f"vendored Pyodide core {PYODIDE_VERSION} -> {PYODIDE_DIR}")
    return VendoredArtifact(
        filename=archive.name,
        version=PYODIDE_VERSION,
        sha256=PYODIDE_CORE_SHA256,
        category="pyodide-core",
    )


def vendor_wheel(url: str, sha256: str, version: str, tmp_dir: Path) -> VendoredArtifact:
    filename = url.rsplit("/", 1)[-1]
    staged = tmp_dir / filename
    _download(url, staged, sha256)
    WHEELS_DIR.mkdir(parents=True, exist_ok=True)
    dest = WHEELS_DIR / filename
    shutil.copyfile(staged, dest)
    print(f"vendored wheel {filename} -> {dest}")
    return VendoredArtifact(filename=filename, version=version, sha256=sha256, category="wheel")


def build_fina_wheel(tmp_dir: Path) -> VendoredArtifact:
    """Build fina's own wheel from this checkout via ``python -m build``."""
    out_dir = tmp_dir / "fina-dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"building fina's wheel from {REPO_ROOT} via `python -m build --wheel`")
    subprocess.run(  # noqa: S603 -- fixed argv, no shell, trusted local build backend
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
        cwd=REPO_ROOT,
        check=True,
    )
    built = sorted(out_dir.glob("fina-*.whl"))
    if len(built) != 1:
        raise RuntimeError(f"expected exactly one built fina wheel, found: {built}")
    source = built[0]
    WHEELS_DIR.mkdir(parents=True, exist_ok=True)
    dest = WHEELS_DIR / source.name
    shutil.copyfile(source, dest)
    sha256 = _sha256_of(dest)
    # fina-<version>-py3-none-any.whl -> "<version>"
    version = source.name.removeprefix("fina-").split("-")[0]
    print(f"built fina wheel {source.name} -> {dest}")
    return VendoredArtifact(
        filename=source.name, version=version, sha256=sha256, category="fina-wheel"
    )


def write_checksums(artifacts: list[VendoredArtifact]) -> None:
    lines = [
        "# Vendored artifact checksums for Fina's Pyodide PWA (WP-11).",
        "# Regenerated by web/vendor/build.py -- do not hand-edit.",
        "# filename\tversion\tcategory\tsha256",
    ]
    for artifact in artifacts:
        lines.append(f"{artifact.filename}\t{artifact.version}\t{artifact.category}\t{artifact.sha256}")
    CHECKSUMS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {CHECKSUMS_PATH}")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="fina-vendor-build-") as tmp:
        tmp_dir = Path(tmp)
        artifacts = [
            vendor_pyodide_core(tmp_dir),
            vendor_wheel(MICROPIP_URL, MICROPIP_SHA256, MICROPIP_VERSION, tmp_dir),
            vendor_wheel(OPENPYXL_URL, OPENPYXL_SHA256, OPENPYXL_VERSION, tmp_dir),
            vendor_wheel(ET_XMLFILE_URL, ET_XMLFILE_SHA256, ET_XMLFILE_VERSION, tmp_dir),
            build_fina_wheel(tmp_dir),
        ]
        write_checksums(artifacts)

    total_bytes = sum(f.stat().st_size for f in VENDOR_DIR.rglob("*") if f.is_file())
    print(f"\nweb/vendor/ total size: {total_bytes / (1024 * 1024):.1f} MiB")


if __name__ == "__main__":
    main()
