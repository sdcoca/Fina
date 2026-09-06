"""Tests for fina.money (WP-1): R-1.2, R-1.5, R-1.11, R-1.12, R-1.13, R-1.14, R-7.3, R-7.6."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fina.errors import ParseError
from fina.money import (
    ibans_match,
    names_match,
    normalize_iban,
    normalize_name,
    parse_spanish_amount,
    parse_spanish_date,
    round_half_up,
)

CTX = {"source_file": "f.csv", "source_row": 2, "column": "Importe"}


def _amount(raw: str) -> Decimal:
    return parse_spanish_amount(raw, **CTX)


# ---------------------------------------------------------------------------
# R-7.6 / R-7.3: Spanish amount parsing
# ---------------------------------------------------------------------------


def test_t001_thousands_and_decimal() -> None:
    assert _amount("1.234,56€") == Decimal("1234.56")


def test_t002_no_thousands_group() -> None:
    assert _amount("12,40€") == Decimal("12.40")


def test_t003_two_thousands_groups() -> None:
    assert _amount("1.234.567,89€") == Decimal("1234567.89")


def test_t004_negative() -> None:
    assert _amount("-650,25€") == Decimal("-650.25")


def test_t005_zero() -> None:
    assert _amount("0,00€") == Decimal("0.00")


def test_t006_header_form_trailing_iso_code() -> None:
    assert _amount("1.126,84€ EUR") == Decimal("1126.84")


def test_t007_nbsp_before_euro_sign() -> None:
    assert _amount("12,40 €") == Decimal("12.40")


def test_t008_leading_trailing_whitespace() -> None:
    assert _amount("  12,40€  ") == Decimal("12.40")


def test_t009_dot_decimal_wrong_locale_raises() -> None:
    with pytest.raises(ParseError) as exc_info:
        _amount("1234.56")
    err = exc_info.value
    assert err.source_file == "f.csv"
    assert err.source_row == 2
    assert err.column == "Importe"
    assert err.raw_value == "1234.56"
    assert err.expected == "Spanish amount, e.g. '1.234,56€'"


def test_t010_empty_string_raises() -> None:
    with pytest.raises(ParseError):
        _amount("")


def test_t011_garbage_raises() -> None:
    with pytest.raises(ParseError):
        _amount("abc")


def test_t012_three_decimals_raises() -> None:
    with pytest.raises(ParseError):
        _amount("1,234")


def test_t013_result_is_decimal_never_float() -> None:
    result = _amount("12,40€")
    assert isinstance(result, Decimal)
    assert not isinstance(result, float)


# ---------------------------------------------------------------------------
# R-1.5: rounding
# ---------------------------------------------------------------------------


def test_t014_round_half_up() -> None:
    assert round_half_up(Decimal("2.675")) == Decimal("2.68")


def test_t015_round_half_away_from_zero_negative() -> None:
    assert round_half_up(Decimal("-0.005")) == Decimal("-0.01")


def test_t016_round_applied_once_not_after_sum() -> None:
    # 1.005 and 1.005 each round to 1.01 (half-up), so round-then-sum gives 2.02, while the
    # exact sum 2.010 rounds to 2.01. The two paths disagree -- proof that rounding must
    # happen once, at render time, on the already-summed exact value (R-1.5).
    a, b = Decimal("1.005"), Decimal("1.005")
    round_then_sum = round_half_up(a) + round_half_up(b)
    sum_then_round = round_half_up(a + b)
    assert round_then_sum != sum_then_round
    assert sum_then_round == Decimal("2.01")


# ---------------------------------------------------------------------------
# R-1.12: IBAN normalization
# ---------------------------------------------------------------------------


def test_t017_iban_normalization_strips_spaces_and_upcases() -> None:
    assert normalize_iban("es00 0000 0000 0000 0000 0202") == "ES0000000000000000000202"


def test_t018_iban_comparison_exact() -> None:
    a = "ES00 0000 0000 0000 0000 0202"
    b = "ES00 0000 0000 0000 0000 0203"
    assert not ibans_match(a, b)
    assert ibans_match(a, "es0000000000000000000202")


# ---------------------------------------------------------------------------
# R-1.13 / R-1.14: name normalization and matching
# ---------------------------------------------------------------------------


def test_t019_case_difference_matches() -> None:
    assert names_match("Fernandez Ortiz Lucia", "FERNANDEZ ORTIZ LUCIA")


def test_t020_word_order_permutation_matches() -> None:
    assert names_match("FERNANDEZ ORTIZ LUCIA", "LUCIA FERNANDEZ ORTIZ")


def test_t021_extra_internal_and_trailing_whitespace_matches() -> None:
    assert names_match("FERNANDEZ   ORTIZ LUCIA ", "FERNANDEZ ORTIZ LUCIA")


def test_t022_vowel_accents_fold() -> None:
    assert names_match("FERNÁNDEZ", "FERNANDEZ")


def test_t023_enye_does_not_fold() -> None:
    assert not names_match("PEÑA", "PENA")


def test_t024_different_token_multiset_does_not_match() -> None:
    assert not names_match("A B C", "A B")


def test_t025_no_substring_or_edit_distance_matching() -> None:
    assert not names_match("JUAN", "JUANA")


def test_normalize_name_empty_string_has_no_tokens() -> None:
    assert normalize_name("   ") == ""
    assert not names_match("", "A")
    assert names_match("", "  ")


@given(st.text(alphabet="ABCDEFÁÉÍÓÚÑ ", min_size=0, max_size=20))
def test_t605_name_matching_idempotent_and_symmetric(raw: str) -> None:
    normalized_once = normalize_name(raw)
    normalized_twice = normalize_name(normalized_once)
    assert normalized_once == normalized_twice
    assert names_match(raw, raw)
    other = raw[::-1]
    assert names_match(raw, other) == names_match(other, raw)


# ---------------------------------------------------------------------------
# R-1.11: Spanish date parsing
# ---------------------------------------------------------------------------


def test_t026_spanish_date_day_first() -> None:
    got = parse_spanish_date("07/03/2027", source_file="f.xlsx", source_row=9, column="Fecha")
    assert got == date(2027, 3, 7)


def test_t027_invalid_calendar_date_raises() -> None:
    with pytest.raises(ParseError) as exc_info:
        parse_spanish_date("31/02/2027", source_file="f.xlsx", source_row=9, column="Fecha")
    err = exc_info.value
    assert err.source_file == "f.xlsx"
    assert err.source_row == 9
    assert err.column == "Fecha"
    assert err.raw_value == "31/02/2027"
    assert err.expected == "a calendar-valid DD/MM/YYYY date"


def test_t028_iso_format_in_ddmmyyyy_column_raises() -> None:
    with pytest.raises(ParseError) as exc_info:
        parse_spanish_date("2027-03-07", source_file="f.xlsx", source_row=9, column="Fecha")
    err = exc_info.value
    assert err.source_file == "f.xlsx"
    assert err.source_row == 9
    assert err.column == "Fecha"
    assert err.raw_value == "2027-03-07"
    assert err.expected == "date in DD/MM/YYYY format"


# ---------------------------------------------------------------------------
# T-600: property-based round trip
# ---------------------------------------------------------------------------


@given(
    st.decimals(
        min_value=Decimal("-999999.99"),
        max_value=Decimal("999999.99"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    )
)
def test_t600_spanish_amount_format_parse_round_trip(value: Decimal) -> None:
    sign = "-" if value < 0 else ""
    magnitude = -value if value < 0 else value
    int_part, _, dec_part = str(magnitude).partition(".")
    dec_part = (dec_part + "00")[:2]
    grouped: list[str] = []
    while len(int_part) > 3:
        grouped.insert(0, int_part[-3:])
        int_part = int_part[:-3]
    grouped.insert(0, int_part)
    spanish = f"{sign}{'.'.join(grouped)},{dec_part}€"
    assert parse_spanish_amount(spanish, **CTX) == value
