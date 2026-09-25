"""Tests for fina.reconciliation (WP-7): R-8.1..R-8.5.

T-358, T-359, T-360, T-361 (the file_sequence-specific regressions added alongside the Q-F
fix) live in tests/test_bank_xlsx.py and tests/test_broker_csv.py: they test the *adapters'*
file_sequence assignment (R-6.1a/R-7.5a) and internal sort order, not reconciliation.py's own
logic, which is what this file covers (T-350..T-357).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl.worksheet.worksheet import Worksheet

from builders import BANK_XLSX, BROKER_CSV, bank_xlsx_with
from fina.adapters import bank_xlsx, broker_csv
from fina.errors import ReconciliationError
from fina.models import LedgerEntry, MovementType
from fina.reconciliation import anchor_at, reconcile

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
    reconcile(
        result.entries,
        header_balances={("bank_es", "banco_ejemplo.xlsx"): header.balance},
    )


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
    reconcile([baseline, second])


# ---------------------------------------------------------------------------
# R-8.4: accounts with no declared balance at all
# ---------------------------------------------------------------------------


def test_t354_broker_accounts_with_no_declared_balance_are_skipped_silently() -> None:
    """The broker export has no running-balance column at all -- neither the `cash` nor the
    `positions` sub-account carries a declared_balance -- so there is nothing to cross-check:
    `reconcile` raises nothing and emits nothing (R-8.4, revised: the owner could not act on an
    "unverified" warning, and the balance is summed from real movements, not estimated).
    """
    result = broker_csv.parse(BROKER_CSV)
    assert all(e.declared_balance is None for e in result.entries)
    assert reconcile(result.entries) is None


def test_a_no_balance_account_does_not_stop_later_accounts_being_checked(tmp_path: Path) -> None:
    """Skipping a no-balance group must move on to the next group, not stop the whole pass:
    a corrupted bank account listed after the broker's accounts must still raise."""

    def mutate(ws: Worksheet) -> None:
        ws["E10"] = "6.196,16€"  # declared balance off by exactly one cent

    bank = bank_xlsx.parse(bank_xlsx_with(tmp_path, mutate, filename="corrupted.xlsx"))
    broker = broker_csv.parse(BROKER_CSV)
    with pytest.raises(ReconciliationError):
        reconcile([*broker.entries, *bank.entries])


def test_t355_no_balance_accounts_add_no_warning_to_a_full_run(tmp_path: Path) -> None:
    import shutil

    from fina.pipeline import run_pipeline

    shutil.copy(BROKER_CSV, tmp_path / BROKER_CSV.name)
    warnings = run_pipeline(tmp_path).warnings
    assert not [w for w in warnings if "declared balance" in w.message or "unverified" in w.message]


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
    reconcile(result.entries, header_balances={})
    reconcile(result.entries, header_balances=None)


# ---------------------------------------------------------------------------
# R-8.2/R-1.5: exact Decimal equality, no epsilon
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# R-9.1 (Q-H): anchor_at, the R-8.3 baseline lookup section1.py's cash_balance calls
# ---------------------------------------------------------------------------


def test_t362_anchor_at_returns_the_latest_declared_balance_entry_at_or_before_as_of() -> None:
    result = bank_xlsx.parse(BANK_XLSX)
    # §12.1: source_row 9 (07/03/2027) is the last row per R-1.22 order; its declared_balance
    # (6183.75) is the anchor for any as_of on or after that date.
    anchor = anchor_at(result.entries, "bank_es", "current_account", date(2027, 3, 7))
    assert anchor is not None
    anchor_entry, anchor_balance = anchor
    assert anchor_entry.source_row == 9
    assert anchor_balance == Decimal("6183.75")


def test_t362b_anchor_at_picks_the_latest_qualifying_entry_not_just_any_match() -> None:
    """`as_of` sits strictly between two declared-balance rows: the anchor must be the earlier
    one (05/03), not the later one (07/03) which is dated after `as_of`.
    """
    result = bank_xlsx.parse(BANK_XLSX)
    anchor = anchor_at(result.entries, "bank_es", "current_account", date(2027, 3, 5))
    assert anchor is not None
    anchor_entry, anchor_balance = anchor
    assert anchor_entry.source_row == 11  # §12.1: 05/03/2027, declared Saldo 6.260,35
    assert anchor_balance == Decimal("6260.35")


def test_t362c_anchor_at_before_any_declared_balance_returns_none() -> None:
    result = bank_xlsx.parse(BANK_XLSX)
    earliest = min(e.date for e in result.entries)
    before_everything = earliest - timedelta(days=1)
    assert anchor_at(result.entries, "bank_es", "current_account", before_everything) is None


def test_t363_anchor_at_returns_none_for_an_account_with_no_declared_balance_at_all() -> None:
    """R-8.4's case: the broker export has no running-balance column, so neither of its two
    sub-accounts ever has a `declared_balance` to anchor to.
    """
    result = broker_csv.parse(BROKER_CSV)
    latest = max(e.date for e in result.entries)
    assert anchor_at(result.entries, "trade_republic", "cash", latest) is None
    assert anchor_at(result.entries, "trade_republic", "positions", latest) is None


def test_anchor_at_excludes_other_accounts_of_the_same_institution() -> None:
    cash = make_entry(
        entry_id="c1",
        institution="trade_republic",
        account="cash",
        declared_balance=Decimal("100.00"),
        date=date(2023, 1, 1),
        source_row=2,
        file_sequence=2,
    )
    positions = make_entry(
        entry_id="p1",
        institution="trade_republic",
        account="positions",
        declared_balance=Decimal("999.00"),
        date=date(2023, 1, 1),
        source_row=3,
        file_sequence=3,
    )
    anchor = anchor_at([cash, positions], "trade_republic", "cash", date(2023, 1, 1))
    assert anchor is not None
    assert anchor[1] == Decimal("100.00")


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
