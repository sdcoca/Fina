"""Own-accounts registry, transfer classification and accounts that might be the user's
(spec section 3).

Implements: R-3.1..R-3.8.

Every transfer counts as money in or out of the account it appears in (R-3.4, revised
2026-09-27): no transfer is recognized as internal, by IBAN or by name. Between two accounts
whose statements are both supplied, the two legs cancel in every total; to or from an account
with no statement, net worth and savings move together and the return is unaffected. Once
that account's statement is supplied, its own legs balance the totals with no rule to match
them.

The accounts that might be the user's (R-3.8) are computed as a second pass, after every
adapter has produced its own entries and `AccountDeclaration`s (R-3.2): only then are all the
holder names in the run known. They are display only and never change a figure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date as _date
from decimal import Decimal

from fina.errors import AccountConflictError
from fina.models import TRANSFER_SHAPED_TYPES, AccountDeclaration, LedgerEntry
from fina.money import names_match, normalize_iban


@dataclass(frozen=True)
class OwnershipCandidate:
    """R-3.8: one counterparty account that might be the user's -- its holder name matches
    the holder of a supplied statement -- but that no supplied statement declares. Every
    figure is traceable to `rows` (CLAUDE.md rule 10)."""

    iban: str
    holder_names: tuple[str, ...]
    rows: tuple[tuple[str, int], ...]
    total_in: Decimal
    total_out: Decimal
    first_date: _date
    last_date: _date


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


def classify_entries(entries: Sequence[LedgerEntry]) -> tuple[LedgerEntry, ...]:
    """R-3.4/R-3.5: every transfer-shaped entry (the adapters' `EXTERNAL_DEPOSIT` /
    `EXTERNAL_WITHDRAWAL` default) keeps its type and gets `is_external_flow=True`. Every other
    entry passes through unchanged -- its `is_external_flow` was already fixed by the adapter
    (R-5.2), or does not apply to that movement type at all (R-2.3)."""
    return tuple(
        replace(entry, is_external_flow=True)
        if entry.movement_type in TRANSFER_SHAPED_TYPES
        else entry
        for entry in entries
    )


def ownership_candidates(
    entries: Sequence[LedgerEntry],
    owned_accounts: Sequence[AccountDeclaration],
) -> tuple[OwnershipCandidate, ...]:
    """R-3.8: one candidate per counterparty IBAN of a transfer-shaped entry that no statement
    in the run declares and whose counterparty name matches (R-1.13) the holder of a supplied
    statement. First-seen order (R-1.20)."""
    declared = {
        normalize_iban(a.iban_or_account) for a in owned_accounts if a.iban_or_account is not None
    }
    groups: dict[str, list[LedgerEntry]] = {}
    for entry in entries:
        if entry.movement_type not in TRANSFER_SHAPED_TYPES or not entry.counterparty_iban:
            continue
        key = normalize_iban(entry.counterparty_iban)
        if key not in declared and _matches_by_name(entry, owned_accounts):
            groups.setdefault(key, []).append(entry)
    return tuple(_candidate(key, hits) for key, hits in groups.items())


def _matches_by_name(entry: LedgerEntry, owned_accounts: Sequence[AccountDeclaration]) -> bool:
    if not entry.counterparty_name:
        return False
    return any(
        names_match(entry.counterparty_name, account.holder_name) for account in owned_accounts
    )


def _candidate(key: str, hits: Sequence[LedgerEntry]) -> OwnershipCandidate:
    names = tuple(dict.fromkeys(e.counterparty_name for e in hits if e.counterparty_name))
    dates = [e.date for e in hits]
    return OwnershipCandidate(
        iban=key,
        holder_names=names,
        rows=tuple((e.source_file, e.source_row) for e in hits),
        total_in=sum((e.cash_effect_eur for e in hits if e.cash_effect_eur > 0), Decimal("0")),
        total_out=-sum((e.cash_effect_eur for e in hits if e.cash_effect_eur < 0), Decimal("0")),
        first_date=min(dates),
        last_date=max(dates),
    )
