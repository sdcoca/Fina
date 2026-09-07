"""Tests for fina.adapters.bank_xlsx (WP-5): R-7.1..R-7.16, R-7.5a.

Note (see docs/plan/open-questions.md Q-F, RESOLVED): this adapter sorts entries by
(date, file_sequence), where file_sequence = -source_row (R-7.5a), per R-1.22/R-7.15. This
correctly reproduces a reconciling balance chain on the canonical fixture's same-date pair
(rows 9/10, T-358) and on a same-date-*and*-same-value_date triple tie (T-359) that a
value_date tiebreak alone cannot resolve -- see docs/technical-decisions.md §4.
"""

from __future__ import annotations

import datetime as datetime_module
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest
from openpyxl.worksheet.worksheet import Worksheet

from builders import BANK_XLSX, bank_xlsx_with
from fina.adapters import bank_xlsx
from fina.errors import ParseError, UnknownMovementError, UnsupportedCurrencyError
from fina.models import MovementType

FIXTURE_HOLDER = "FERNANDEZ ORTIZ LUCIA"
FIXTURE_IBAN = "ES0000000000000000000202"


# ---------------------------------------------------------------------------
# §12.1 oracle: the canonical fixture end to end
# ---------------------------------------------------------------------------


def test_t200_canonical_fixture_yields_7_entries() -> None:
    result = bank_xlsx.parse(BANK_XLSX)
    assert len(result.entries) == 7


def test_oracle_header_balance_matches_last_declared_balance() -> None:
    header = bank_xlsx.read_header_block(BANK_XLSX)
    assert header.balance == Decimal("6183.75")
    assert header.iban == FIXTURE_IBAN
    assert header.holder_name == FIXTURE_HOLDER
    assert header.export_date == datetime_module.date(2027, 3, 10)


def test_oracle_savings_flow_march_2027() -> None:
    result = bank_xlsx.parse(BANK_XLSX)
    total = sum((e.cash_effect_eur for e in result.entries), start=Decimal("0"))
    assert total == Decimal("5183.75")


def test_zero_amount_row_warning_names_the_correct_file(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["C9"] = "NOMINA EJEMPLO"
        ws["D9"] = "0,00€"

    path = bank_xlsx_with(tmp_path, mutate, filename="zeroamount.xlsx")
    result = bank_xlsx.parse(path)
    matching = [w for w in result.warnings if "amount_eur is exactly 0" in w.message]
    assert len(matching) == 1
    assert matching[0].source_file == "zeroamount.xlsx"
    assert matching[0].source_row == 9


def test_single_sheet_file_has_no_sheets_warning() -> None:
    result = bank_xlsx.parse(BANK_XLSX)
    assert not any("sheets" in w.message for w in result.warnings)


def test_full_field_propagation_on_a_plain_row(tmp_path: Path) -> None:
    """One row exercising every simple field pass-through in `_build_entry` and the `raw`
    dict, so a mutation swapping any of them for `None`/wrong-key/wrong-case is caught
    directly.
    """

    def mutate(ws: Worksheet) -> None:
        ws["A9"] = "07/03/2027"
        ws["B9"] = "05/03/2027"
        ws["C9"] = "RECIBO Ejemplo S.A."
        ws["D9"] = "-1,00€"
        ws["E9"] = "1.234,56€"
        ws["F9"] = "EUR"

    path = bank_xlsx_with(tmp_path, mutate, filename="fullfield.xlsx")
    result = bank_xlsx.parse(path)
    entry = next(e for e in result.entries if e.source_row == 9)
    assert entry.institution == "bank_es"
    assert entry.account == "current_account"
    assert entry.currency == "EUR"
    assert entry.status == "actual"
    assert entry.is_external_flow is True  # EXPENSE
    assert entry.source_file == "fullfield.xlsx"
    assert entry.raw == {
        "Fecha operación": "07/03/2027",
        "Fecha valor": "05/03/2027",
        "Concepto": "RECIBO Ejemplo S.A.",
        "Importe": "-1,00€",
        "Saldo": "1.234,56€",
        "Divisa": "EUR",
    }


def test_external_deposit_row_has_undetermined_is_external_flow(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["C9"] = "TRANSFERENCIA DE ALGUIEN"
        ws["D9"] = "1,00€"

    path = bank_xlsx_with(tmp_path, mutate, filename="extdep.xlsx")
    result = bank_xlsx.parse(path)
    entry = next(e for e in result.entries if e.source_row == 9)
    assert entry.is_external_flow is None


def test_entry_id_uses_institution_account_file_and_row(tmp_path: Path) -> None:
    import hashlib

    def mutate(ws: Worksheet) -> None:
        ws["C9"] = "NOMINA EJEMPLO"
        ws["D9"] = "1,00€"

    path = bank_xlsx_with(tmp_path, mutate, filename="entryidhash.xlsx")
    result = bank_xlsx.parse(path)
    entry = next(e for e in result.entries if e.source_row == 9)
    digest_input = "\x1f".join(["bank_es", "current_account", "entryidhash.xlsx", "9"])
    expected = "sha256:" + hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    assert entry.entry_id == expected


# ---------------------------------------------------------------------------
# R-7.2: header block located by label, regardless of leading blank rows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank_rows", [0, 3, 8])
def test_t201_header_block_located_with_varying_leading_blank_rows(
    tmp_path: Path, blank_rows: int
) -> None:
    def mutate(ws: Worksheet) -> None:
        if blank_rows:
            ws.insert_rows(1, amount=blank_rows)

    path = bank_xlsx_with(tmp_path, mutate, filename=f"blanks{blank_rows}.xlsx")
    header = bank_xlsx.read_header_block(path)
    assert header.iban == FIXTURE_IBAN
    assert header.holder_name == FIXTURE_HOLDER


def test_t202_missing_cuenta_label_raises_naming_it(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["C1"] = "NoLabel"

    path = bank_xlsx_with(tmp_path, mutate, filename="nocuenta.xlsx")
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.read_header_block(path)
    err = exc_info.value
    assert err.source_file == "nocuenta.xlsx"
    assert err.source_row == 1
    assert err.column == "CUENTA"
    assert err.raw_value == "<absent>"
    assert err.expected == "a 'CUENTA' label cell somewhere in the header block"


@pytest.mark.parametrize(("cell", "label"), [("C3", "TITULAR"), ("D3", "SALDO"), ("D1", "FECHA")])
def test_t203_each_missing_header_label_raises(tmp_path: Path, cell: str, label: str) -> None:
    def mutate(ws: Worksheet) -> None:
        ws[cell] = "NoLabel"

    path = bank_xlsx_with(tmp_path, mutate, filename=f"missing_{label}.xlsx")
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.read_header_block(path)
    err = exc_info.value
    assert err.source_file == f"missing_{label}.xlsx"
    assert err.source_row == 1
    assert err.column == label
    assert err.raw_value == "<absent>"
    assert err.expected == f"a {label!r} label cell somewhere in the header block"


def test_t204_header_balance_with_trailing_iso_code_parses() -> None:
    header = bank_xlsx.read_header_block(BANK_XLSX)
    assert header.balance == Decimal("6183.75")


# ---------------------------------------------------------------------------
# R-7.4: movements table row located case/accent-insensitively
# ---------------------------------------------------------------------------


def test_t205_movements_header_located_case_and_accent_insensitively(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["A8"] = "fecha operacion"
        ws["B8"] = "fecha valor"
        ws["C8"] = "concepto"
        ws["D8"] = "importe"
        ws["E8"] = "saldo"
        ws["F8"] = "divisa"

    path = bank_xlsx_with(tmp_path, mutate, filename="lowerheader.xlsx")
    result = bank_xlsx.parse(path)
    assert len(result.entries) == 7


# ---------------------------------------------------------------------------
# R-7.5: stopping at a blank row (footer, or mid-table)
# ---------------------------------------------------------------------------


def test_t206_footer_after_table_ignored_with_warning(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        # A real footer needs a blank separator row first (R-7.5: parsing stops at the first
        # fully-blank row; content after THAT is what gets ignored) -- disclaimer text with
        # no leading blank row would instead be treated as a malformed data row.
        ws["A17"] = "Disclaimer text"

    path = bank_xlsx_with(tmp_path, mutate, filename="footer.xlsx")
    result = bank_xlsx.parse(path)
    assert len(result.entries) == 7
    matching = [w for w in result.warnings if "blank row stopped" in w.message]
    assert len(matching) == 1
    assert matching[0].message == (
        "footer.xlsx:16: blank row stopped table parsing; any content below this row "
        "(footer, disclaimer, or further data) is ignored"
    )
    assert matching[0].source_file == "footer.xlsx"
    assert matching[0].source_row == 16


def test_t207_blank_row_inside_table_stops_and_warns(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        # Insert a blank row between the 2 most recent movement rows (at row 10, pushing
        # everything below down) so there IS more data after the blank -- proving it is not
        # silently skipped past.
        ws.insert_rows(10)

    path = bank_xlsx_with(tmp_path, mutate, filename="midblank.xlsx")
    result = bank_xlsx.parse(path)
    assert len(result.entries) == 1  # only the row(s) before the blank row are kept
    matching = [w for w in result.warnings if "blank row stopped" in w.message]
    assert len(matching) == 1
    assert matching[0].source_row == 10


# ---------------------------------------------------------------------------
# R-7.7: numeric (non-string) amount/balance cells
# ---------------------------------------------------------------------------


def test_t208_numeric_amount_cell_converted_with_warning(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["D9"] = -12.40

    path = bank_xlsx_with(tmp_path, mutate, filename="numamount.xlsx")
    result = bank_xlsx.parse(path)
    entry = next(e for e in result.entries if e.source_row == 9)
    assert entry.amount_eur == Decimal(str(-12.40))
    matching = [
        w for w in result.warnings if "numeric cell" in w.message and "'Importe'" in w.message
    ]
    assert len(matching) == 1
    assert matching[0].source_row == 9


def test_t209_numeric_balance_cell_converted_with_warning(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["E9"] = 6183.75

    path = bank_xlsx_with(tmp_path, mutate, filename="numbalance.xlsx")
    result = bank_xlsx.parse(path)
    entry = next(e for e in result.entries if e.source_row == 9)
    assert entry.declared_balance == Decimal(str(6183.75))
    matching = [
        w for w in result.warnings if "numeric cell" in w.message and "'Saldo'" in w.message
    ]
    assert len(matching) == 1


# ---------------------------------------------------------------------------
# R-7.8: date cells as datetime objects
# ---------------------------------------------------------------------------


def test_t210_date_cell_as_datetime_object_parses_identically(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["A9"] = datetime_module.datetime(2027, 3, 7)
        ws["B9"] = datetime_module.date(2027, 3, 7)

    path = bank_xlsx_with(tmp_path, mutate, filename="datecell.xlsx")
    result = bank_xlsx.parse(path)
    entry = next(e for e in result.entries if e.source_row == 9)
    assert entry.date == datetime_module.date(2027, 3, 7)
    assert entry.value_date == datetime_module.date(2027, 3, 7)


# ---------------------------------------------------------------------------
# R-7.9: currency
# ---------------------------------------------------------------------------


def test_t211_non_eur_divisa_raises(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["F9"] = "USD"

    path = bank_xlsx_with(tmp_path, mutate, filename="baddivisa.xlsx")
    with pytest.raises(UnsupportedCurrencyError) as exc_info:
        bank_xlsx.parse(path)
    assert exc_info.value.source_file == "baddivisa.xlsx"
    assert exc_info.value.currency == "USD"
    assert exc_info.value.source_row == 9


def test_empty_divisa_cell_treated_as_empty_string_currency(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["F9"] = None

    path = bank_xlsx_with(tmp_path, mutate, filename="nodivisa.xlsx")
    with pytest.raises(UnsupportedCurrencyError) as exc_info:
        bank_xlsx.parse(path)
    assert exc_info.value.currency == ""


def test_malformed_fecha_operacion_integration_exact_fields(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["A9"] = "not-a-date"

    path = bank_xlsx_with(tmp_path, mutate, filename="badfechaop.xlsx")
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.parse(path)
    err = exc_info.value
    assert err.source_file == "badfechaop.xlsx"
    assert err.source_row == 9
    assert err.column == "Fecha operación"


def test_malformed_fecha_valor_integration_exact_fields(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["B9"] = "not-a-date"

    path = bank_xlsx_with(tmp_path, mutate, filename="badfechaval.xlsx")
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.parse(path)
    err = exc_info.value
    assert err.source_file == "badfechaval.xlsx"
    assert err.source_row == 9
    assert err.column == "Fecha valor"


def test_malformed_importe_integration_exact_fields(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["D9"] = "not-an-amount"

    path = bank_xlsx_with(tmp_path, mutate, filename="badimporte.xlsx")
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.parse(path)
    err = exc_info.value
    assert err.source_file == "badimporte.xlsx"
    assert err.source_row == 9
    assert err.column == "Importe"


def test_malformed_saldo_integration_exact_fields(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["E9"] = "not-an-amount"

    path = bank_xlsx_with(tmp_path, mutate, filename="badsaldo.xlsx")
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.parse(path)
    err = exc_info.value
    assert err.source_file == "badsaldo.xlsx"
    assert err.source_row == 9
    assert err.column == "Saldo"


# ---------------------------------------------------------------------------
# R-7.10: concept categorization rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("concepto", "expected"),
    [
        ("PAGO MOVIL EN FARMACIA EJEMPLO", MovementType.EXPENSE),
        ("TRANSACCION CONTACTLESS EN TIENDA EJEMPLO", MovementType.EXPENSE),
        ("COMPRA Ejemplo, TARJETA 0000", MovementType.EXPENSE),
        ("RECIBO Ejemplo S.A.", MovementType.EXPENSE),
        ("LIQUIDACION PERIODICA PRESTAMO 000", MovementType.EXPENSE),
        ("LIQUIDACION DE LAS TARJETAS DE CREDITO DEL CONTRATO 000", MovementType.EXPENSE),
        ("TRANSFERENCIA DE JUAN PEREZ", MovementType.EXTERNAL_DEPOSIT),
        ("TRANSFERENCIA A JUAN PEREZ", MovementType.EXTERNAL_WITHDRAWAL),
        ("NOMINA EJEMPLO S.A.", MovementType.PAYROLL_INCOME),
        ("ABONO NOMINA EJEMPLO S.A.", MovementType.PAYROLL_INCOME),
    ],
)
def test_t212_each_concept_rule_matches_its_canonical_example(
    tmp_path: Path, concepto: str, expected: MovementType
) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["C9"] = concepto
        ws["D9"] = "1,00€"

    path = bank_xlsx_with(tmp_path, mutate, filename="concept.xlsx")
    result = bank_xlsx.parse(path)
    entry = next(e for e in result.entries if e.source_row == 9)
    assert entry.movement_type is expected


def test_t213_rule_order_compra_wins_over_recibo_substring(tmp_path: Path) -> None:
    """A concept starting with 'COMPRA ' and containing 'TARJETA' matches rule 3 -- even
    though it also contains the word 'RECIBO' elsewhere (not as a prefix, so rule 4 could
    never fire for it under a correct implementation; this proves rule 3, checked first,
    is what actually decides it).
    """

    def mutate(ws: Worksheet) -> None:
        ws["C9"] = "COMPRA Ejemplo, RECIBO impreso disponible, TARJETA 0000"
        ws["D9"] = "-1,00€"

    path = bank_xlsx_with(tmp_path, mutate, filename="ruleorder.xlsx")
    result = bank_xlsx.parse(path)
    entry = next(e for e in result.entries if e.source_row == 9)
    assert entry.movement_type is MovementType.EXPENSE


def test_compra_without_tarjeta_does_not_match_rule_3(tmp_path: Path) -> None:
    """Rule 3 requires *both* the 'COMPRA ' prefix *and* the word 'TARJETA' -- a concept with
    only the prefix falls through to UnknownMovementError, proving the condition is `and`,
    not `or`.
    """

    def mutate(ws: Worksheet) -> None:
        ws["C9"] = "COMPRA Ejemplo sin palabra clave"
        ws["D9"] = "-1,00€"

    path = bank_xlsx_with(tmp_path, mutate, filename="compranotarjeta.xlsx")
    with pytest.raises(UnknownMovementError):
        bank_xlsx.parse(path)


def test_transferencia_a_captures_the_counterparty_name(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["C9"] = "TRANSFERENCIA A JUAN PEREZ GARCIA"
        ws["D9"] = "-1,00€"

    path = bank_xlsx_with(tmp_path, mutate, filename="transa.xlsx")
    result = bank_xlsx.parse(path)
    entry = next(e for e in result.entries if e.source_row == 9)
    assert entry.movement_type is MovementType.EXTERNAL_WITHDRAWAL
    assert entry.counterparty_name == "JUAN PEREZ GARCIA"


def test_unknown_movement_error_carries_correct_source_file(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["C9"] = "ALGO TOTALMENTE DESCONOCIDO"
        ws["D9"] = "-1,00€"

    path = bank_xlsx_with(tmp_path, mutate, filename="unknownfile.xlsx")
    with pytest.raises(UnknownMovementError) as exc_info:
        bank_xlsx.parse(path)
    assert exc_info.value.source_file == "unknownfile.xlsx"


def test_t214_transferencia_de_with_concepto_suffix_strips_it(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["C9"] = "TRANSFERENCIA DE JUAN PEREZ GARCIA, CONCEPTO regalo."
        ws["D9"] = "1,00€"

    path = bank_xlsx_with(tmp_path, mutate, filename="transdesuffix.xlsx")
    result = bank_xlsx.parse(path)
    entry = next(e for e in result.entries if e.source_row == 9)
    assert entry.movement_type is MovementType.EXTERNAL_DEPOSIT
    assert entry.counterparty_name == "JUAN PEREZ GARCIA"


def test_t215_transferencia_de_without_suffix(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["C9"] = "TRANSFERENCIA DE JUAN PEREZ GARCIA"
        ws["D9"] = "1,00€"

    path = bank_xlsx_with(tmp_path, mutate, filename="transdenosuffix.xlsx")
    result = bank_xlsx.parse(path)
    entry = next(e for e in result.entries if e.source_row == 9)
    assert entry.counterparty_name == "JUAN PEREZ GARCIA"


@pytest.mark.parametrize("concepto", ["Recibo Ejemplo S.A.", "RECÍBO Ejemplo S.A."])
def test_t216_accent_and_case_insensitive_concept_matching(tmp_path: Path, concepto: str) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["C9"] = concepto
        ws["D9"] = "-1,00€"

    path = bank_xlsx_with(tmp_path, mutate, filename="accentcase.xlsx")
    result = bank_xlsx.parse(path)
    entry = next(e for e in result.entries if e.source_row == 9)
    assert entry.movement_type is MovementType.EXPENSE


@pytest.mark.parametrize("amount", ["-1,00€", "1,00€"])
def test_t217_t218_unmatched_concept_raises_regardless_of_sign(tmp_path: Path, amount: str) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["C9"] = "UN CONCEPTO TOTALMENTE DESCONOCIDO"
        ws["D9"] = amount

    path = bank_xlsx_with(tmp_path, mutate, filename="unknownconcept.xlsx")
    with pytest.raises(UnknownMovementError) as exc_info:
        bank_xlsx.parse(path)
    assert exc_info.value.source_row == 9
    assert exc_info.value.observed == "UN CONCEPTO TOTALMENTE DESCONOCIDO"


# ---------------------------------------------------------------------------
# R-7.14: date = Fecha operación, value_date = Fecha valor
# ---------------------------------------------------------------------------


def test_t219_date_and_value_date_come_from_different_columns() -> None:
    result = bank_xlsx.parse(BANK_XLSX)
    # source_row 12: Fecha operación 04/03, Fecha valor 03/03 -- genuinely different.
    entry = next(e for e in result.entries if e.source_row == 12)
    assert entry.date == datetime_module.date(2027, 3, 4)
    assert entry.value_date == datetime_module.date(2027, 3, 3)


# ---------------------------------------------------------------------------
# R-7.15 / R-1.22: newest-first source re-sorted (using distinct dates -- see Q-F for the
# one same-date pair in the canonical fixture, which is a separate, open question).
# ---------------------------------------------------------------------------


def test_t220_newest_first_source_order_is_resorted(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        # Overwrite the movement rows with 3 distinctly-dated rows, still newest-first, and
        # confirm the parser returns them oldest-first.
        ws["A9"], ws["B9"] = "03/03/2027", "03/03/2027"
        ws["A10"], ws["B10"] = "02/03/2027", "02/03/2027"
        ws["A11"], ws["B11"] = "01/03/2027", "01/03/2027"
        for row in (9, 10, 11):
            ws[f"C{row}"] = "NOMINA EJEMPLO"
            ws[f"D{row}"] = "1,00€"
            ws[f"E{row}"] = "1,00€"
            ws[f"F{row}"] = "EUR"
        ws.delete_rows(12, amount=3)  # drop rows 12-14 so only 9,10,11,15 remain as data

    path = bank_xlsx_with(tmp_path, mutate, filename="resort.xlsx")
    result = bank_xlsx.parse(path)
    dates = [e.date for e in result.entries]
    assert dates == sorted(dates)
    assert dates[0] == datetime_module.date(2027, 3, 1)
    assert dates[-1] == datetime_module.date(2027, 3, 3)


def _reconciliation_discrepancies(
    ordered: list[bank_xlsx.LedgerEntry],
) -> list[tuple[int, Decimal, Decimal]]:
    """Replicates R-8.2's formula directly (reconciliation.py does not exist yet at this
    patch stage): for each consecutive pair, `previous.declared_balance + current.
    cash_effect_eur` must equal `current.declared_balance`. Returns
    `(source_row, expected, declared)` for every row that disagrees.
    """
    problems: list[tuple[int, Decimal, Decimal]] = []
    previous = None
    for entry in ordered:
        if previous is not None and previous.declared_balance is not None:
            expected = previous.declared_balance + entry.cash_effect_eur
            if entry.declared_balance is not None and expected != entry.declared_balance:
                problems.append((entry.source_row, expected, entry.declared_balance))
        previous = entry
    return problems


def test_t358_file_sequence_tiebreak_reconciles_raw_source_row_tiebreak_does_not() -> None:
    """§12.1 (revised): rows 9 and 10 share `Fecha operación` (07/03/2027). Sorting the tie by
    ascending `file_sequence` (row 10 before row 9, since file_sequence = -source_row puts
    -10 before -9) is what this adapter's own output already does, and it reconciles exactly.
    Sorting the same tie by raw ascending `source_row` instead (the pre-fix R-1.22 wording)
    does not -- off by exactly 64.20 at that one step, per the spec's own hand-verified
    numbers, which is itself the required regression test.
    """
    result = bank_xlsx.parse(BANK_XLSX)
    entries = result.entries  # already sorted by (date, file_sequence) -- R-7.15/R-1.22
    assert _reconciliation_discrepancies(entries) == []

    by_date_then_raw_source_row = sorted(entries, key=lambda e: (e.date, e.source_row))
    problems = _reconciliation_discrepancies(by_date_then_raw_source_row)
    # Swapping the tied pair breaks the chain at row 9 (checked right after row 11 in this
    # wrong order) by exactly 64.20 -- the spec's own hand-verified figure -- and that error
    # then cascades into row 10's check too, since it now follows row 9's (wrong) position.
    assert [row for row, _expected, _declared in problems] == [9, 10]
    row, expected, declared = problems[0]
    assert row == 9
    assert expected - declared == Decimal("64.20")


def test_t359_three_row_same_date_and_same_value_date_tie_resolves_by_file_sequence(
    tmp_path: Path,
) -> None:
    """R-1.22's rationale / docs/technical-decisions.md §4: a real production export had
    three rows sharing **both** `Fecha operación` and `Fecha valor` on one day, which a
    `value_date` tiebreak cannot resolve. Built here as a `tests/builders.py` mutation of the
    canonical fixture (TD-3), not a new canonical fixture file. Hand-verified numbers per the
    spec's own rationale: baseline 1184.31, then -0.73 -> 1183.58, then -17.04 -> 1166.54,
    then -34.70 -> 1131.84 -- the exact reverse of the rows' physical (newest-first) position.
    """

    def mutate(ws: Worksheet) -> None:
        ws.delete_rows(9, amount=7)  # drop all 7 canonical data rows
        # Physically newest-first: row 9 (closest to the header) is the most recent of the
        # tied trio; row 12 is the untied baseline, chronologically before all three.
        rows = [
            (9, "05/09/2027", "05/09/2027", "RECIBO EJEMPLO A", "-34,70€", "1.131,84€"),
            (10, "05/09/2027", "05/09/2027", "RECIBO EJEMPLO B", "-17,04€", "1.166,54€"),
            (11, "05/09/2027", "05/09/2027", "RECIBO EJEMPLO C", "-0,73€", "1.183,58€"),
            (12, "01/09/2027", "01/09/2027", "NOMINA EJEMPLO", "500,00€", "1.184,31€"),
        ]
        for row, fecha_op, fecha_valor, concepto, importe, saldo in rows:
            ws[f"A{row}"] = fecha_op
            ws[f"B{row}"] = fecha_valor
            ws[f"C{row}"] = concepto
            ws[f"D{row}"] = importe
            ws[f"E{row}"] = saldo
            ws[f"F{row}"] = "EUR"

    path = bank_xlsx_with(tmp_path, mutate, filename="triple_tie.xlsx")
    result = bank_xlsx.parse(path)
    assert len(result.entries) == 4

    # Ascending file_sequence order: baseline (row 12), then C, B, A (rows 11, 10, 9) -- the
    # exact reverse of physical/source_row order for the tied trio.
    assert [e.source_row for e in result.entries] == [12, 11, 10, 9]
    assert [e.declared_balance for e in result.entries] == [
        Decimal("1184.31"),
        Decimal("1183.58"),
        Decimal("1166.54"),
        Decimal("1131.84"),
    ]
    assert _reconciliation_discrepancies(result.entries) == []

    # The superseded proposal from Q-F's original analysis -- breaking a same-date tie with
    # value_date -- ties too on this trio (they share both fields) and falls through to
    # source_row, reproducing the file's own (wrong) physical order. Confirms file_sequence
    # is doing real work here, not redundant with date/value_date alone.
    by_date_value_date_source_row = sorted(
        result.entries, key=lambda e: (e.date, e.value_date, e.source_row)
    )
    assert [e.source_row for e in by_date_value_date_source_row] == [12, 9, 10, 11]
    assert _reconciliation_discrepancies(by_date_value_date_source_row) != []


def test_t361_file_sequence_equals_negated_source_row_for_every_entry() -> None:
    """R-7.5a: this export lists rows newest-first, so file_sequence = -source_row."""
    result = bank_xlsx.parse(BANK_XLSX)
    assert len(result.entries) > 0
    for entry in result.entries:
        assert entry.file_sequence == -entry.source_row


# ---------------------------------------------------------------------------
# R-7.16: account declaration
# ---------------------------------------------------------------------------


def test_t221_account_declaration_carries_normalized_iban_holder_export_date() -> None:
    result = bank_xlsx.parse(BANK_XLSX)
    assert len(result.accounts) == 1
    decl = result.accounts[0]
    assert decl.institution == "bank_es"
    assert decl.iban_or_account == FIXTURE_IBAN
    assert decl.holder_name == FIXTURE_HOLDER
    assert decl.declared_in_file == "banco_ejemplo.xlsx"
    assert decl.as_of_date == datetime_module.date(2027, 3, 10)


# ---------------------------------------------------------------------------
# R-7.1: a second worksheet is ignored, with a warning
# ---------------------------------------------------------------------------


def test_t222_second_worksheet_ignored_with_warning(tmp_path: Path) -> None:
    wb = openpyxl.load_workbook(BANK_XLSX)
    wb.create_sheet("Extra")
    dest = tmp_path / "twosheets.xlsx"
    wb.save(dest)
    result = bank_xlsx.parse(dest)
    assert len(result.entries) == 7
    matching = [w for w in result.warnings if "2 sheets" in w.message]
    assert len(matching) == 1
    assert matching[0].message == "twosheets.xlsx: workbook has 2 sheets; only the first is read"
    assert matching[0].source_file == "twosheets.xlsx"
    assert matching[0].source_row is None


def test_three_worksheets_reports_the_correct_count(tmp_path: Path) -> None:
    wb = openpyxl.load_workbook(BANK_XLSX)
    wb.create_sheet("Extra1")
    wb.create_sheet("Extra2")
    dest = tmp_path / "threesheets.xlsx"
    wb.save(dest)
    result = bank_xlsx.parse(dest)
    matching = [w for w in result.warnings if "sheets" in w.message]
    assert len(matching) == 1
    assert "3 sheets" in matching[0].message


# ---------------------------------------------------------------------------
# R-7.13: bank rows carry no counterparty IBAN
# ---------------------------------------------------------------------------


def test_t223_bank_entries_have_no_counterparty_iban() -> None:
    result = bank_xlsx.parse(BANK_XLSX)
    assert all(e.counterparty_iban is None for e in result.entries)


# ---------------------------------------------------------------------------
# Defensive / low-level unit tests closing branches a full-file round trip through openpyxl
# cannot reach (openpyxl always reads a written `date` back as `datetime`, never a bare
# `date`; and a value cannot be *both* a real file cell and a raw Python object outside a
# cell at once).
# ---------------------------------------------------------------------------


def test_parse_header_date_splits_on_pipe_not_whitespace_first_occurrence_maxsplit_1() -> None:
    # Multiple '|' characters and internal whitespace distinguish split("|", 1) from
    # split(None, 1) (whitespace-split) and from rsplit / a different maxsplit.
    result = bank_xlsx._parse_header_date("10/03/2027 | 09:00:00 | extra", source_file="f.xlsx")
    assert result == datetime_module.date(2027, 3, 10)


def test_parse_header_date_splits_on_pipe_even_with_no_surrounding_whitespace() -> None:
    # With a space before the '|', the first whitespace-delimited token happens to equal
    # the first '|'-delimited token, so that alone cannot distinguish split("|", 1) from
    # split(None, 1) (whitespace-split). Omitting the space forces the two to diverge:
    # split(None, 1) sees no whitespace at all and returns the whole string as one token,
    # which would fail to parse as a date -- proving the code really splits on '|'.
    result = bank_xlsx._parse_header_date("10/03/2027|09:00:00", source_file="f.xlsx")
    assert result == datetime_module.date(2027, 3, 10)


def test_parse_header_date_exact_error_fields() -> None:
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx._parse_header_date("not-a-date | 09:00:00", source_file="f.xlsx")
    err = exc_info.value
    assert err.source_file == "f.xlsx"
    assert err.source_row == 1
    assert err.column == "FECHA"


def test_read_header_block_source_file_on_malformed_date(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["D2"] = "not-a-date | 09:00:00"

    path = bank_xlsx_with(tmp_path, mutate, filename="badheaderdate.xlsx")
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.read_header_block(path)
    assert exc_info.value.source_file == "badheaderdate.xlsx"


def test_read_header_block_source_file_on_malformed_balance(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["D4"] = "not-an-amount"

    path = bank_xlsx_with(tmp_path, mutate, filename="badheaderbalance.xlsx")
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.read_header_block(path)
    assert exc_info.value.source_file == "badheaderbalance.xlsx"
    assert exc_info.value.source_row == 1
    assert exc_info.value.column == "SALDO"


def test_read_header_block_data_only_true_is_passed_to_the_loader(tmp_path: Path) -> None:
    """Same reasoning as the `parse()`-level version: a formula cell in the header balance
    position distinguishes `data_only=True` from `False`/omitted (R-7.3/R-7.7).
    """

    def mutate(ws: Worksheet) -> None:
        ws["D4"] = "=1+1"

    path = bank_xlsx_with(tmp_path, mutate, filename="headerformula.xlsx")
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.read_header_block(path)
    assert exc_info.value.raw_value == "<absent>"


def test_data_only_true_is_actually_passed_to_the_loader(tmp_path: Path) -> None:
    """Proves `parse()` opens the workbook with `data_only=True`, not `False` or omitted.

    openpyxl never evaluates formulas itself: a formula cell written by openpyxl and then
    re-read with `data_only=True` has no cached value at all (``None``), while
    `data_only=False` (or omitted, its default) returns the literal formula text instead.
    Putting a formula in the Importe cell makes these two behaviours produce distinguishable
    `ParseError`s: `None` reaches the "value is None" branch (`raw_value=""`), while the
    formula text reaches `parse_spanish_amount` instead (`raw_value="=1+1"`).
    """

    def mutate(ws: Worksheet) -> None:
        ws["D9"] = "=1+1"

    path = bank_xlsx_with(tmp_path, mutate, filename="formula.xlsx")
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.parse(path)
    assert exc_info.value.raw_value == ""


def test_cell_to_amount_string_path_propagates_context_exactly() -> None:
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx._cell_to_amount(
            "not-an-amount", source_file="f.xlsx", source_row=7, column="Importe"
        )
    err = exc_info.value
    assert err.source_file == "f.xlsx"
    assert err.source_row == 7
    assert err.column == "Importe"


def test_cell_to_amount_none_value_exact_fields() -> None:
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx._cell_to_amount(None, source_file="f.xlsx", source_row=7, column="Importe")
    err = exc_info.value
    assert err.source_file == "f.xlsx"
    assert err.source_row == 7
    assert err.column == "Importe"
    assert err.raw_value == ""
    assert err.expected == "a Spanish amount string or a numeric cell"


def test_cell_to_amount_numeric_warning_exact_fields() -> None:
    value, warning = bank_xlsx._cell_to_amount(
        12.5, source_file="f.xlsx", source_row=7, column="Importe"
    )
    assert value == Decimal(str(12.5))
    assert warning is not None
    assert warning.message == (
        "f.xlsx:7: column 'Importe' is a numeric cell, not text; its exactness cannot be "
        "verified against the source document"
    )
    assert warning.source_file == "f.xlsx"
    assert warning.source_row == 7


def test_cell_to_date_string_path_propagates_context_exactly() -> None:
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx._cell_to_date(
            "not-a-date", source_file="f.xlsx", source_row=7, column="Fecha operación"
        )
    err = exc_info.value
    assert err.source_file == "f.xlsx"
    assert err.source_row == 7
    assert err.column == "Fecha operación"


def test_cell_to_date_accepts_a_bare_date_object() -> None:
    result = bank_xlsx._cell_to_date(
        datetime_module.date(2027, 3, 7), source_file="f.xlsx", source_row=9, column="Fecha"
    )
    assert result == datetime_module.date(2027, 3, 7)


def test_cell_to_date_rejects_an_unrecognized_type() -> None:
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx._cell_to_date(12345, source_file="f.xlsx", source_row=9, column="Fecha")
    err = exc_info.value
    assert err.source_file == "f.xlsx"
    assert err.source_row == 9
    assert err.column == "Fecha"
    assert err.raw_value == "12345"
    assert err.expected == "a DD/MM/YYYY date string or a date/datetime cell"


def test_find_label_value_falls_back_to_the_right_candidate(tmp_path: Path) -> None:
    """When the cell directly below and below-right of a label are both empty, the search
    falls back to the cell immediately to the label's right.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "CUENTA"
    ws["B1"] = "ES00 SOME VALUE"
    # Leave A2 and B2 (below / below-right) empty.
    dest = tmp_path / "rightfallback.xlsx"
    wb.save(dest)
    wb2 = openpyxl.load_workbook(dest, data_only=True)
    value = bank_xlsx._find_label_value(
        wb2.active, "CUENTA", source_file="rightfallback.xlsx", max_row=5
    )
    assert value == "ES00 SOME VALUE"


def test_find_label_value_matched_label_with_all_candidates_empty_raises(
    tmp_path: Path,
) -> None:
    """The label cell itself is found, but its below/below-right/right neighbours are all
    empty -- the search must keep looking (in case the same label text appears again) and
    ultimately raise, not return an empty string.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "CUENTA"
    # A2, B1, B2 all left empty: no candidate has a value.
    dest = tmp_path / "emptycandidates.xlsx"
    wb.save(dest)
    wb2 = openpyxl.load_workbook(dest, data_only=True)
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx._find_label_value(
            wb2.active, "CUENTA", source_file="emptycandidates.xlsx", max_row=5
        )
    err = exc_info.value
    assert err.source_file == "emptycandidates.xlsx"
    assert err.source_row == 1
    assert err.column == "CUENTA"
    assert err.raw_value == "<absent>"
    assert err.expected == "a 'CUENTA' label cell somewhere in the header block"


def test_find_label_value_falls_back_to_below_right_candidate(tmp_path: Path) -> None:
    """When the cell directly below is empty but below-right has a value, that is used."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "CUENTA"
    ws["B2"] = "value at below-right"
    dest = tmp_path / "belowright.xlsx"
    wb.save(dest)
    wb2 = openpyxl.load_workbook(dest, data_only=True)
    value = bank_xlsx._find_label_value(
        wb2.active, "CUENTA", source_file="belowright.xlsx", max_row=5
    )
    assert value == "value at below-right"


def test_find_label_value_skips_an_empty_string_candidate() -> None:
    """An empty-string candidate (as opposed to a genuinely empty/None cell) must also be
    skipped in favour of the next candidate, not returned as-is.

    This must be checked on the in-memory workbook, never after a save/reload round trip:
    openpyxl does not preserve a genuinely empty-string cell value through
    ``save``/``load_workbook`` (it comes back as ``None``), which would silently collapse
    this test into the already-covered ``None`` case instead of the ``""`` case it targets.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "CUENTA"
    ws["A2"] = ""  # below: empty string, not None
    ws["B2"] = "value at below-right"
    assert ws["A2"].value == ""
    value = bank_xlsx._find_label_value(ws, "CUENTA", source_file="emptystring.xlsx", max_row=5)
    assert value == "value at below-right"


def test_find_label_value_max_row_excludes_a_duplicate_label_beyond_it(
    tmp_path: Path,
) -> None:
    """`max_row` scoping matters even for labels with no natural duplicate in the real
    fixture (CUENTA/TITULAR/FECHA): construct one explicitly and confirm the out-of-scope
    duplicate is never used.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "TITULAR"
    ws["A2"] = "REAL HOLDER"
    ws["A10"] = "TITULAR"  # a false, out-of-scope duplicate further down
    ws["A11"] = "WRONG HOLDER"
    dest = tmp_path / "duplicatelabel.xlsx"
    wb.save(dest)
    wb2 = openpyxl.load_workbook(dest, data_only=True)
    value = bank_xlsx._find_label_value(
        wb2.active, "TITULAR", source_file="duplicatelabel.xlsx", max_row=5
    )
    assert value == "REAL HOLDER"


def test_read_header_block_boundary_is_exactly_movements_row_minus_1(
    tmp_path: Path,
) -> None:
    """The header-block search boundary is `movements_header_row - 1`, not `- 2`: a label
    placed exactly on that boundary row (with its value to the right, same row, so no extra
    row is needed) must still be found.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "CUENTA"
    ws["A2"] = "ES00 SOME IBAN"
    ws["A3"] = "TITULAR"
    ws["A4"] = "SOME HOLDER"
    ws["A5"] = "SALDO"
    ws["A6"] = "1,00€"
    # FECHA sits exactly on row 7 = movements_header_row(8) - 1: the boundary itself. Placed
    # in column H (outside the movements table's columns A-F) so its "below" and
    # "below-right" candidates (row 8, cols H/I) are genuinely empty and the search falls
    # through to "right" (same row, next column) rather than accidentally picking up the
    # movements table's own header text sitting below columns A-F.
    ws["H7"] = "FECHA"
    ws["I7"] = "01/01/2027"
    ws.append(["Fecha operación", "Fecha valor", "Concepto", "Importe", "Saldo", "Divisa"])
    dest = tmp_path / "boundary.xlsx"
    wb.save(dest)
    header = bank_xlsx.read_header_block(dest)
    assert header.export_date == datetime_module.date(2027, 1, 1)


def test_read_header_block_cuenta_lookup_is_bounded_to_the_header_block(
    tmp_path: Path,
) -> None:
    """Each of the four header-block lookups in `_read_header_block_from_sheet` must pass
    its own `max_row=boundary`, not search the whole sheet: construct a CUENTA label whose
    in-block candidates are all blank, plus a duplicate CUENTA label (with a real value)
    placed well beyond the boundary. A correctly bounded search never sees the duplicate and
    must raise; a search that (by a coding slip) searched unboundedly would find the
    duplicate and silently return its value instead.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "CUENTA"
    # A2 (below), B2 (below-right) and B1 (right) are all left blank: none of the three
    # candidates for this in-block match resolves to a value.
    ws["A3"] = "TITULAR"
    ws["A4"] = "REAL HOLDER"
    ws["A5"] = "SALDO"
    ws["A6"] = "1,00€"
    ws["A7"] = "FECHA"
    ws["A8"] = "01/01/2027"
    for col, text in enumerate(
        ["Fecha operación", "Fecha valor", "Concepto", "Importe", "Saldo", "Divisa"], start=1
    ):
        ws.cell(row=10, column=col, value=text)
    # A duplicate CUENTA label, far beyond the boundary, with a real value below it.
    ws["A20"] = "CUENTA"
    ws["A21"] = "ES00 OUT OF SCOPE DUPLICATE"
    dest = tmp_path / "cuenta_unbounded.xlsx"
    wb.save(dest)
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.read_header_block(dest)
    err = exc_info.value
    assert err.column == "CUENTA"
    assert err.raw_value == "<absent>"


def test_read_header_block_titular_lookup_is_bounded_to_the_header_block(
    tmp_path: Path,
) -> None:
    """Same as the CUENTA case above, for the TITULAR lookup."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "CUENTA"
    ws["A2"] = "ES00 REAL IBAN"
    ws["A3"] = "TITULAR"
    # A4 (below), B4 (below-right) and B3 (right) are all left blank.
    ws["A5"] = "SALDO"
    ws["A6"] = "1,00€"
    ws["A7"] = "FECHA"
    ws["A8"] = "01/01/2027"
    for col, text in enumerate(
        ["Fecha operación", "Fecha valor", "Concepto", "Importe", "Saldo", "Divisa"], start=1
    ):
        ws.cell(row=10, column=col, value=text)
    ws["A20"] = "TITULAR"
    ws["A21"] = "OUT OF SCOPE DUPLICATE HOLDER"
    dest = tmp_path / "titular_unbounded.xlsx"
    wb.save(dest)
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.read_header_block(dest)
    err = exc_info.value
    assert err.column == "TITULAR"
    assert err.raw_value == "<absent>"


def test_read_header_block_fecha_lookup_is_bounded_to_the_header_block(
    tmp_path: Path,
) -> None:
    """Same as the CUENTA case above, for the FECHA lookup."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "CUENTA"
    ws["A2"] = "ES00 REAL IBAN"
    ws["A3"] = "TITULAR"
    ws["A4"] = "REAL HOLDER"
    ws["A5"] = "SALDO"
    ws["A6"] = "1,00€"
    ws["A7"] = "FECHA"
    # A8 (below), B8 (below-right) and B7 (right) are all left blank.
    for col, text in enumerate(
        ["Fecha operación", "Fecha valor", "Concepto", "Importe", "Saldo", "Divisa"], start=1
    ):
        ws.cell(row=10, column=col, value=text)
    ws["A20"] = "FECHA"
    ws["A21"] = "01/01/2027"
    dest = tmp_path / "fecha_unbounded.xlsx"
    wb.save(dest)
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.read_header_block(dest)
    err = exc_info.value
    assert err.column == "FECHA"
    assert err.raw_value == "<absent>"


def test_normalize_label_none_is_empty_string() -> None:
    assert bank_xlsx._normalize_label(None) == ""


def test_is_blank_cells_whitespace_only_string_counts_as_blank() -> None:
    assert bank_xlsx._is_blank_cells(["   ", None]) is True
    assert bank_xlsx._is_blank_cells(["x", None]) is False


def test_find_movements_header_row_exact_fields_on_failure(tmp_path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "nothing here"
    dest = tmp_path / "nomovementsrow.xlsx"
    wb.save(dest)
    wb2 = openpyxl.load_workbook(dest, data_only=True)
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx._find_movements_header_row(wb2.active, source_file="nomovementsrow.xlsx")
    err = exc_info.value
    assert err.source_file == "nomovementsrow.xlsx"
    assert err.source_row == 1
    assert err.column == "<movements header>"
    assert err.raw_value == "<absent>"
    assert err.expected == (
        "a row with 'Fecha operación, Fecha valor, Concepto, Importe, Saldo, Divisa'"
    )


def test_find_movements_header_row_only_checks_first_6_columns(tmp_path: Path) -> None:
    """A 7th column's content must not prevent a match on the first 6."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Fecha operación", "Fecha valor", "Concepto", "Importe", "Saldo", "Divisa", "Extra"])
    dest = tmp_path / "extracol.xlsx"
    wb.save(dest)
    wb2 = openpyxl.load_workbook(dest, data_only=True)
    assert bank_xlsx._find_movements_header_row(wb2.active, source_file="extracol.xlsx") == 1


def test_read_header_block_from_sheet_returns_the_movements_row_index() -> None:
    wb = openpyxl.load_workbook(BANK_XLSX, data_only=True)
    header, movements_row = bank_xlsx._read_header_block_from_sheet(
        wb.active, source_file="banco_ejemplo.xlsx"
    )
    assert movements_row == 8
    assert header.iban == FIXTURE_IBAN


def test_find_movements_header_row_missing_raises(tmp_path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "not a movements table at all"
    dest = tmp_path / "nomovements.xlsx"
    wb.save(dest)
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.read_header_block(dest)
    err = exc_info.value
    assert err.source_file == "nomovements.xlsx"
    assert err.source_row == 1
    assert err.column == "<movements header>"
    assert err.raw_value == "<absent>"
    assert err.expected == (
        "a row with 'Fecha operación, Fecha valor, Concepto, Importe, Saldo, Divisa'"
    )


def test_parse_missing_movements_header_raises_with_correct_source_file(
    tmp_path: Path,
) -> None:
    def mutate(ws: Worksheet) -> None:
        for row in range(8, 16):
            for col in range(1, 7):
                ws.cell(row=row, column=col).value = None

    path = bank_xlsx_with(tmp_path, mutate, filename="nomovements2.xlsx")
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.parse(path)
    assert exc_info.value.source_file == "nomovements2.xlsx"


def test_empty_concepto_cell_raises_parse_error(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["C9"] = None

    path = bank_xlsx_with(tmp_path, mutate, filename="noconcepto.xlsx")
    with pytest.raises(ParseError) as exc_info:
        bank_xlsx.parse(path)
    err = exc_info.value
    assert err.source_file == "noconcepto.xlsx"
    assert err.source_row == 9
    assert err.column == "Concepto"
    assert err.raw_value == ""
    assert err.expected == "a non-empty Concepto string"
