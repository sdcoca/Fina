"""File reading: encodings, RFC 4180 CSV parsing, physical row indexing, content hashing.

Implements: R-1.15, R-1.16, R-1.17, R-1.18, R-1.19, R-2.15.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Sequence
from pathlib import Path

from fina.errors import DuplicateSourceError, ParseError
from fina.models import Warning


def sha256_of_file(path: Path) -> str:
    """The hex SHA-256 digest of a file's raw bytes (R-2.15)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_no_duplicate_file_contents(files: Sequence[Path]) -> None:
    """R-2.15: within one pipeline run, no two input files may have identical content.

    Raises on the first duplicate found, in the order ``files`` is given (deterministic,
    R-1.20).
    """
    seen: dict[str, list[str]] = {}
    for path in files:
        digest = sha256_of_file(path)
        seen.setdefault(digest, []).append(path.name)
    for names in seen.values():
        if len(names) > 1:
            raise DuplicateSourceError(source_rows=(), files=tuple(names))


def decode_text_with_fallback(
    data: bytes,
    *,
    source_file: str,
) -> tuple[str, tuple[Warning, ...]]:
    """Decode file bytes as UTF-8 (BOM stripped if present); retry as cp1252 on failure,
    emitting a warning naming the file and the fallback used; raise ``ParseError`` if both
    fail (R-1.15).
    """
    try:
        return data.decode("utf-8-sig"), ()
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode("cp1252")
    except UnicodeDecodeError as exc:
        raise ParseError(
            source_file=source_file,
            source_row=0,
            column="<file encoding>",
            raw_value="<binary content>",
            expected="UTF-8 or cp1252-decodable bytes",
        ) from exc
    warning = Warning(
        message=f"{source_file}: UTF-8 decoding failed; fell back to cp1252",
        source_file=source_file,
        source_row=None,
    )
    return text, (warning,)


def _is_blank_row(row: list[str]) -> bool:
    return all(cell.strip() == "" for cell in row)


def read_csv_table(text: str) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """Parse RFC 4180 CSV text into ``(header, data_rows)``.

    ``data_rows`` is ``(source_row, cells)`` pairs using the 1-indexed physical row number,
    counting the header row as row 1 (R-1.19). Both ``\\n`` and ``\\r\\n`` line endings parse
    identically (R-1.17, delegated to the stdlib ``csv`` module rather than a hand-rolled
    split -- R-1.16). Trailing blank lines are ignored, not parsed as empty rows (R-1.17); a
    file with no rows, or only a header row, yields an empty ``data_rows`` (R-1.18).
    """
    all_rows = list(csv.reader(io.StringIO(text)))
    end = len(all_rows)
    while end > 0 and _is_blank_row(all_rows[end - 1]):
        end -= 1
    trimmed = all_rows[:end]
    if not trimmed:
        return [], []
    header = trimmed[0]
    data_rows = [(idx + 1, row) for idx, row in enumerate(trimmed) if idx > 0]
    return header, data_rows


def empty_table_warning(source_file: str) -> Warning:
    """The required warning for R-1.18: an empty file, or header-only file, is not an error."""
    return Warning(
        message=f"{source_file}: no data rows found (empty file or header-only)",
        source_file=source_file,
        source_row=None,
    )
