"""Tests for fina.io_utils (WP-3): R-1.15..R-1.19, R-2.15."""

from __future__ import annotations

from pathlib import Path

import pytest

from fina.errors import DuplicateSourceError, ParseError
from fina.io_utils import (
    check_no_duplicate_file_contents,
    decode_text_with_fallback,
    empty_table_warning,
    read_csv_table,
    sha256_of_file,
)

SAMPLE_CSV = "a,b,c\n1,2,3\n4,5,6\n"


# ---------------------------------------------------------------------------
# R-1.15: encoding + BOM
# ---------------------------------------------------------------------------


def test_t118_utf8_bom_stripped() -> None:
    bom = b"\xef\xbb\xbf"
    data = bom + b"a,b\n1,2\n"
    text, warnings = decode_text_with_fallback(data, source_file="f.csv")
    assert text == "a,b\n1,2\n"
    assert "﻿" not in text
    assert warnings == ()


def test_t119_cp1252_fallback_with_warning() -> None:
    # 'ñ' encoded in cp1252 (0xF1) is not valid UTF-8 on its own.
    data = "año".encode("cp1252")
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    text, warnings = decode_text_with_fallback(data, source_file="f.csv")
    assert text == "año"
    assert len(warnings) == 1
    assert warnings[0].message == "f.csv: UTF-8 decoding failed; fell back to cp1252"
    assert warnings[0].source_file == "f.csv"
    assert warnings[0].source_row is None


def test_t120_undecodable_bytes_raise_parse_error() -> None:
    # 0x81 is undefined in cp1252 and invalid as a UTF-8 continuation byte.
    data = b"\x81\x81\x81"
    with pytest.raises(ParseError) as exc_info:
        decode_text_with_fallback(data, source_file="f.csv")
    err = exc_info.value
    assert err.source_file == "f.csv"
    assert err.source_row == 0
    assert err.column == "<file encoding>"
    assert err.raw_value == "<binary content>"
    assert err.expected == "UTF-8 or cp1252-decodable bytes"


def test_plain_utf8_without_bom_decodes_unchanged() -> None:
    text, warnings = decode_text_with_fallback("año,niño\n".encode(), source_file="f.csv")
    assert text == "año,niño\n"
    assert warnings == ()


# ---------------------------------------------------------------------------
# R-1.16 / R-1.17: RFC 4180 parsing, CRLF/LF equivalence, trailing blanks
# ---------------------------------------------------------------------------


def test_t121_crlf_and_lf_produce_identical_rows() -> None:
    lf_header, lf_rows = read_csv_table("a,b\n1,2\n3,4\n")
    crlf_header, crlf_rows = read_csv_table("a,b\r\n1,2\r\n3,4\r\n")
    assert lf_header == crlf_header
    assert lf_rows == crlf_rows


def test_t122_quoted_field_with_comma() -> None:
    header, rows = read_csv_table('a,b\n1,"hello, world"\n')
    assert header == ["a", "b"]
    assert rows == [(2, ["1", "hello, world"])]


def test_t123_quoted_field_with_escaped_quotes() -> None:
    header, rows = read_csv_table('a,b\n1,"quote ""in"" here"\n')
    assert rows == [(2, ["1", 'quote "in" here'])]


def test_t124_quoted_field_with_embedded_newline() -> None:
    header, rows = read_csv_table('a,b\n1,"multi\nline"\n')
    assert rows == [(2, ["1", "multi\nline"])]


def test_t125_trailing_blank_lines_ignored() -> None:
    header, rows = read_csv_table("a,b\n1,2\n\n\n")
    assert header == ["a", "b"]
    assert rows == [(2, ["1", "2"])]


def test_single_trailing_blank_line_ignored() -> None:
    # Exactly one trailing blank line -- distinguishes "strip one row per iteration" from a
    # buggy "strip two rows per iteration", which would happen to give the same answer when
    # there are exactly two trailing blanks (as in test_t125) but not when there is one.
    header, rows = read_csv_table("a,b\n1,2\n\n")
    assert header == ["a", "b"]
    assert rows == [(2, ["1", "2"])]


def test_file_of_only_blank_lines_is_fully_empty() -> None:
    header, rows = read_csv_table("\n\n\n")
    assert header == []
    assert rows == []


def test_file_of_only_comma_only_lines_is_fully_empty() -> None:
    # Each line "," parses as two empty-string fields (['', '']), not the fully-empty []
    # a bare blank text line produces -- distinguishes "trim while any row remains" from a
    # buggy "stop trimming with one row left", which would otherwise both coincidentally
    # report an empty header for the plain "\n\n\n" case above.
    header, rows = read_csv_table(",\n,\n,\n")
    assert header == []
    assert rows == []


def test_trailing_line_of_empty_fields_is_treated_as_blank() -> None:
    # A trailing line of empty comma-separated fields (csv parses "," as ["", ""], not the
    # fully-empty [] a blank text line produces) must still be recognized as blank.
    header, rows = read_csv_table("a,b\n1,2\n,\n")
    assert header == ["a", "b"]
    assert rows == [(2, ["1", "2"])]


# ---------------------------------------------------------------------------
# R-1.18: empty / header-only files
# ---------------------------------------------------------------------------


def test_t126_empty_file_yields_no_header_no_rows() -> None:
    header, rows = read_csv_table("")
    assert header == []
    assert rows == []


def test_t127_header_only_file_yields_no_rows() -> None:
    header, rows = read_csv_table("a,b,c\n")
    assert header == ["a", "b", "c"]
    assert rows == []


def test_empty_table_warning_names_the_file() -> None:
    warning = empty_table_warning("f.csv")
    assert warning.source_file == "f.csv"
    assert "f.csv" in warning.message


# ---------------------------------------------------------------------------
# R-1.19: physical 1-indexed row numbers, header counted as row 1
# ---------------------------------------------------------------------------


def test_t128_source_row_is_physical_1_indexed_row() -> None:
    header, rows = read_csv_table(SAMPLE_CSV)
    assert rows[0][0] == 2  # first data row is physical row 2 (header is row 1)
    assert rows[1][0] == 3


def test_source_row_correct_with_multiline_quoted_field_before_it() -> None:
    text = 'a,b\n1,"multi\nline"\n2,plain\n'
    header, rows = read_csv_table(text)
    # csv module counts logical rows, not physical text lines, which is what we want here:
    # the "row" abstraction is a logical CSV record, and its 1-based position among records
    # (including the header) is what R-1.19 means by "row" for traceability purposes.
    assert [r[0] for r in rows] == [2, 3]


# ---------------------------------------------------------------------------
# R-2.15: duplicate file content across files in one run
# ---------------------------------------------------------------------------


def test_t503_duplicate_file_content_raises(tmp_path: Path) -> None:
    content = b"a,b\n1,2\n"
    file_a = tmp_path / "a.csv"
    file_b = tmp_path / "b.csv"
    file_a.write_bytes(content)
    file_b.write_bytes(content)
    with pytest.raises(DuplicateSourceError) as exc_info:
        check_no_duplicate_file_contents([file_a, file_b])
    assert exc_info.value.files == ("a.csv", "b.csv")
    assert exc_info.value.transaction_id is None
    assert exc_info.value.source_rows == ()


def test_distinct_file_contents_do_not_raise(tmp_path: Path) -> None:
    file_a = tmp_path / "a.csv"
    file_b = tmp_path / "b.csv"
    file_a.write_bytes(b"a,b\n1,2\n")
    file_b.write_bytes(b"a,b\n9,9\n")
    check_no_duplicate_file_contents([file_a, file_b])


def test_sha256_of_file_matches_hashlib(tmp_path: Path) -> None:
    import hashlib

    path = tmp_path / "x.csv"
    path.write_bytes(b"hello world")
    assert sha256_of_file(path) == hashlib.sha256(b"hello world").hexdigest()
