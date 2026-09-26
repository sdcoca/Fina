"""R-3.3 (name-order-insensitive conflict check), R-3.8 (ownership candidates, not_owned),
R-3.9 (estimated mirror rows) and R-9.12 (estimated_net_worth), end to end."""

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
from fina import cli
from fina.adapters import broker_csv
from fina.classification import (
    OwnershipCandidate,
    classify_entries,
    collect_owned_accounts,
    mirror_unverified_transfers,
    ownership_candidates,
)
from fina.errors import AccountConflictError
from fina.models import (
    OWN_UNVERIFIED_INSTITUTION,
    USER_CONFIRMED_INSTITUTION,
    AccountDeclaration,
    LedgerEntry,
    MovementType,
)
from fina.pipeline import manifest_dict, run_pipeline
from fina.section1 import compute_section1, estimated_net_worth
from test_classification import make_account, make_entry

OWNER = "FERNANDEZ ORTIZ LUCIA"
IBAN_A = "ES0000000000000000000202"
IBAN_B = "ES0000000000000000000203"


def confirmed(iban: str = IBAN_A, name: str = "LUCIA FERNANDEZ ORTIZ") -> AccountDeclaration:
    return AccountDeclaration(
        institution=USER_CONFIRMED_INSTITUTION,
        iban_or_account=iban,
        holder_name=name,
        declared_in_file="cuentas.json",
        as_of_date=date(2026, 9, 25),
    )


def broker_owner() -> AccountDeclaration:
    return make_account(institution="trade_republic", iban_or_account=None, holder_name=OWNER)


def transfer(
    row: int,
    cash: str,
    iban: str | None = IBAN_A,
    name: str = OWNER,
    day: int = 10,
) -> LedgerEntry:
    amount = Decimal(cash)
    return make_entry(
        entry_id=f"e{row}",
        institution="trade_republic",
        account="cash",
        movement_type=(
            MovementType.EXTERNAL_DEPOSIT if amount > 0 else MovementType.EXTERNAL_WITHDRAWAL
        ),
        amount_eur=amount,
        cash_effect_eur=amount,
        counterparty_iban=iban,
        counterparty_name=name,
        source_file="broker.csv",
        source_row=row,
        date=date(2023, 3, day),
    )


# ---------------------------------------------------------------------------
# R-3.3: same account, same person, different word order -> no conflict
# ---------------------------------------------------------------------------


def test_same_iban_with_the_holder_name_in_another_word_order_is_not_a_conflict() -> None:
    statement = make_account(holder_name="FERNANDEZ ORTIZ LUCIA", declared_in_file="bank.xlsx")
    assert collect_owned_accounts([statement, confirmed()]) == (statement, confirmed())


def test_same_iban_with_a_genuinely_different_holder_still_conflicts() -> None:
    statement = make_account(holder_name="FERNANDEZ ORTIZ LUCIA")
    with pytest.raises(AccountConflictError):
        collect_owned_accounts([statement, confirmed(name="MORENO SANZ DAVID")])


# ---------------------------------------------------------------------------
# R-3.8: candidates and not_owned
# ---------------------------------------------------------------------------


def test_pending_candidate_aggregates_every_row_of_its_account() -> None:
    entries = [
        transfer(2, "100.00", day=3),
        transfer(3, "-30.00", name="LUCIA FERNANDEZ ORTIZ", day=1),
        transfer(4, "20.00", iban="ES00 0000 0000 0000 0000 0202", day=9),
    ]
    (candidate,) = ownership_candidates(entries, [broker_owner()])
    assert candidate == OwnershipCandidate(
        iban=IBAN_A,
        holder_names=(OWNER, "LUCIA FERNANDEZ ORTIZ"),
        rows=(("broker.csv", 2), ("broker.csv", 3), ("broker.csv", 4)),
        total_in=Decimal("120.00"),
        total_out=Decimal("30.00"),
        first_date=date(2023, 3, 1),
        last_date=date(2023, 3, 9),
        status="pending",
    )


def test_candidates_keep_first_seen_order_and_carry_their_status() -> None:
    entries = [
        transfer(2, "10.00", iban=IBAN_B, name="MORENO SANZ DAVID"),
        transfer(3, "10.00", iban=IBAN_A),
        transfer(4, "10.00", iban="ES0000000000000000000303"),
    ]
    owned = [broker_owner(), confirmed(iban=IBAN_A)]
    candidates = ownership_candidates(entries, owned, not_owned={IBAN_B})
    assert [(c.iban, c.status) for c in candidates] == [
        (IBAN_B, "not_owned"),  # decided, even though the name is someone else's
        (IBAN_A, "owned"),
        ("ES0000000000000000000303", "pending"),
    ]


def test_accounts_backed_by_a_statement_are_never_candidates() -> None:
    statement = make_account(iban_or_account=IBAN_A, holder_name=OWNER)
    entries = [transfer(2, "10.00")]
    assert ownership_candidates(entries, [statement, confirmed()]) == ()


def test_rows_without_iban_other_names_or_non_transfer_types_are_not_candidates() -> None:
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
        fee,
    ]
    assert ownership_candidates(entries, [broker_owner()]) == ()


def test_a_statement_account_row_does_not_stop_the_scan() -> None:
    statement = make_account(iban_or_account=IBAN_B, holder_name=OWNER)
    entries = [transfer(2, "10.00", iban=IBAN_B), transfer(3, "10.00")]
    assert [c.iban for c in ownership_candidates(entries, [broker_owner(), statement])] == [IBAN_A]


def test_candidate_lists_only_the_names_actually_seen() -> None:
    """A decided account can have rows with no counterparty name: no blank name is listed."""
    entries = [transfer(2, "10.00"), replace(transfer(3, "10.00"), counterparty_name=None)]
    (candidate,) = ownership_candidates(entries, [broker_owner(), confirmed()])
    assert candidate.holder_names == (OWNER,)
    assert len(candidate.rows) == 2


def test_candidate_totals_count_amounts_below_one_euro() -> None:
    entries = [transfer(2, "0.50"), transfer(3, "-0.25")]
    (candidate,) = ownership_candidates(entries, [broker_owner()])
    assert (candidate.total_in, candidate.total_out) == (Decimal("0.50"), Decimal("0.25"))


def test_candidate_totals_ignore_zero_rows_entirely() -> None:
    """A zero row adds nothing, not even its decimal places to the published total."""
    entries = [transfer(2, "10.00"), transfer(3, "-5.00"), transfer(4, "0.000")]
    (candidate,) = ownership_candidates(entries, [broker_owner()])
    assert (str(candidate.total_in), str(candidate.total_out)) == ("10.00", "5.00")


def test_candidate_figures_are_decimals_even_when_nothing_is_summed() -> None:
    """A pending account with only outgoing transfers: every figure is a Decimal zero or
    amount, never the int 0 a bare `sum()` would give (rule 9)."""
    (candidate,) = ownership_candidates([transfer(2, "-10.00")], [broker_owner()])
    figures = (
        candidate.total_in,
        candidate.total_out,
        candidate.estimated_balance,
        candidate.unseen_income,
    )
    assert all(type(f) is Decimal for f in figures)
    (only_in,) = ownership_candidates([transfer(2, "10.00")], [broker_owner()])
    assert type(only_in.total_out) is Decimal


def test_not_owned_account_stays_external_and_is_no_longer_warned() -> None:
    entries = [transfer(2, "10.00")]
    (entry,), warnings = classify_entries(entries, [broker_owner()], not_owned={IBAN_A})
    assert entry.is_external_flow is True
    assert warnings == ()
    _entries, pending_warnings = classify_entries(entries, [broker_owner()])
    assert len(pending_warnings) == 1


def test_confirmed_account_becomes_internal_by_iban_without_a_warning() -> None:
    (entry,), warnings = classify_entries([transfer(2, "10.00")], [broker_owner(), confirmed()])
    assert entry.movement_type is MovementType.INTERNAL_TRANSFER_IN
    assert warnings == ()


# ---------------------------------------------------------------------------
# R-3.9: estimated mirror rows
# ---------------------------------------------------------------------------


def _classified(owned: list[AccountDeclaration]) -> tuple[LedgerEntry, ...]:
    entries = [transfer(2, "100.00"), transfer(3, "-30.00"), transfer(4, "5.00", iban=IBAN_B)]
    classified, _warnings = classify_entries(entries, owned)
    return classified


def test_mirror_books_the_other_leg_with_a_zero_floor() -> None:
    """IBAN_A sends 100 into the tracked account, then receives 30 back. Its mirror would go
    to -100 first, so 100 of estimated outside income is booked just before it: the account's
    estimated balance never drops below zero and ends at 30."""
    owned = [broker_owner(), confirmed()]
    unseen, deposit_mirror, withdrawal_mirror = mirror_unverified_transfers(
        _classified(owned), owned
    )

    assert unseen.entry_id == "unseen:e2"
    assert unseen.movement_type is MovementType.EXTERNAL_DEPOSIT
    assert (unseen.amount_eur, unseen.cash_effect_eur) == (Decimal("100.00"), Decimal("100.00"))
    assert unseen.is_external_flow is True
    assert unseen.status == "estimated"
    assert (unseen.institution, unseen.account) == (OWN_UNVERIFIED_INSTITUTION, IBAN_A)
    assert (unseen.counterparty_name, unseen.counterparty_iban) == (None, None)
    assert (unseen.source_file, unseen.source_row) == ("broker.csv", 2)

    assert deposit_mirror.entry_id == "mirror:e2"
    assert deposit_mirror.institution == OWN_UNVERIFIED_INSTITUTION
    assert deposit_mirror.account == IBAN_A
    assert deposit_mirror.movement_type is MovementType.INTERNAL_TRANSFER_OUT
    assert deposit_mirror.amount_eur == Decimal("-100.00")
    assert deposit_mirror.cash_effect_eur == Decimal("-100.00")
    assert deposit_mirror.status == "estimated"
    assert deposit_mirror.is_external_flow is False
    assert deposit_mirror.declared_balance is None
    assert (deposit_mirror.source_file, deposit_mirror.source_row) == ("broker.csv", 2)

    assert withdrawal_mirror.entry_id == "mirror:e3"
    assert withdrawal_mirror.movement_type is MovementType.INTERNAL_TRANSFER_IN
    assert withdrawal_mirror.cash_effect_eur == Decimal("30.00")


def test_no_top_up_while_the_account_stays_at_or_above_zero() -> None:
    owned = [broker_owner(), confirmed()]
    entries = [transfer(2, "-50.00", day=1), transfer(3, "50.00", day=2)]
    classified, _w = classify_entries(entries, owned)
    estimated = mirror_unverified_transfers(classified, owned)
    assert [e.entry_id for e in estimated] == ["mirror:e2", "mirror:e3"]


def test_floor_follows_ledger_order_not_input_order() -> None:
    """Receiving 50 first (day 1) covers sending 50 back later (day 2), whatever order the
    rows arrive in."""
    owned = [broker_owner(), confirmed()]
    entries = [transfer(3, "50.00", day=2), transfer(2, "-50.00", day=1)]
    classified, _w = classify_entries(entries, owned)
    estimated = mirror_unverified_transfers(classified, owned)
    assert [e.entry_id for e in estimated] == ["mirror:e2", "mirror:e3"]


def test_mirror_drops_fee_and_tax_from_the_other_leg() -> None:
    owned = [broker_owner(), confirmed()]
    entry = replace_fee(transfer(2, "-100.00"))
    classified, _w = classify_entries([entry], owned)
    (mirror,) = mirror_unverified_transfers(classified, owned)
    assert (mirror.fee_eur, mirror.tax_eur) == (None, None)


def replace_fee(entry: LedgerEntry) -> LedgerEntry:
    return replace(entry, fee_eur=Decimal("-1.00"), tax_eur=Decimal("-0.50"))


def test_mirror_never_carries_the_real_legs_declared_balance() -> None:
    """The tracked account's statement balance says nothing about the other account."""
    owned = [broker_owner(), confirmed()]
    entry = replace(transfer(2, "-100.00"), declared_balance=Decimal("900.00"))
    classified, _w = classify_entries([entry], owned)
    (mirror,) = mirror_unverified_transfers(classified, owned)
    assert mirror.declared_balance is None


def test_no_mirror_once_a_statement_for_that_account_is_supplied() -> None:
    statement = make_account(iban_or_account=IBAN_A, holder_name=OWNER)
    owned = [broker_owner(), statement, confirmed()]
    assert mirror_unverified_transfers(_classified(owned), owned) == ()


def test_no_mirror_without_a_confirmation() -> None:
    owned = [broker_owner()]
    assert mirror_unverified_transfers(_classified(owned), owned) == ()


def test_estimated_net_worth_counts_only_the_own_unverified_accounts() -> None:
    owned = [broker_owner(), confirmed()]
    classified = _classified(owned)
    estimated = mirror_unverified_transfers(classified, owned)
    ledger = classified + estimated
    assert estimated_net_worth(ledger, date(2023, 3, 31)) == Decimal("30.00")
    (period,) = compute_section1(ledger)
    assert period.estimated_net_worth == Decimal("30.00")
    # Tracked account: +100 -30 +5 = 75; plus the estimated 30.
    assert period.real_net_worth == Decimal("105.00")


def test_candidate_reports_the_estimated_balance_and_unseen_income() -> None:
    owned = [broker_owner(), confirmed()]
    entries = [transfer(2, "100.00"), transfer(3, "-30.00")]
    classified, _w = classify_entries(entries, owned)
    estimated = mirror_unverified_transfers(classified, owned)
    (candidate,) = ownership_candidates(entries, owned, estimated=estimated)
    assert (candidate.status, candidate.estimated_balance, candidate.unseen_income) == (
        "owned",
        Decimal("30.00"),
        Decimal("100.00"),
    )
    (without_estimates,) = ownership_candidates(entries, owned)
    assert (without_estimates.estimated_balance, without_estimates.unseen_income) == (
        Decimal("0"),
        Decimal("0"),
    )


# ---------------------------------------------------------------------------
# End to end over the anonymized broker fixture
# ---------------------------------------------------------------------------


def _run(tmp_path: Path, decisions: list[dict[str, Any]] | None, with_bank: bool = False) -> Any:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    shutil.copyfile(BROKER_CSV, input_dir / BROKER_CSV.name)
    if with_bank:
        shutil.copyfile(BANK_XLSX, input_dir / BANK_XLSX.name)
    if decisions is not None:
        doc = {"format": "fina-own-accounts", "version": 1, "accounts": decisions}
        (input_dir / "cuentas.json").write_text(json.dumps(doc), encoding="utf-8")
    return run_pipeline(input_dir)


def _decide(owned: bool) -> list[dict[str, Any]]:
    return [
        {
            "iban": IBAN_A,
            "holder_name": "LUCIA FERNANDEZ ORTIZ",
            "owned": owned,
            "decided_on": "2026-09-25",
        }
    ]


def test_undecided_account_is_a_pending_candidate_with_its_warning(tmp_path: Path) -> None:
    result = _run(tmp_path, None)
    (candidate,) = result.ownership_candidates
    assert (candidate.iban, candidate.status, len(candidate.rows)) == (IBAN_A, "pending", 4)
    assert any(IBAN_A in w.message for w in result.warnings)


def test_confirming_an_account_keeps_tracked_balances_and_the_gap(tmp_path: Path) -> None:
    """The core promise, month by month: the tracked accounts do not move, the confirmed
    account only adds its estimated balance on top, and the gap does not change by a cent."""
    before = _run(tmp_path / "before", None)
    after = _run(tmp_path / "after", _decide(owned=True))
    assert after.ownership_candidates[0].status == "owned"
    assert not any(IBAN_A in w.message for w in after.warnings)
    for b, a in zip(before.series, after.series, strict=True):
        assert a.real_net_worth - a.estimated_net_worth == b.real_net_worth
        assert a.gap == b.gap
        assert a.estimated_net_worth >= 0
    # Fixture: 8000 + 12000 + 5000 came in from IBAN_A (all unseen income), 80 went back.
    candidate = after.ownership_candidates[0]
    assert (candidate.unseen_income, candidate.estimated_balance) == (
        Decimal("25000.000000"),
        Decimal("80.000000"),
    )
    assert after.series[-1].estimated_net_worth == Decimal("80.000000")


def test_rejecting_an_account_changes_no_figure_and_silences_it(tmp_path: Path) -> None:
    before = _run(tmp_path / "before", None)
    after = _run(tmp_path / "after", _decide(owned=False))
    assert after.series == before.series
    assert after.ownership_candidates[0].status == "not_owned"
    assert not any(IBAN_A in w.message for w in after.warnings)


def test_statement_wins_over_confirmation_even_with_names_in_another_order(
    tmp_path: Path,
) -> None:
    """The bank fixture declares IBAN_A itself: no conflict, no candidate, no mirror."""
    result = _run(tmp_path, _decide(owned=True), with_bank=True)
    assert result.ownership_candidates == ()
    assert not any(e.institution == OWN_UNVERIFIED_INSTITUTION for e in result.entries)
    assert all(p.estimated_net_worth == 0 for p in result.series)


def test_manifest_records_candidates_and_the_estimated_figure(tmp_path: Path) -> None:
    manifest = manifest_dict(_run(tmp_path, _decide(owned=True)))
    (candidate,) = manifest["ownership_candidates"]
    assert candidate["iban"] == IBAN_A
    assert candidate["status"] == "owned"
    assert candidate["total_in"] == "25000.000000"
    assert candidate["total_out"] == "80.000000"
    assert candidate["rows"][0] == {"source_file": "broker_ejemplo.csv", "source_row": 2}
    assert (candidate["first_date"], candidate["last_date"]) == ("2023-03-10", "2023-10-10")
    assert candidate["holder_names"] == [OWNER]
    assert candidate["estimated_balance"] == "80.000000"
    assert candidate["unseen_income"] == "25000.000000"
    assert manifest["section1_series"][-1]["estimated_net_worth"] == "80.000000"


def test_manifest_records_the_pending_accounts_warning(tmp_path: Path) -> None:
    result = _run(tmp_path, None)
    manifest = manifest_dict(result)
    expected = [
        {"message": w.message, "source_file": w.source_file, "source_row": w.source_row}
        for w in result.warnings
    ]
    assert manifest["warnings"] == expected
    assert any(IBAN_A in w["message"] for w in manifest["warnings"])


def test_cli_prints_the_estimated_share_only_when_there_is_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    shutil.copyfile(BROKER_CSV, input_dir / BROKER_CSV.name)
    assert cli.main(["build", "--input", str(input_dir), "--out", str(tmp_path / "o1")]) == 0
    assert "of which estimated" not in capsys.readouterr().out
    doc = {"format": "fina-own-accounts", "version": 1, "accounts": _decide(owned=True)}
    (input_dir / "cuentas.json").write_text(json.dumps(doc), encoding="utf-8")
    assert cli.main(["build", "--input", str(input_dir), "--out", str(tmp_path / "o2")]) == 0
    assert (
        "  of which estimated (own accounts without a statement): 80.00 EUR"
        in capsys.readouterr().out.splitlines()
    )


def test_broker_fixture_rows_used_here_are_what_these_tests_assume() -> None:
    rows = [e for e in broker_csv.parse(BROKER_CSV).entries if e.counterparty_iban == IBAN_A]
    assert sum((e.cash_effect_eur for e in rows), Decimal("0")) == Decimal("24920.000000")
