"""Balance reconciliation against each source's own declared running balance (spec section 8).

Implements: R-8.1..R-8.5. Also exposes `anchor_at` (R-9.1), the R-8.3 baseline lookup
`section1.py` calls rather than re-deriving independently -- see R-9.1's rationale in
`docs/spec/section1-ingestion-spec.md` for the real bug (Q-H) this one-formula-not-two
requirement was written to prevent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date as _date
from decimal import Decimal

from fina.errors import ReconciliationError
from fina.models import LedgerEntry, Warning

#: An entry paired with its own `declared_balance`, already narrowed to `Decimal` (never
#: `None`) by construction -- see `_with_declared_balance`. Keeping the narrowing inside one
#: comprehension, rather than a `cast()` at each use site, means there is no `cast()` call
#: left anywhere in this module for a mutation test to flag as a trivially-equivalent no-op.
_DeclaredPair = tuple[LedgerEntry, Decimal]


def sort_key(entry: LedgerEntry) -> tuple[_date, int, str]:
    """R-1.22: `(date, file_sequence, source_file)` ascending. Public (not `_sort_key`)
    because `section1.py`'s R-9.1 anchor arithmetic needs to compare two entries' sort keys
    the same way this module orders them for reconciliation -- one definition, not two.
    """
    return (entry.date, entry.file_sequence, entry.source_file)


def _group_by_account(
    entries: Sequence[LedgerEntry],
) -> dict[tuple[str, str], list[LedgerEntry]]:
    """R-8.1: reconciliation runs per `(institution, account)`. Plain dict, not a set-backed
    grouping: iteration order follows first appearance in `entries`, which is itself
    deterministic (R-1.20) -- never re-sorted here.
    """
    groups: dict[tuple[str, str], list[LedgerEntry]] = {}
    for entry in entries:
        groups.setdefault((entry.institution, entry.account), []).append(entry)
    return groups


def _with_declared_balance(ordered: Sequence[LedgerEntry]) -> list[_DeclaredPair]:
    """R-8.4's filter: entries with no `declared_balance` at all are excluded here, once, so
    every later step in this module works with an already-narrowed `Decimal`.
    """
    return [
        (entry, entry.declared_balance) for entry in ordered if entry.declared_balance is not None
    ]


def _check_balance_chain(pairs: Sequence[_DeclaredPair]) -> None:
    """R-8.2/R-8.3: the first declared-balance row is the baseline (not itself checked);
    every subsequent one must satisfy `previous.declared_balance + current.cash_effect_eur
    == current.declared_balance` exactly (CLAUDE.md rule 9 -- no tolerance).
    """
    for i in range(1, len(pairs)):
        _previous_entry, previous_balance = pairs[i - 1]
        current_entry, current_balance = pairs[i]
        expected = previous_balance + current_entry.cash_effect_eur
        if expected != current_balance:
            raise ReconciliationError(
                source_file=current_entry.source_file,
                source_row=current_entry.source_row,
                expected=expected,
                declared=current_balance,
                delta=expected - current_balance,
            )


def _check_header_balance(
    institution: str,
    pairs: Sequence[_DeclaredPair],
    header_balances: Mapping[tuple[str, str], Decimal],
) -> None:
    """R-8.5: the account's final (most recent, per R-1.22 order) declared balance must equal
    the balance stated in that entry's own file's header block, when the caller has supplied
    it (`header_balances` is populated by whoever has filesystem access to reopen the source
    file and read its header -- e.g. via `bank_xlsx.read_header_block` -- since this module is
    a pure function of already-parsed entries and never touches the filesystem itself,
    R-5.4-style).
    """
    final_entry, final_balance = pairs[-1]
    key = (institution, final_entry.source_file)
    if key not in header_balances:
        return
    header_balance = header_balances[key]
    if final_balance != header_balance:
        raise ReconciliationError(
            source_file=final_entry.source_file,
            source_row=final_entry.source_row,
            expected=header_balance,
            declared=final_balance,
            delta=header_balance - final_balance,
        )


def reconcile(
    entries: Sequence[LedgerEntry],
    header_balances: Mapping[tuple[str, str], Decimal] | None = None,
) -> tuple[Warning, ...]:
    """Run R-8.1..R-8.5 over every `(institution, account)` group in `entries`.

    Raises `ReconciliationError` on the first violation found (R-8.2 or R-8.5); returns the
    R-8.4 "unverified" warnings for every group that carries no declared balance at all (e.g.
    the broker's cash account, which has no running-balance column in its source format).
    `header_balances`, keyed by `(institution, source_file)`, supplies the R-8.5 same-file
    cross-check for institutions whose files carry one; a group whose final file has no entry
    in this mapping simply skips that one check (R-8.2/R-8.3 still apply regardless).
    """
    resolved_header_balances = header_balances or {}
    warnings: list[Warning] = []
    for (institution, account), group in _group_by_account(entries).items():
        ordered = sorted(group, key=sort_key)
        pairs = _with_declared_balance(ordered)
        if not pairs:
            warnings.append(
                Warning(
                    message=(
                        f"{institution}/{account}: computed balance is unverified against "
                        "any source-declared balance"
                    ),
                    source_file=ordered[0].source_file,
                    source_row=None,
                )
            )
            continue
        _check_balance_chain(pairs)
        _check_header_balance(institution, pairs, resolved_header_balances)
    return tuple(warnings)


def anchor_at(
    entries: Sequence[LedgerEntry], institution: str, account: str, as_of: _date
) -> _DeclaredPair | None:
    """R-8.3/R-9.1: the reconciliation baseline for `(institution, account)` at or before
    `as_of` -- the entry with the latest `sort_key` (R-1.22) among all entries for this pair
    that carry a non-`None` `declared_balance` and are dated on or before `as_of`, paired with
    that balance. `as_of` is a bare date, not a full sort key, so "at or before `as_of`" is
    read as "dated on or before `as_of`"; among ties on that filter the entry with the latest
    `sort_key` wins, exactly mirroring how `_check_balance_chain` picks each row's predecessor.

    Returns `None` when no such entry exists -- R-8.4's case, the whole `(institution,
    account)` group carries no declared balance at all (e.g. the broker's cash sub-account,
    whose export has no running-balance column). `section1.py`'s `cash_balance` calls this
    rather than re-deriving the same fact independently: before this function existed,
    `cash_balance` summed `cash_effect_eur` from an assumed zero balance, which silently
    dropped any unrecorded balance that predated an account's earliest ingested entry (the
    real bug this function exists to fix -- see R-9.1's rationale in the spec).
    """
    candidates = [
        (entry, entry.declared_balance)
        for entry in entries
        if entry.institution == institution
        and entry.account == account
        and entry.declared_balance is not None
        and entry.date <= as_of
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: sort_key(pair[0]))
