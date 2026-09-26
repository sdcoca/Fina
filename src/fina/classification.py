"""Own-accounts registry and internal/external classification (spec section 3).

Implements: R-3.1..R-3.9.

This runs as a second pass, after every adapter has produced its own entries and
`AccountDeclaration`s (R-3.2): only once every file in the run has been parsed is it possible
to know the full set of accounts the user owns, since one file's transfer rows may only be
classifiable against a declaration that lives in a *different* file (`docs/technical-decisions.md`
§4).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import date as _date
from decimal import Decimal
from typing import Literal

from fina.errors import AccountConflictError, ValidationError
from fina.models import (
    OWN_UNVERIFIED_INSTITUTION,
    TRANSFER_SHAPED_TYPES,
    USER_CONFIRMED_INSTITUTION,
    AccountDeclaration,
    LedgerEntry,
    MovementType,
    Warning,
)
from fina.money import ibans_match, names_match, normalize_iban
from fina.reconciliation import sort_key

OwnershipStatus = Literal["pending", "owned", "not_owned"]


@dataclass(frozen=True)
class OwnershipCandidate:
    """R-3.6/R-3.8: one counterparty account the user may own but has supplied no statement for
    -- either its holder name matches theirs, or they already decided on it in the own-accounts
    confirmation file. Every figure is traceable to `rows` (CLAUDE.md rule 10)."""

    iban: str
    holder_names: tuple[str, ...]
    rows: tuple[tuple[str, int], ...]
    total_in: Decimal
    total_out: Decimal
    first_date: _date
    last_date: _date
    status: OwnershipStatus
    #: R-3.9: only non-zero for an `owned` account with no statement -- its estimated balance
    #: at the end of the run, and the part of it that had to come from outside (floor at 0).
    estimated_balance: Decimal = Decimal("0")
    unseen_income: Decimal = Decimal("0")


def collect_owned_accounts(
    accounts: Sequence[AccountDeclaration],
) -> tuple[AccountDeclaration, ...]:
    """R-3.1: the owned-accounts set for a run is the union of every declaration produced by
    every adapter over every file in the run -- there is no hand-maintained registry.

    R-3.3: if two declarations share a normalized `iban_or_account` but their `holder_name`s
    are not the same name under R-1.13 (`names_match`: word order, case and vowel accents
    ignored), raise `AccountConflictError`. Declarations with no IBAN (e.g. the broker's own
    account, R-6.16) never participate in this check -- there is nothing to compare.
    """
    owned = tuple(accounts)
    groups: dict[str, list[AccountDeclaration]] = {}
    for account in owned:
        if account.iban_or_account is None:
            continue
        groups.setdefault(normalize_iban(account.iban_or_account), []).append(account)
    for iban_key, group in groups.items():
        if not all(names_match(group[0].holder_name, a.holder_name) for a in group):
            raise AccountConflictError(
                iban=iban_key,
                holder_names=tuple(a.holder_name for a in group),
                files=tuple(a.declared_in_file for a in group),
            )
    return owned


def classify_entries(
    entries: Sequence[LedgerEntry],
    owned_accounts: Sequence[AccountDeclaration],
    not_owned: Collection[str] = (),
) -> tuple[tuple[LedgerEntry, ...], tuple[Warning, ...]]:
    """R-3.2/R-3.4/R-3.5/R-3.6: classify every transfer-shaped entry as internal or external
    against `owned_accounts`, and rewrite `movement_type`/`is_external_flow` accordingly.

    Entries whose `movement_type` is not transfer-shaped (R-3.4's scope: only the adapters'
    `EXTERNAL_DEPOSIT`/`EXTERNAL_WITHDRAWAL` pre-classification default) pass through
    unchanged -- their `is_external_flow` was already fixed by the adapter (R-5.2), or does
    not apply to that movement type at all (R-2.3).

    `not_owned` (normalized IBANs, R-3.8): accounts the user declared not theirs -- still
    external, but no longer warned about.
    """
    # R-3.6 hits grouped per counterparty account (one warning per account, not per row),
    # in first-seen order so the output stays deterministic (R-1.20).
    stability_hits: dict[str, list[LedgerEntry]] = {}
    classified: list[LedgerEntry] = []
    for entry in entries:
        if entry.movement_type not in TRANSFER_SHAPED_TYPES:
            classified.append(entry)
            continue
        if _matches_by_iban(entry, owned_accounts):
            classified.append(_reclassify_internal(entry))
            continue
        if not entry.counterparty_iban and _matches_by_name(entry, owned_accounts):
            classified.append(_reclassify_internal(entry))
            continue
        # Rule 3: external. R-3.6 stability guard: warn if the name alone matches an owned
        # holder, regardless of *why* this row fell through to external (unmatched IBAN, or
        # no IBAN and no name match) -- a name match here means a statement for the matching
        # account may simply not have been supplied yet in this run.
        key = _counterparty_key(entry)
        if key not in not_owned and _matches_by_name(entry, owned_accounts):
            stability_hits.setdefault(key, []).append(entry)
        classified.append(replace(entry, is_external_flow=True))
    warnings = tuple(_stability_warning(hits) for hits in stability_hits.values())
    return tuple(classified), warnings


def _counterparty_key(entry: LedgerEntry) -> str:
    # Always a real IBAN here: a row with no IBAN whose name matches was already classified
    # internal (R-3.4 rule 2) before reaching the R-3.6 branch.
    return normalize_iban(entry.counterparty_iban or "")


def _stability_warning(hits: Sequence[LedgerEntry]) -> Warning:
    """R-3.6: one warning per counterparty account, listing every row it covers so each one
    stays traceable to its source (CLAUDE.md rule 10). A single-row account keeps the exact
    per-row wording."""
    first = hits[0]
    directions = {e.movement_type is MovementType.EXTERNAL_DEPOSIT for e in hits}
    direction = "to/from" if len(directions) > 1 else ("from" if directions.pop() else "to")
    rows_by_file: dict[str, list[str]] = {}
    for e in hits:
        rows_by_file.setdefault(e.source_file, []).append(str(e.source_row))
    refs = "; ".join(f"{f}:{','.join(rows)}" for f, rows in rows_by_file.items())
    count = "transfer" if len(hits) == 1 else f"{len(hits)} transfers"
    return Warning(
        message=(
            f"{refs}: {count} {direction} {first.counterparty_name!r} "
            f"(IBAN {first.counterparty_iban!r}) classified external, but the name matches an "
            "owned account holder; a statement for this account may not have been supplied"
        ),
        source_file=first.source_file if len(rows_by_file) == 1 else None,
        source_row=first.source_row if len(hits) == 1 else None,
        rule="R-3.6",
    )


def _matches_by_iban(entry: LedgerEntry, owned_accounts: Sequence[AccountDeclaration]) -> bool:
    if not entry.counterparty_iban:
        return False
    return any(
        account.iban_or_account is not None
        and ibans_match(entry.counterparty_iban, account.iban_or_account)
        for account in owned_accounts
    )


def _matches_by_name(entry: LedgerEntry, owned_accounts: Sequence[AccountDeclaration]) -> bool:
    if not entry.counterparty_name:
        return False
    return any(
        names_match(entry.counterparty_name, account.holder_name) for account in owned_accounts
    )


def _reclassify_internal(entry: LedgerEntry) -> LedgerEntry:
    """R-3.5: rewrite to `INTERNAL_TRANSFER_IN`/`_OUT` by the sign of `cash_effect_eur`
    (already computed by the adapter per R-2.6; unchanged by this rewrite -- the cash-effect
    formula returns `amount_eur` unchanged for every transfer-shaped movement type, internal
    or external alike). A zero cash effect on an internal transfer is a modelled invariant
    violation, not a valid degenerate case.
    """
    if entry.cash_effect_eur > 0:
        new_type = MovementType.INTERNAL_TRANSFER_IN
    elif entry.cash_effect_eur < 0:
        new_type = MovementType.INTERNAL_TRANSFER_OUT
    else:
        raise ValidationError(
            source_file=entry.source_file,
            source_row=entry.source_row,
            invariant="R-3.5: an internal transfer must have a non-zero cash_effect_eur",
        )
    return replace(entry, movement_type=new_type, is_external_flow=False)


def _own_iban_sets(owned_accounts: Sequence[AccountDeclaration]) -> tuple[set[str], set[str]]:
    """(IBANs proven by a statement, IBANs confirmed by the user in the R-3.8 file)."""
    statement: set[str] = set()
    confirmed: set[str] = set()
    for account in owned_accounts:
        if account.iban_or_account is None:
            continue
        target = confirmed if account.institution == USER_CONFIRMED_INSTITUTION else statement
        target.add(normalize_iban(account.iban_or_account))
    return statement, confirmed


def ownership_candidates(
    entries: Sequence[LedgerEntry],
    owned_accounts: Sequence[AccountDeclaration],
    not_owned: Collection[str] = (),
    estimated: Sequence[LedgerEntry] = (),
) -> tuple[OwnershipCandidate, ...]:
    """R-3.8: every counterparty IBAN of a transfer-shaped entry (pre-classification) that no
    statement in the run declares, and that either matches an owned holder by name or has
    already been decided in the confirmation file -- so decided accounts stay listed and can be
    changed. First-seen order (R-1.20). `estimated` is `mirror_unverified_transfers`'
    output, from which each owned account's estimated balance and unseen income are summed."""
    statement, confirmed = _own_iban_sets(owned_accounts)
    groups: dict[str, list[LedgerEntry]] = {}
    for entry in entries:
        if entry.movement_type not in TRANSFER_SHAPED_TYPES or not entry.counterparty_iban:
            continue
        key = normalize_iban(entry.counterparty_iban)
        if key in statement:
            continue
        decided = key in confirmed or key in not_owned
        if decided or _matches_by_name(entry, owned_accounts):
            groups.setdefault(key, []).append(entry)
    return tuple(
        _candidate(key, hits, _status(key, confirmed, not_owned), estimated)
        for key, hits in groups.items()
    )


def _status(key: str, confirmed: Collection[str], not_owned: Collection[str]) -> OwnershipStatus:
    if key in confirmed:
        return "owned"
    if key in not_owned:
        return "not_owned"
    return "pending"


def _candidate(
    key: str,
    hits: Sequence[LedgerEntry],
    status: OwnershipStatus,
    estimated: Sequence[LedgerEntry],
) -> OwnershipCandidate:
    names = tuple(dict.fromkeys(e.counterparty_name for e in hits if e.counterparty_name))
    dates = [e.date for e in hits]
    own = [e for e in estimated if e.account == key]
    return OwnershipCandidate(
        iban=key,
        holder_names=names,
        rows=tuple((e.source_file, e.source_row) for e in hits),
        total_in=sum((e.cash_effect_eur for e in hits if e.cash_effect_eur > 0), Decimal("0")),
        total_out=-sum((e.cash_effect_eur for e in hits if e.cash_effect_eur < 0), Decimal("0")),
        first_date=min(dates),
        last_date=max(dates),
        status=status,
        estimated_balance=sum((e.cash_effect_eur for e in own), Decimal("0")),
        unseen_income=sum(
            (e.cash_effect_eur for e in own if e.movement_type is MovementType.EXTERNAL_DEPOSIT),
            Decimal("0"),
        ),
    )


_OPPOSITE_INTERNAL = {
    MovementType.INTERNAL_TRANSFER_IN: MovementType.INTERNAL_TRANSFER_OUT,
    MovementType.INTERNAL_TRANSFER_OUT: MovementType.INTERNAL_TRANSFER_IN,
}


def mirror_unverified_transfers(
    classified: Sequence[LedgerEntry],
    owned_accounts: Sequence[AccountDeclaration],
) -> tuple[LedgerEntry, ...]:
    """R-3.9: for every internal transfer whose counterparty is an account the user confirmed
    (R-3.8) but supplied no statement for, the other leg of that same transfer, booked on an
    `OWN_UNVERIFIED_INSTITUTION` account keyed by its IBAN, marked `status="estimated"`. Moving
    money to or from that account then changes neither real net worth nor savings (CLAUDE.md
    rule 13). Each mirror keeps its real row's `source_file`/`source_row` (rule 10). Once a
    statement for that IBAN is supplied, no mirrors are produced and its real balance replaces
    the estimate.

    Floor at zero (owner's decision on real data): walking each account's mirrors in ledger
    order (R-1.22), whenever its estimated balance would drop below zero -- the account sent
    the tracked accounts money they never sent it -- an estimated `EXTERNAL_DEPOSIT` for exactly
    the shortfall is booked on it first, on the same row: income that must have reached that
    account from outside (a salary, say) and that no supplied statement shows. It counts as
    savings and as net worth alike, so the gap is unchanged, and no estimated balance is ever
    negative.
    """
    statement, confirmed = _own_iban_sets(owned_accounts)
    unverified = confirmed - statement
    legs = [
        entry
        for entry in classified
        if entry.movement_type in _OPPOSITE_INTERNAL
        and entry.counterparty_iban
        and normalize_iban(entry.counterparty_iban) in unverified
    ]
    estimated: list[LedgerEntry] = []
    balances: dict[str, Decimal] = {}
    for entry in sorted(legs, key=sort_key):
        key = normalize_iban(entry.counterparty_iban or "")
        mirror = replace(
            entry,
            entry_id=f"mirror:{entry.entry_id}",
            institution=OWN_UNVERIFIED_INSTITUTION,
            account=key,
            movement_type=_OPPOSITE_INTERNAL[entry.movement_type],
            amount_eur=-entry.amount_eur,
            cash_effect_eur=-entry.cash_effect_eur,
            fee_eur=None,
            tax_eur=None,
            declared_balance=None,
            status="estimated",
        )
        balance = balances.get(key, Decimal("0")) + mirror.cash_effect_eur
        if balance < 0:
            estimated.append(
                replace(
                    mirror,
                    entry_id=f"unseen:{entry.entry_id}",
                    movement_type=MovementType.EXTERNAL_DEPOSIT,
                    amount_eur=-balance,
                    cash_effect_eur=-balance,
                    counterparty_name=None,
                    counterparty_iban=None,
                    is_external_flow=True,
                )
            )
            balance = Decimal("0")
        balances[key] = balance
        estimated.append(mirror)
    return tuple(estimated)
