"""Numeric and text-normalization primitives shared by every adapter.

Implements: R-1.2, R-1.5, R-1.11, R-1.12, R-1.13, R-1.14, R-7.3, R-7.6.

Every monetary value in this module is :class:`decimal.Decimal`. Construction from source
text is always ``Decimal("<string>")`` (R-1.2); ``float`` never appears, not even as an
intermediate step.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fina.errors import ParseError

# R-7.6: [-]D{1,3}(.DDD)*,DD -- Spanish thousands-dot, comma-decimal amount shape, after the
# currency symbol/ISO code/whitespace have already been stripped.
_SPANISH_AMOUNT_RE = re.compile(r"^-?\d{1,3}(\.\d{3})*,\d{2}$")

# A trailing ISO-4217-shaped currency code, e.g. " EUR" in the header balance form (R-7.3).
_TRAILING_ISO_CODE_RE = re.compile(r"\s+[A-Za-z]{3}$")

# R-1.11: strict DD/MM/YYYY, no other format accepted.
_SPANISH_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")

# R-1.13: vowels with acute/diaeresis accents fold to their bare vowel. Ñ is deliberately
# absent from this table -- it is a distinct Spanish letter and must never fold to N.
_VOWEL_ACCENT_TABLE = str.maketrans("ÁÉÍÓÚÜ", "AEIOUU")

_WHITESPACE_RUN_RE = re.compile(r"\s+")

# Non-breaking space, observed in some bank exports before the euro sign (R-7.6 / T-007).
_NBSP = " "


def parse_spanish_amount(
    raw: str,
    *,
    source_file: str,
    source_row: int,
    column: str,
) -> Decimal:
    """Parse a Spanish-formatted amount string into an exact ``Decimal`` (R-7.6, R-7.3).

    Handles: an optional ``€`` symbol, an optional trailing ISO currency code (the header
    balance form, R-7.3), non-breaking spaces, and surrounding whitespace. Rejects anything
    that does not match the expected shape -- there is no best-effort salvage (R-7.6).
    """
    cleaned = raw.replace(_NBSP, " ").strip()
    cleaned = _TRAILING_ISO_CODE_RE.sub("", cleaned)
    cleaned = cleaned.replace("€", "").strip()

    if not _SPANISH_AMOUNT_RE.match(cleaned):
        raise ParseError(
            source_file=source_file,
            source_row=source_row,
            column=column,
            raw_value=raw,
            expected="Spanish amount, e.g. '1.234,56€'",
        )

    normalized = cleaned.replace(".", "").replace(",", ".")
    return Decimal(normalized)


def round_half_up(value: Decimal) -> Decimal:
    """Round to 2 decimal places, half away from zero (R-1.5).

    Must be applied exactly once, at the point a value is rendered for display -- never to a
    value that will be summed again afterwards.
    """
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def normalize_iban(raw: str) -> str:
    """Strip all whitespace and upper-case (R-1.12). Comparison is exact string equality."""
    return "".join(raw.split()).upper()


def ibans_match(a: str, b: str) -> bool:
    """Exact equality after normalization (R-1.12)."""
    return normalize_iban(a) == normalize_iban(b)


def normalize_name(raw: str) -> str:
    """Normalize a holder/counterparty name for matching (R-1.13).

    Upper-case; trim; collapse internal whitespace runs to a single space; fold accents off
    vowels only. ``Ñ`` is preserved as a distinct letter and must never fold to ``N`` -- see
    the rationale in R-1.13: folding it would let ``PEÑA`` match ``PENA``, a false positive
    that silently reclassifies an external flow as internal.
    """
    s = raw.strip()
    s = _WHITESPACE_RUN_RE.sub(" ", s)
    s = s.upper()
    return s.translate(_VOWEL_ACCENT_TABLE)


def _name_multiset(raw: str) -> tuple[str, ...]:
    normalized = normalize_name(raw)
    if not normalized:
        return ()
    return tuple(sorted(normalized.split(" ")))


def names_match(a: str, b: str) -> bool:
    """Compare two names as an unordered multiset of normalized tokens (R-1.13, R-1.14).

    No fuzzy matching beyond R-1.13: no edit distance, no substring matching (R-1.14).
    """
    return _name_multiset(a) == _name_multiset(b)


def parse_spanish_date(
    raw: str,
    *,
    source_file: str,
    source_row: int,
    column: str,
) -> date:
    """Parse a strict ``DD/MM/YYYY`` date (R-1.11). No fallback to any other format."""
    match = _SPANISH_DATE_RE.match(raw.strip())
    if match is None:
        raise ParseError(
            source_file=source_file,
            source_row=source_row,
            column=column,
            raw_value=raw,
            expected="date in DD/MM/YYYY format",
        )
    day, month, year = (int(group) for group in match.groups())
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ParseError(
            source_file=source_file,
            source_row=source_row,
            column=column,
            raw_value=raw,
            expected="a calendar-valid DD/MM/YYYY date",
        ) from exc
