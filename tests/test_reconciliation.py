"""Tests for fina.reconciliation (WP-7): R-8.1..R-8.5.

T-358, T-359, T-360, T-361 (the file_sequence-specific regressions added alongside the Q-F
fix) live in tests/test_bank_xlsx.py and tests/test_broker_csv.py: they test the *adapters'*
file_sequence assignment (R-6.1a/R-7.5a) and internal sort order, not reconciliation.py's own
logic, which is what this file covers (T-350..T-357).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl.worksheet.worksheet import Worksheet

from builders import BANK_XLSX, BROKER_CSV, bank_xlsx_with
from fina.adapters import bank_xlsx, broker_csv
from fina.errors import ReconciliationError
from fina.models import LedgerEntry, MovementType, Warning
from fina.reconciliation import reconcile

SOURCE_FILE = "banco_ejemplo.xlsx"


def make_entry(**overrides: object) -> LedgerEntry:
    defaults: dict[str, object] = {
        "entry_id": "e1",
        "date": date(2027, 3, 1),
        "value_date": None,
        "source_timestamp": None,
        "institution": "bank_es",
        "account": "current_account",
        "movement_type": MovementType.EXPENSE,
        "asset": None,
        "asset_class": None,
        "quantity": None,
        "unit_price": None,
        "currency": "EUR",
        "amount_eur": Decimal("-10.00"),
        "fee_eur": None,
        "tax_eur": None,
        "original_amount": None,
        "original_currency": None,
        "fx_rate": None,
        "cash_effect_eur": Decimal("-10.00"),
        "declared_balance": Decimal("90.00"),
        "counterparty_name": None,
        "counterparty_iban": None,
        "is_external_flow": True,
        "status": "actual",
        "source_file": SOURCE_FILE,
        "source_row": 9,
        "file_sequence": -9,
        "raw": {"Importe": "-10,00€"},
    }
    defaults.update(overrides)
    return LedgerEntry(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# R-8.1/R-8.2/R-8.5: the canonical bank fixture, end to end
# ---------------------------------------------------------------------------


def test_t350_bank_fixture_reconciles_with_zero_discrepancy() -> None:
    """§12.1: reconciliation passes with zero discrepancy on every row, and the final
    declared balance equals the header balance (R-8.5), all in one pass.
    """
    result = bank_xlsx.parse(BANK_XLSX)
    header = bank_xlsx.read_header_block(BANK_XLSX)
    warnings = reconcile(
        result.entries,
        header_balances={("bank_es", "banco_ejemplo.xlsx"): header.balance},
    )
    assert warnings == ()


def test_t351_one_cent_corrupted_raises_with_exact_fields(tmp_path: Path) -> None:
    def mutate(ws: Worksheet) -> None:
        ws["E10"] = "6.196,16€"  # was 6.196,15€: declared balance off by exactly one cent

    path = bank_xlsx_with(tmp_path, mutate, filename="corrupted.xlsx")
    result = bank_xlsx.parse(path)
    with pytest.raises(ReconciliationError) as exc_info:
        reconcile(result.entries)
    err = exc_info.value
    assert err.source_file == "corrupted.xlsx"
    assert err.source_row == 10
    assert err.expected == Decimal("6196.15")
    assert err.declared == Decimal("6196.16")
    assert err.delta == Decimal("-0.01")


def test_t352_wrong_file_sequence_on_a_tied_pair_breaks_reconciliation() -> None:
    """Ordering is load-bearing: if the two same-`Fecha operación` rows (07/03/2027) were
    assigned file_sequence in the wrong relative order, reconciliation must fail -- this is
    exactly the failure R-7.5a's `file_sequence = -source_row` rule prevents.
    """
    result = bank_xlsx.parse(BANK_XLSX)
    entries = list(result.entries)
    tied = [e for e in entries if e.date == date(2027, 3, 7)]
    assert len(tied) == 2
    a, b = tied
    swapped = [
        replace(e, file_sequence=b.file_sequence)
        if e is a
        else replace(e, file_sequence=a.file_sequence)
        if e is b
        else e
        for e in entries
    ]
    with pytest.raises(ReconciliationError):
        reconcile(swapped)


# ---------------------------------------------------------------------------
# R-8.3: baseline row is not itself checked
# ---------------------------------------------------------------------------


def test_t353_first_declared_balance_row_is_not_checked() -> None:
    """The baseline's own declared_balance is never verified against anything -- an
    "impossible" value (not derivable from any prior state, since none is claimed) must not
    raise, as long as every row *after* it is internally consistent.
    """
    baseline = make_entry(
        entry_id="e1",
        date=date(2027, 3, 1),
        cash_effect_eur=Decimal("1.00"),
        declared_balance=Decimal("999999.99"),  # unverifiable and, on its own, implausible
        source_row=9,
        file_sequence=-9,
    )
    second = make_entry(
        entry_id="e2",
        date=date(2027, 3, 2),
        cash_effect_eur=Decimal("-10.00"),
        declared_balance=Decimal("999989.99"),  # consistent with the baseline above
        source_row=10,
        file_sequence=-10,
    )
    warnings = reconcile([baseline, second])
    assert warnings == ()


# ---------------------------------------------------------------------------
# R-8.4: accounts with no declared balance at all
# ---------------------------------------------------------------------------


def test_t354_broker_account_has_no_error_and_an_unverified_warning() -> None:
    """The broker export has no running-balance column at all -- neither the `cash` nor the
    `positions` sub-account carries a declared_balance -- so both are unverified, and
    `reconcile` must raise nothing.
    """
    result = broker_csv.parse(BROKER_CSV)
    warnings = reconcile(result.entries)
    assert len(warnings) == 2
    by_account = {w.message.split(":", 1)[0]: w for w in warnings}
    assert set(by_account) == {"trade_republic/cash", "trade_republic/positions"}
    for account_key, warning in by_account.items():
        assert isinstance(warning, Warning)
        assert warning.message == (
            f"{account_key}: computed balance is unverified against any source-declared balance"
        )
        assert warning.source_file == "broker_ejemplo.csv"
        assert warning.source_row is None


def test_t355_exactly_one_warning_per_institution_account_not_per_row() -> None:
    result = broker_csv.parse(BROKER_CSV)
    cash_rows = [e for e in result.entries if e.account == "cash"]
    assert len(cash_rows) > 1  # many rows share (institution, account) = (trade_republic, cash)
    warnings = reconcile(result.entries)
    # One warning per distinct (institution, account) group, not one per row: 18 broker rows
    # collapse to exactly 2 warnings (cash, positions), never 18.
    accounts_warned = {(e.institution, e.account) for e in result.entries}
    assert len(warnings) == len(accounts_warned) == 2


def test_two_distinct_no_balance_accounts_each_get_their_own_warning() -> None:
    cash = make_entry(
        entry_id="c1",
        institution="trade_republic",
        account="cash",
        declared_balance=None,
        source_row=2,
        file_sequence=2,
    )
    positions = make_entry(
        entry_id="p1",
        institution="trade_republic",
        account="positions",
        movement_type=MovementType.BUY,
        amount_eur=Decimal("-100.00"),
        quantity=Decimal("1"),
        cash_effect_eur=Decimal("-100.00"),
        declared_balance=None,
        is_external_flow=None,
        source_row=3,
        file_sequence=3,
    )
    warnings = reconcile([cash, positions])
    assert len(warnings) == 2
    accounts_warned = {w.message for w in warnings}
    assert len(accounts_warned) == 2


# ---------------------------------------------------------------------------
# R-8.5: header-balance same-file cross-check
# ---------------------------------------------------------------------------


def test_t356_final_balance_vs_header_balance_mismatch_raises() -> None:
    result = bank_xlsx.parse(BANK_XLSX)
    with pytest.raises(ReconciliationError) as exc_info:
        reconcile(
            result.entries,
            header_balances={("bank_es", "banco_ejemplo.xlsx"): Decimal("1.00")},
        )
    err = exc_info.value
    assert err.source_file == "banco_ejemplo.xlsx"
    assert err.source_row == 9  # the final declared-balance row, per §12.1
    assert err.expected == Decimal("1.00")
    assert err.declared == Decimal("6183.75")
    assert err.delta == Decimal("1.00") - Decimal("6183.75")


def test_header_balance_check_is_skipped_when_not_supplied() -> None:
    """No entry in `header_balances` for this file -> R-8.5's check is simply not attempted;
    R-8.2/R-8.3 still run.
    """
    result = bank_xlsx.parse(BANK_XLSX)
    warnings = reconcile(result.entries, header_balances={})
    assert warnings == ()
    warnings_none = reconcile(result.entries, header_balances=None)
    assert warnings_none == ()


# ---------------------------------------------------------------------------
# R-8.2/R-1.5: exact Decimal equality, no epsilon
# ---------------------------------------------------------------------------


def test_t357_a_sub_cent_difference_still_raises() -> None:
    baseline = make_entry(
        entry_id="e1", date=date(2027, 3, 1), declared_balance=Decimal("100.000"), source_row=9
    )
    off_by_a_fraction_of_a_cent = make_entry(
        entry_id="e2",
        date=date(2027, 3, 2),
        cash_effect_eur=Decimal("-10.00"),
        declared_balance=Decimal("90.001"),  # exact chain result would be 90.000
        source_row=10,
        file_sequence=-10,
    )
    with pytest.raises(ReconciliationError) as exc_info:
        reconcile([baseline, off_by_a_fraction_of_a_cent])
    err = exc_info.value
    assert err.delta == Decimal("-0.001")
