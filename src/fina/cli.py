"""Command-line interface (spec section 11).

Implements: R-11.3, R-11.4 (R-11.1/R-11.2/R-11.5 are `fina.pipeline`'s responsibility).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fina.errors import FinaError
from fina.money import round_half_up
from fina.pipeline import run_pipeline
from fina.render.prepare import to_chart_rows
from fina.render.section1_chart import render_section1_chart


def _build(input_dir: Path, out_dir: Path) -> int:
    """R-11.4: any known pipeline failure aborts with a non-zero exit code and writes nothing
    to `out_dir` -- a report that exists but is wrong is worse than no report. `run_pipeline`
    itself writes the manifest (R-11.5), only as its very last step; the chart is the one
    thing this layer writes directly.
    """
    try:
        result = run_pipeline(input_dir, out_dir)
    except FinaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # R-11.3/R-1.24: every warning is printed before any figure.
    for warning in result.warnings:
        print(f"warning: {warning.message}")

    if result.series:
        latest = result.series[-1]
        as_of_note = " (partial month)" if latest.is_partial else ""
        print(
            f"real_net_worth ({latest.completeness}) as of {latest.as_of.isoformat()}"
            f"{as_of_note}: {round_half_up(latest.real_net_worth)} EUR"
        )
        print(f"savings_only: {round_half_up(latest.savings_only)} EUR")
        print(f"gap: {round_half_up(latest.gap)} EUR")

    chart_html = render_section1_chart(to_chart_rows(result.series))
    (out_dir / "section1_chart.html").write_text(chart_html, encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fina")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser(
        "build", help="Run the full pipeline over an input directory."
    )
    build_parser.add_argument("--input", required=True, type=Path)
    build_parser.add_argument("--out", required=True, type=Path)

    args = parser.parse_args(argv)
    return _build(args.input, args.out)
