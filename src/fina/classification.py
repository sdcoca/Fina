"""Own-accounts registry and internal/external classification (spec section 3).

Implements: R-3.1..R-3.7.

This runs as a second pass, after every adapter has produced its own entries and
`AccountDeclaration`s (R-3.2): only once every file in the run has been parsed is it possible
to know the full set of accounts the user owns, since one file's transfer rows may only be
classifiable against a declaration that lives in a *different* file (`docs/technical-decisions.md`
§4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from fina.errors import AccountConflictError, ValidationError
from fina.models import (
    TRANSFER_SHAPED_TYPES,
    AccountDeclaration,
    LedgerEntry,
    MovementType,
    Warning,
)
from fina.money import ibans_match, names_match, normalize_iban, normalize_name


def collect_owned_accounts(
    accounts: Sequence[AccountDeclaration],
) -> tuple[AccountDeclaration, ...]:
    """R-3.1: the owned-accounts set for a run is the union of every declaration produced by
    every adapter over every file in the run -- there is no hand-maintained registry.

    R-3.3: if two declarations share a normalized `iban_or_account` but differ in normalized
    `holder_name`, raise `AccountConflictError`. Declarations with no IBAN (e.g. the broker's
    own account, R-6.16) never participate in this check -- there is nothing to compare.
    """
    owned = tuple(accounts)
    groups: dict[str, list[AccountDeclaration]] = {}
    for account in owned:
        if account.iban_or_account is None:
            continue
        groups.setdefault(normalize_iban(account.iban_or_account), []).append(account)
    for iban_key, group in groups.items():
        distinct_normalized_holders = {normalize_name(a.holder_name) for a in group}
        if len(distinct_normalized_holders) > 1:
            raise AccountConflictError(
                iban=iban_key,
                holder_names=tuple(a.holder_name for a in group),
                files=tuple(a.declared_in_file for a in group),
            )
    return owned


def classify_entries(
    entries: Sequence[LedgerEntry],
    owned_accounts: Sequence[AccountDeclaration],
) -> tuple[tuple[LedgerEntry, ...], tuple[Warning, ...]]:
    """R-3.2/R-3.4/R-3.5/R-3.6: classify every transfer-shaped entry as internal or external
    against `owned_accounts`, and rewrite `movement_type`/`is_external_flow` accordingly.

    Entries whose `movement_type` is not transfer-shaped (R-3.4's scope: only the adapters'
    `EXTERNAL_DEPOSIT`/`EXTERNAL_WITHDRAWAL` pre-classification default) pass through
    unchanged -- their `is_external_flow` was already fixed by the adapter (R-5.2), or does
    not apply to that movement type at all (R-2.3).
    """
    warnings: list[Warning] = []
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
        if _matches_by_name(entry, owned_accounts):
            direction = "from" if entry.movement_type is MovementType.EXTERNAL_DEPOSIT else "to"
            warnings.append(
                Warning(
                    message=(
                        f"{entry.source_file}:{entry.source_row}: transfer {direction} "
                        f"{entry.counterparty_name!r} classified external, but the name "
                        "matches an owned account holder; a statement for this account may "
                        "not have been supplied"
                    ),
                    source_file=entry.source_file,
                    source_row=entry.source_row,
                )
            )
        classified.append(replace(entry, is_external_flow=True))
    return tuple(classified), tuple(warnings)


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
