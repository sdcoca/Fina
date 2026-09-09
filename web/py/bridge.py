"""Pyodide bridge (WP-12): replicates `fina.cli._build`'s exact steps inside the browser.

This module is imported *inside* a running Pyodide interpreter, after WP-11's vendored wheels
(`fina`, `openpyxl`, `et_xmlfile`) have been `micropip.install()`-ed from local files (see
`web/js/pyodide-bridge.js`) -- at that point `import fina` works unmodified, exactly as it
does under a native CPython interpreter, because the wheel is the same artifact `pip install`
would use. This module performs **no financial computation of its own**: every figure comes
from unmodified calls into `fina.pipeline.run_pipeline`, `fina.render.prepare.to_chart_rows`
and `fina.render.section1_chart.render_section1_chart` -- the exact three calls
`fina.cli._build` makes today (see that module, kept side by side in spirit with this one).

Bridging mechanism (`docs/plan/mobile-pyodide.md` §3.2, WP-11's own research): Pyodide's
Emscripten-backed virtual filesystem (MEMFS) is transparent below `pathlib.Path`/`open()` --
JS writes bytes into it, and ordinary `Path` calls on the Python side see an ordinary-looking
filesystem. This module therefore needs **zero** changes to `src/fina`: it writes incoming
bytes to fixed MEMFS paths (`/uploads`, `/out`, `/sniff`) with plain `Path.write_bytes`/
`Path.read_text`, then calls the real pipeline functions exactly as `cli.py` does.

Public surface, called from `web/js/pyodide-bridge.js`:
    - `sniff(filename: str, data) -> str | None`
    - `run(active_files) -> dict`

Both accept a "JS-friendly" `data`/`active_files` shape rather than a strict Python type,
because the same functions are exercised two ways: from JS, where `data` arrives as a Pyodide
`JsProxy`-wrapped `Uint8Array` and `active_files` as a `JsProxy`-wrapped JS array of
`{filename, data}` objects; and from `tests/test_pyodide_bridge.py`'s differential test, which
calls these same functions with plain Python `bytes`/`dict`/`tuple` values for convenience
where it drives them directly. `_to_bytes`/`_iter_active_files` below normalize either shape.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fina.errors import FinaError
from fina.money import round_half_up
from fina.pipeline import run_pipeline, sniff_adapter_name
from fina.render.prepare import to_chart_rows
from fina.render.section1_chart import render_section1_chart

__all__ = ["run", "sniff"]

#: Fixed MEMFS paths, not per-call temp directories: this mirrors the exact flow
#: `docs/plan/mobile-pyodide.md` §3.2 lays out ("/uploads" and "/out"), and -- more
#: importantly for `run`'s negative-case contract -- makes "nothing was written under /out"
#: independently checkable from the test/JS side after a failing run, since the path is known
#: in advance rather than a fresh random name each call.
_UPLOADS_DIR = Path("/uploads")
_OUT_DIR = Path("/out")
_SNIFF_DIR = Path("/sniff")


def _reset_dir(path: Path) -> None:
    """Removes `path` (if present) and recreates it empty.

    Called at the start of every `sniff`/`run` call so a previous call's files can never leak
    into the next one -- MEMFS has no per-call isolation of its own, so this module supplies
    it explicitly rather than relying on fixed paths staying "naturally" clean.
    """
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _to_bytes(data: Any) -> bytes:
    """Normalizes one file's payload to `bytes`, whatever shape it arrived in.

    Handles, in order: plain Python `bytes`/`bytearray` (the differential test calling this
    module directly); a Pyodide `JsProxy` wrapping a JS `Uint8Array` (the real JS call path --
    `JsProxy.to_py()` converts a JS TypedArray to a Python `bytearray`); anything else that
    merely supports the buffer protocol, as a last resort.
    """
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    to_py = getattr(data, "to_py", None)
    if callable(to_py):
        return bytes(to_py())
    return bytes(data)


def _extract_one(item: Any) -> tuple[str, Any]:
    """Pulls `(filename, data)` out of one `active_files` entry, whatever shape it arrived in:
    a `{"filename": ..., "data": ...}` dict or a `(filename, data)` tuple/list (both used by
    `tests/test_pyodide_bridge.py` for convenience), or a Pyodide `JsProxy` wrapping a JS
    object with `.filename`/`.data` properties (the real shape `web/js/pyodide-bridge.js`
    sends -- see that file's `runBuild`).
    """
    if isinstance(item, dict):
        return str(item["filename"]), item["data"]
    if isinstance(item, (tuple, list)) and len(item) == 2:
        return str(item[0]), item[1]
    return str(item.filename), item.data


def _iter_active_files(active_files: Any) -> list[tuple[str, Any]]:
    return [_extract_one(item) for item in active_files]


def sniff(filename: str, data: Any) -> str | None:
    """Which adapter's shape `data` matches, or `None` if none does -- never raises on an
    unrecognized file, matching `fina.pipeline.sniff_adapter_name`'s own contract (WP-10).

    Writes `data` to a MEMFS file *named* `filename` (its base name only, so a path-like
    filename can never write outside `_SNIFF_DIR`) rather than a generic temp name: R-11.2
    says adapter dispatch never looks at filename/extension, but the real filename is
    preserved anyway in case a future adapter's error/warning message wants to cite it (per
    WP-12's own spec: "preserve the real filename anyway since it appears in error/warning
    messages").
    """
    _reset_dir(_SNIFF_DIR)
    file_path = _SNIFF_DIR / Path(filename).name
    file_path.write_bytes(_to_bytes(data))
    return sniff_adapter_name(file_path)


def run(active_files: Any) -> dict[str, Any]:
    """Reproduces `fina.cli._build`'s exact steps over `active_files`, entirely inside MEMFS.

    `active_files` is a sequence of per-file `(filename, data)` pairs (see
    `_iter_active_files`/`_extract_one` for every accepted shape). Every file is written under
    `/uploads`, then `run_pipeline(/uploads, /out)` is called exactly as `cli.py::_build` calls
    it (`out_dir` given, so `run_pipeline` itself writes `/out/manifest.json` as its own last
    step, R-11.5) -- and, on success, this function renders and writes
    `/out/section1_chart.html` itself, exactly as `_build` does.

    Returns a plain, JSON-safe dict (only `str`/`int`/`bool`/`list`/`dict`/`None` values, so
    it round-trips through Pyodide's `PyProxy.toJs()` with no custom handling needed):

    On success::

        {
            "ok": True,
            "warnings": [<str>, ...],
            "summary": {  # None if `result.series` is empty, exactly as `_build` prints
                          # nothing in that case
                "completeness": <str>,
                "as_of": <str, ISO date>,
                "is_partial": <bool>,
                "real_net_worth": <str, rounded>,
                "savings_only": <str, rounded>,
                "gap": <str, rounded>,
            } | None,
            "manifest_json": <str>,   # exact bytes `/out/manifest.json` was written with
            "chart_html": <str>,      # exact bytes `/out/section1_chart.html` was written with
            "error": None,
        }

    On any `FinaError` (R-11.4's contract: no files written, caller reports failure instead of
    trusting a wrong result)::

        {
            "ok": False,
            "warnings": [],
            "summary": None,
            "manifest_json": None,
            "chart_html": None,
            "error": {"type": <exception class name, str>, "message": str(exc)},
        }

    `/out` is reset to empty *before* `run_pipeline` runs and deleted again if `run_pipeline`
    raises, so a failing call provably leaves nothing under `/out` -- not merely "this
    function happens not to write there," but a directory that a caller can independently
    check does not exist.
    """
    _reset_dir(_UPLOADS_DIR)
    for name, data in _iter_active_files(active_files):
        (_UPLOADS_DIR / Path(name).name).write_bytes(_to_bytes(data))

    _reset_dir(_OUT_DIR)
    try:
        result = run_pipeline(_UPLOADS_DIR, _OUT_DIR)
    except FinaError as exc:
        shutil.rmtree(_OUT_DIR, ignore_errors=True)
        return {
            "ok": False,
            "warnings": [],
            "summary": None,
            "manifest_json": None,
            "chart_html": None,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }

    chart_html = render_section1_chart(to_chart_rows(result.series))
    (_OUT_DIR / "section1_chart.html").write_text(chart_html, encoding="utf-8")
    manifest_json = (_OUT_DIR / "manifest.json").read_text(encoding="utf-8")

    summary: dict[str, Any] | None = None
    if result.series:
        latest = result.series[-1]
        summary = {
            "completeness": latest.completeness,
            "as_of": latest.as_of.isoformat(),
            "is_partial": latest.is_partial,
            "real_net_worth": str(round_half_up(latest.real_net_worth)),
            "savings_only": str(round_half_up(latest.savings_only)),
            "gap": str(round_half_up(latest.gap)),
        }

    return {
        "ok": True,
        "warnings": [w.message for w in result.warnings],
        "summary": summary,
        "manifest_json": manifest_json,
        "chart_html": chart_html,
        "error": None,
    }
