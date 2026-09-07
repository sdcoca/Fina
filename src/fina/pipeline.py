"""Pipeline orchestration (spec section 11).

Implements: R-11.1, R-11.2, R-11.5 (R-11.3/R-11.4 are the CLI's responsibility -- see
``fina.cli`` -- since they concern *when* things print and *what* exit code is returned, not
what this module computes).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from fina import __version__
from fina.adapters import bank_xlsx, broker_csv
from fina.classification import classify_entries, collect_owned_accounts
from fina.errors import ParseError
from fina.io_utils import check_no_duplicate_file_contents, sha256_of_file
from fina.models import AccountDeclaration, AdapterResult, LedgerEntry, Warning
from fina.reconciliation import reconcile
from fina.section1 import Section1Period, compute_section1

#: R-11.2: each adapter's (name, sniff, parse) -- tried in this fixed order so adapter
#: selection is deterministic (R-1.20) even if two adapters could somehow both claim a file.
_AdapterEntry = tuple[str, Callable[[Path], bool], Callable[[Path], AdapterResult]]
_ADAPTERS: tuple[_AdapterEntry, ...] = (
    ("trade_republic_broker_csv", broker_csv.sniff, broker_csv.parse),
    ("bank_es_xlsx", bank_xlsx.sniff, bank_xlsx.parse),
)


@dataclass(frozen=True)
class InputFile:
    """One input file's identity for the run manifest (R-11.5)."""

    name: str
    sha256: str
    adapter: str


@dataclass(frozen=True)
class PipelineResult:
    """Everything the CLI needs to print, write, and render (R-11.1..R-11.5)."""

    input_files: tuple[InputFile, ...]
    owned_accounts: tuple[AccountDeclaration, ...]
    entries: tuple[LedgerEntry, ...]
    warnings: tuple[Warning, ...]
    series: tuple[Section1Period, ...]
    tool_version: str


def _discover_files(input_dir: Path) -> list[Path]:
    """R-11.1: file discovery. Sorted by name for determinism (R-1.20); directories and
    dotfiles are skipped.
    """
    return sorted(
        (p for p in input_dir.iterdir() if p.is_file() and not p.name.startswith(".")),
        key=lambda p: p.name,
    )


def _select_adapter(file_path: Path) -> tuple[str, Callable[[Path], AdapterResult]]:
    """R-11.2: adapter selection by file shape, never by filename or extension."""
    tried: list[str] = []
    for name, sniff, parse_fn in _ADAPTERS:
        tried.append(name)
        if sniff(file_path):
            return name, parse_fn
    raise ParseError(
        source_file=file_path.name,
        source_row=1,
        column="<file shape>",
        raw_value="<unrecognized>",
        expected=f"a shape matching one of the known adapters: {', '.join(tried)}",
    )


def _header_balances_for(adapter_name: str, file_path: Path) -> dict[tuple[str, str], Decimal]:
    """R-8.5's same-file cross-check: only the bank adapter's export carries a header-stated
    balance to compare against (R-7.2); other adapters contribute nothing here.
    """
    if adapter_name != "bank_es_xlsx":
        return {}
    header = bank_xlsx.read_header_block(file_path)
    return {(bank_xlsx.INSTITUTION, file_path.name): header.balance}


def run_pipeline(input_dir: Path, out_dir: Path | None = None) -> PipelineResult:
    """R-11.1: discover -> parse (per adapter, R-11.2) -> classify (§3) -> reconcile (§8) ->
    compute Section 1 (§9). Validation happens inside each adapter's own `LedgerEntry`
    construction (R-2.x), not as a separate step here.

    When `out_dir` is given, the run manifest (R-11.5) is written to `out_dir/manifest.json`
    as the very last step, after every step that can raise has already succeeded -- so a
    failing run (R-11.4) never gets this far and never writes a partial file. `out_dir` is
    optional because most callers (this module's own tests, `fina.cli`, which also writes the
    chart alongside the manifest) only need the returned `PipelineResult`, not the file.

    Raises `DuplicateSourceError` (R-2.15), `ParseError` (R-11.2, an unrecognized file shape),
    or whatever `reconcile` raises (`ReconciliationError`) -- the CLI decides what to do with
    each (R-11.4).
    """
    files = _discover_files(input_dir)
    check_no_duplicate_file_contents(files)

    input_files: list[InputFile] = []
    all_accounts: list[AccountDeclaration] = []
    all_entries: list[LedgerEntry] = []
    all_warnings: list[Warning] = []
    header_balances: dict[tuple[str, str], Decimal] = {}

    for file_path in files:
        adapter_name, parse_fn = _select_adapter(file_path)
        input_files.append(
            InputFile(name=file_path.name, sha256=sha256_of_file(file_path), adapter=adapter_name)
        )
        parsed = parse_fn(file_path)
        all_accounts.extend(parsed.accounts)
        all_entries.extend(parsed.entries)
        all_warnings.extend(parsed.warnings)
        header_balances.update(_header_balances_for(adapter_name, file_path))

    owned_accounts = collect_owned_accounts(all_accounts)
    classified_entries, classification_warnings = classify_entries(all_entries, owned_accounts)
    all_warnings.extend(classification_warnings)

    reconciliation_warnings = reconcile(classified_entries, header_balances)
    all_warnings.extend(reconciliation_warnings)

    series = compute_section1(classified_entries)

    result = PipelineResult(
        input_files=tuple(input_files),
        owned_accounts=owned_accounts,
        entries=classified_entries,
        warnings=tuple(all_warnings),
        series=series,
        tool_version=__version__,
    )

    if out_dir is not None:
        write_manifest(result, out_dir)

    return result


def write_manifest(result: PipelineResult, out_dir: Path) -> Path:
    """R-11.5: writes `out_dir/manifest.json`, creating `out_dir` if needed. Two runs over
    identical inputs write byte-identical files (R-1.20).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_dict(result), indent=2) + "\n", encoding="utf-8")
    return manifest_path


def manifest_dict(result: PipelineResult) -> dict[str, object]:
    """R-11.5: a JSON-serializable run manifest -- input files with their SHA-256, the
    `owned_accounts` set (R-3.7), every warning, the tool version, and the resulting Section 1
    series. Every value is a plain `str`/`int`/`bool`/`list`/`dict` (Decimal and date values
    are rendered as strings, never as a binary float) so `json.dumps` needs no custom encoder,
    and two runs over identical inputs produce byte-identical JSON (R-1.20/R-11.5).
    """
    return {
        "tool_version": result.tool_version,
        "input_files": [
            {"name": f.name, "sha256": f.sha256, "adapter": f.adapter} for f in result.input_files
        ],
        "owned_accounts": [
            {
                "institution": a.institution,
                "iban_or_account": a.iban_or_account,
                "holder_name": a.holder_name,
                "declared_in_file": a.declared_in_file,
                "as_of_date": a.as_of_date.isoformat(),
            }
            for a in result.owned_accounts
        ],
        "warnings": [
            {"message": w.message, "source_file": w.source_file, "source_row": w.source_row}
            for w in result.warnings
        ],
        "section1_series": [
            {
                "month": p.month.isoformat(),
                "as_of": p.as_of.isoformat(),
                "is_partial": p.is_partial,
                "real_net_worth": str(p.real_net_worth),
                "completeness": p.completeness,
                "savings_flow": str(p.savings_flow),
                "savings_only": str(p.savings_only),
                "gap": str(p.gap),
            }
            for p in result.series
        ],
    }
