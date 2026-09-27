"""R-3.3 (name-order-insensitive conflict check), R-3.8 (accounts that might be the user's,
and the account-names file) and R-3.4's promise that counting every transfer as a flow keeps
the totals right (WP-23), end to end."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from builders import BANK_XLSX, BROKER_CSV
from fina.adapters import broker_csv
from fina.classification import (
    OwnershipCandidate,
    classify_entries,
    collect_owned_accounts,
    ownership_candidates,
)
from fina.errors import AccountConflictError
from fina.models import AccountDeclaration, LedgerEntry, MovementType
from fina.pipeline import manifest_dict, run_pipeline
from fina.section1 import compute_section1
from test_classification import make_account, make_entry

OWNER = "FERNANDEZ ORTIZ LUCIA"
IBAN_A = "ES0000000000000000000202"
IBAN_B = "ES0000000000000000000203"


def broker_owner() -> AccountDeclaration:
    return make_account(institution="trade_republic", iban_or_account=None, holder_name=OWNER)


def transfer(
    row: int,
    cash: str,
    iban: str | None = IBAN_A,
    name: str | None = OWNER,
    day: int = 10,
    institution: str = "trade_republic",
    account: str = "cash",
) -> LedgerEntry:
    amount = Decimal(cash)
    return make_entry(
        entry_id=f"{institution}-e{row}",
        institution=institution,
        account=account,
        movement_type=(
            MovementType.EXTERNAL_DEPOSIT if amount > 0 else MovementType.EXTERNAL_WITHDRAWAL
        ),
        amount_eur=amount,
        cash_effect_eur=amount,
        counterparty_iban=iban,
        counterparty_name=name,
        source_file=f"{institution}.csv",
        source_row=row,
        date=date(2023, 3, day),
    )


# ---------------------------------------------------------------------------
# R-3.3: same account, same person, different word order -> no conflict
# ---------------------------------------------------------------------------


def test_t312_same_iban_with_the_holder_name_in_another_word_order_is_not_a_conflict() -> None:
    first = make_account(holder_name="FERNANDEZ ORTIZ LUCIA", declared_in_file="bank.xlsx")
    second = make_account(holder_name="LUCIA FERNANDEZ ORTIZ", declared_in_file="bank2.xlsx")
    assert collect_owned_accounts([first, second]) == (first, second)


def test_t312_same_iban_with_a_genuinely_different_holder_still_conflicts() -> None:
    first = make_account(holder_name="FERNANDEZ ORTIZ LUCIA")
    second = make_account(holder_name="MORENO SANZ DAVID", declared_in_file="bank2.xlsx")
    with pytest.raises(AccountConflictError):
        collect_owned_accounts([first, second])


# ---------------------------------------------------------------------------
# R-3.8: accounts that might be the user's
# ---------------------------------------------------------------------------


def test_t314_candidate_aggregates_every_row_of_its_account() -> None:
    entries = [
        transfer(2, "100.00", day=3),
        transfer(3, "-30.00", name="LUCIA FERNANDEZ ORTIZ", day=1),
        transfer(4, "20.00", iban="ES00 0000 0000 0000 0000 0202", day=9),
    ]
    (candidate,) = ownership_candidates(entries, [broker_owner()])
    assert candidate == OwnershipCandidate(
        iban=IBAN_A,
        holder_names=(OWNER, "LUCIA FERNANDEZ ORTIZ"),
        rows=(("trade_republic.csv", 2), ("trade_republic.csv", 3), ("trade_republic.csv", 4)),
        total_in=Decimal("120.00"),
        total_out=Decimal("30.00"),
        first_date=date(2023, 3, 1),
        last_date=date(2023, 3, 9),
    )


def test_t314_candidates_keep_first_seen_order() -> None:
    entries = [transfer(2, "10.00", iban=IBAN_B), transfer(3, "10.00"), transfer(4, "5.00")]
    assert [c.iban for c in ownership_candidates(entries, [broker_owner()])] == [IBAN_B, IBAN_A]


def test_t314_accounts_backed_by_a_statement_are_never_candidates() -> None:
    statement = make_account(iban_or_account=IBAN_A, holder_name=OWNER)
    assert ownership_candidates([transfer(2, "10.00")], [statement]) == ()


def test_t314_rows_without_iban_other_names_or_non_transfer_types_are_not_candidates() -> None:
    fee = make_entry(
        entry_id="f",
        movement_type=MovementType.EXPENSE,
        amount_eur=Decimal("-1"),
        cash_effect_eur=Decimal("-1"),
        counterparty_iban=IBAN_A,
        counterparty_name=OWNER,
    )
    entries = [
        transfer(2, "10.00", iban=None),
        transfer(3, "10.00", name="MORENO SANZ DAVID"),
        transfer(4, "10.00", name=None),
        fee,
    ]
    assert ownership_candidates(entries, [broker_owner()]) == ()


def test_t314_a_statement_account_row_does_not_stop_the_scan() -> None:
    statement = make_account(iban_or_account=IBAN_B, holder_name=OWNER)
    entries = [transfer(2, "10.00", iban=IBAN_B), transfer(3, "10.00")]
    assert [c.iban for c in ownership_candidates(entries, [broker_owner(), statement])] == [IBAN_A]


def test_t314_candidate_lists_only_the_names_actually_seen() -> None:
    """Every row of a candidate has a matching name; the listed names are the distinct ones."""
    entries = [transfer(2, "10.00"), transfer(3, "10.00")]
    (candidate,) = ownership_candidates(entries, [broker_owner()])
    assert candidate.holder_names == (OWNER,)
    assert len(candidate.rows) == 2


def test_t314_candidate_totals_count_amounts_below_one_euro() -> None:
    entries = [transfer(2, "0.50"), transfer(3, "-0.25")]
    (candidate,) = ownership_candidates(entries, [broker_owner()])
    assert (candidate.total_in, candidate.total_out) == (Decimal("0.50"), Decimal("0.25"))


def test_t314_candidate_totals_ignore_zero_rows_entirely() -> None:
    """A zero row adds nothing, not even its decimal places to the published total."""
    entries = [transfer(2, "10.00"), transfer(3, "-5.00"), transfer(4, "0.000")]
    (candidate,) = ownership_candidates(entries, [broker_owner()])
    assert (str(candidate.total_in), str(candidate.total_out)) == ("10.00", "5.00")


def test_t314_candidate_figures_are_decimals_even_when_nothing_is_summed() -> None:
    """Only outgoing (or only incoming) transfers: every figure is a Decimal, never the int 0
    a bare `sum()` would give (rule 9)."""
    (only_out,) = ownership_candidates([transfer(2, "-10.00")], [broker_owner()])
    assert type(only_out.total_in) is Decimal
    (only_in,) = ownership_candidates([transfer(2, "10.00")], [broker_owner()])
    assert type(only_in.total_out) is Decimal


def test_t314_a_candidate_changes_no_figure() -> None:
    """Display only: the ledger and the series are the same whether or not the candidate
    exists (the holder name is what makes it one)."""
    entries = [transfer(2, "100.00"), transfer(3, "-30.00")]
    stranger = [replace(e, counterparty_name="MORENO SANZ DAVID") for e in entries]
    assert ownership_candidates(entries, [broker_owner()]) != ()
    assert ownership_candidates(stranger, [broker_owner()]) == ()
    series = compute_section1(classify_entries(entries))
    assert [(p.real_net_worth, p.savings_only, p.gap) for p in series] == [
        (p.real_net_worth, p.savings_only, p.gap)
        for p in compute_section1(classify_entries(stranger))
    ]


# ---------------------------------------------------------------------------
# R-3.4: counting both legs as flows keeps every total right
# ---------------------------------------------------------------------------


def test_t319_both_legs_of_a_transfer_between_supplied_accounts_cancel() -> None:
    """Account B (bank) sends 500 to account A (broker) on the same day of a later month. Each
    leg is an external flow of its own account, and together they change neither net worth nor
    savings -- exactly as if the transfer had been recognized as internal."""
    seed = transfer(
        1, "1000.00", iban=None, name=None, day=1, institution="bank_es", account="current_account"
    )
    out_leg = replace(
        transfer(2, "-500.00", day=5, institution="bank_es", account="current_account"),
        date=date(2023, 4, 5),
    )
    in_leg = replace(transfer(3, "500.00", iban=IBAN_B), date=date(2023, 4, 5))
    later = replace(seed, entry_id="later", source_row=4, date=date(2023, 4, 20))
    with_transfer = compute_section1(classify_entries([seed, out_leg, in_leg, later]))
    without = compute_section1(classify_entries([seed, later]))
    for period, baseline in zip(with_transfer, without, strict=True):
        assert (period.real_net_worth, period.savings_only, period.gap) == (
            baseline.real_net_worth,
            baseline.savings_only,
            baseline.gap,
        )
    assert with_transfer[-1].savings_flow == Decimal("1000.00")


def test_t319_one_leg_alone_moves_net_worth_and_savings_together() -> None:
    """Only the broker's statement is supplied: 500 arriving from an account without one moves
    net worth and savings by the same 500 in a later month, so the return is unaffected."""
    seed = transfer(1, "100.00", iban=None, name=None, day=1)
    arrival = replace(transfer(2, "500.00"), date=date(2023, 4, 5))
    before, after = compute_section1(classify_entries([seed, arrival]))
    assert after.real_net_worth - before.real_net_worth == Decimal("500.00")
    assert after.savings_only - before.savings_only == Decimal("500.00")
    assert after.gap == before.gap


# ---------------------------------------------------------------------------
# End to end over the anonymized fixtures
# ---------------------------------------------------------------------------


def _run(tmp_path: Path, names_file: dict[str, Any] | None, with_bank: bool = False) -> Any:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    shutil.copyfile(BROKER_CSV, input_dir / BROKER_CSV.name)
    if with_bank:
        shutil.copyfile(BANK_XLSX, input_dir / BANK_XLSX.name)
    if names_file is not None:
        (input_dir / "cuentas.json").write_text(json.dumps(names_file), encoding="utf-8")
    return run_pipeline(input_dir)


def test_t314_fixture_account_in_the_owners_name_is_a_candidate(tmp_path: Path) -> None:
    result = _run(tmp_path, None)
    (candidate,) = result.ownership_candidates
    assert (candidate.iban, len(candidate.rows)) == (IBAN_A, 4)
    assert not any(IBAN_A in w.message for w in result.warnings)


def test_t313_an_old_confirmation_file_changes_no_figure(tmp_path: Path) -> None:
    """A version 1 file saying IBAN_A is the owner's (the retired R-3.8 decision) is read and
    ignored: same series, same candidate, no extra account."""
    decision = {
        "iban": IBAN_A,
        "holder_name": "LUCIA FERNANDEZ ORTIZ",
        "owned": True,
        "decided_on": "2026-09-25",
    }
    old_file = {"format": "fina-own-accounts", "version": 1, "accounts": [decision]}
    before = _run(tmp_path / "before", None)
    after = _run(tmp_path / "after", old_file)
    assert after.series == before.series
    assert after.ownership_candidates == before.ownership_candidates
    assert after.owned_accounts == before.owned_accounts


def test_t314_statement_account_is_no_candidate(tmp_path: Path) -> None:
    """The bank fixture declares IBAN_A itself: no candidate."""
    result = _run(tmp_path, None, with_bank=True)
    assert result.ownership_candidates == ()


def test_t314_manifest_records_the_candidates(tmp_path: Path) -> None:
    names = {"format": "fina-own-accounts", "version": 3, "aliases": {IBAN_A: "Savings"}}
    manifest = manifest_dict(_run(tmp_path, names))
    (candidate,) = manifest["ownership_candidates"]
    assert candidate == {
        "iban": IBAN_A,
        "holder_names": [OWNER],
        "rows": [{"source_file": "broker_ejemplo.csv", "source_row": r} for r in (2, 8, 11, 15)],
        "total_in": "25000.000000",
        "total_out": "80.000000",
        "first_date": "2023-03-10",
        "last_date": "2023-10-10",
    }
    assert "estimated_net_worth" not in manifest["section1_series"][-1]


def test_broker_fixture_rows_used_here_are_what_these_tests_assume() -> None:
    rows = [e for e in broker_csv.parse(BROKER_CSV).entries if e.counterparty_iban == IBAN_A]
    assert sum((e.cash_effect_eur for e in rows), Decimal("0")) == Decimal("24920.000000")
