"""Core data model: ``LedgerEntry``, ``AccountDeclaration``, ``MovementType``, cash effect.

Implements: R-1.21, R-1.23 (structured errors used here), R-2.1..R-2.17.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from fina.errors import DuplicateSourceError, ValidationError

Status = Literal["actual", "estimated"]


class MovementType(Enum):
    """The complete movement taxonomy (R-2.3). Exactly these 13 members."""

    EXTERNAL_DEPOSIT = "EXTERNAL_DEPOSIT"
    EXTERNAL_WITHDRAWAL = "EXTERNAL_WITHDRAWAL"
    INTERNAL_TRANSFER_IN = "INTERNAL_TRANSFER_IN"
    INTERNAL_TRANSFER_OUT = "INTERNAL_TRANSFER_OUT"
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    INTEREST = "INTEREST"
    EXPENSE = "EXPENSE"
    PAYROLL_INCOME = "PAYROLL_INCOME"
    RSU_VESTING = "RSU_VESTING"
    ESPP_PURCHASE = "ESPP_PURCHASE"
    TECHNICAL_ADJUSTMENT = "TECHNICAL_ADJUSTMENT"


#: Movement types deferred to a future iteration (D3): must never be produced (R-2.4).
_DEFERRED_D3_TYPES = frozenset({MovementType.RSU_VESTING, MovementType.ESPP_PURCHASE})

#: Movement types that count toward `savings_flow` only when `is_external_flow is True`
#: (R-2.3, used by section1.py -- exposed here as the single source of truth for the taxonomy).
SAVINGS_FLOW_ELIGIBLE_TYPES = frozenset(
    {
        MovementType.EXTERNAL_DEPOSIT,
        MovementType.EXTERNAL_WITHDRAWAL,
        MovementType.EXPENSE,
        MovementType.PAYROLL_INCOME,
    }
)

#: Movement types for which `is_external_flow` is meaningful at all (R-2.3 column 4).
EXTERNAL_FLOW_APPLICABLE_TYPES = frozenset(
    {
        MovementType.EXTERNAL_DEPOSIT,
        MovementType.EXTERNAL_WITHDRAWAL,
        MovementType.INTERNAL_TRANSFER_IN,
        MovementType.INTERNAL_TRANSFER_OUT,
        MovementType.EXPENSE,
        MovementType.PAYROLL_INCOME,
    }
)

#: The pre-classification "transfer-shaped" types that R-3.4's second pass may rewrite.
TRANSFER_SHAPED_TYPES = frozenset({MovementType.EXTERNAL_DEPOSIT, MovementType.EXTERNAL_WITHDRAWAL})


def compute_cash_effect(
    movement_type: MovementType,
    amount_eur: Decimal,
    fee_eur: Decimal | None,
) -> Decimal:
    """The cash-effect formula (R-2.6), exhaustive over every ``MovementType`` member.

    Deliberately not a ``match``/``case`` with a final wildcard: that shape leaves an
    unreachable "no case matched" branch that 100% branch coverage (gate G-1) can never
    exercise without a no-cover exemption disallowed outside the gate G-2 allowlist.
    Exhaustiveness is instead proven behaviourally by T-058, which iterates every current
    ``MovementType`` member against an explicit expected-value table -- a member added to the
    enum without a corresponding table entry fails that test immediately
    (implementation-plan.md WP-2 notes).
    """
    if movement_type in _DEFERRED_D3_TYPES:
        raise NotImplementedError(
            f"{movement_type.value} is deferred (D3): no employee-plan adapter exists "
            "in this iteration."
        )
    if movement_type is MovementType.TECHNICAL_ADJUSTMENT:
        return Decimal("0")
    if movement_type in (MovementType.BUY, MovementType.SELL):
        return amount_eur + (fee_eur if fee_eur is not None else Decimal("0"))
    return amount_eur


def compute_entry_id(
    *,
    transaction_id: str | None,
    institution: str,
    account: str,
    source_file: str,
    source_row: int,
) -> str:
    """Deterministic ``entry_id`` (R-1.21): the source ``transaction_id`` when present, else a
    stable hash of ``(institution, account, source_file, source_row)``. Never wall-clock time,
    randomness, or process state.
    """
    if transaction_id:
        return transaction_id
    digest_input = "\x1f".join([institution, account, source_file, str(source_row)])
    return "sha256:" + hashlib.sha256(digest_input.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Warning:
    """A non-fatal condition surfaced in ``AdapterResult.warnings`` / ``PipelineResult.warnings``
    (R-1.24). Never silent.
    """

    message: str
    source_file: str | None = None
    source_row: int | None = None


@dataclass(frozen=True)
class AccountDeclaration:
    """An account the user has proven ownership of by supplying a statement for it (R-2.8)."""

    institution: str
    iban_or_account: str | None
    holder_name: str
    declared_in_file: str
    as_of_date: _date


@dataclass(frozen=True)
class LedgerEntry:
    """One row of the master ledger (R-2.1). Immutable; carries the original ``raw`` row for
    traceability (R-2.2), and validates the self-contained invariants of spec section 2.5 at
    construction time.
    """

    entry_id: str
    date: _date
    value_date: _date | None
    source_timestamp: datetime | None
    institution: str
    account: str
    movement_type: MovementType
    asset: str | None
    asset_class: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    currency: str
    amount_eur: Decimal
    fee_eur: Decimal | None
    tax_eur: Decimal | None
    original_amount: Decimal | None
    original_currency: str | None
    fx_rate: Decimal | None
    cash_effect_eur: Decimal
    declared_balance: Decimal | None
    counterparty_name: str | None
    counterparty_iban: str | None
    is_external_flow: bool | None
    status: Status
    source_file: str
    source_row: int
    raw: Mapping[str, str] = field(compare=True)

    def __post_init__(self) -> None:
        if self.movement_type in _DEFERRED_D3_TYPES:
            raise NotImplementedError(
                f"{self.movement_type.value} is deferred (D3): no employee-plan adapter "
                "exists in this iteration; this entry must not be constructed."
            )
        self._check_buy_sell_invariants()
        self._check_dividend_interest_invariant()
        self._check_fee_invariant()
        self._check_technical_adjustment_invariant()

    def _fail(self, invariant: str) -> ValidationError:
        return ValidationError(
            source_file=self.source_file,
            source_row=self.source_row,
            invariant=invariant,
        )

    def _check_buy_sell_invariants(self) -> None:
        if self.movement_type is MovementType.BUY and not (
            self.amount_eur < 0 and self.quantity is not None and self.quantity > 0
        ):
            raise self._fail("R-2.10: BUY requires amount_eur < 0 and quantity > 0")
        if self.movement_type is MovementType.SELL and not (
            self.amount_eur > 0 and self.quantity is not None and self.quantity < 0
        ):
            raise self._fail("R-2.11: SELL requires amount_eur > 0 and quantity < 0")

    def _check_dividend_interest_invariant(self) -> None:
        if self.movement_type in (
            MovementType.DIVIDEND,
            MovementType.INTEREST,
        ) and not (self.amount_eur >= 0 and self.quantity is None):
            raise self._fail(
                "R-2.12: DIVIDEND/INTEREST require amount_eur >= 0 and quantity is None"
            )

    def _check_fee_invariant(self) -> None:
        if self.fee_eur is not None and self.fee_eur > 0:
            raise self._fail("R-2.13: fee_eur, when present, must be <= 0")

    def _check_technical_adjustment_invariant(self) -> None:
        if self.movement_type is MovementType.TECHNICAL_ADJUSTMENT and self.cash_effect_eur != 0:
            raise self._fail("R-2.17: TECHNICAL_ADJUSTMENT requires cash_effect_eur == 0")


@dataclass(frozen=True)
class AdapterResult:
    """The contract every adapter returns (R-5.1)."""

    entries: tuple[LedgerEntry, ...]
    accounts: tuple[AccountDeclaration, ...]
    warnings: tuple[Warning, ...]


def check_duplicate_transaction_ids(
    source_file: str,
    id_rows: Sequence[tuple[str, int]],
) -> None:
    """R-2.14: within one file, non-empty ``transaction_id`` values must be unique.

    ``id_rows`` is ``(transaction_id, source_row)`` pairs for rows that carry a
    ``transaction_id`` at all (callers must exclude rows with no id). Raises on the first
    duplicated id, in file order, deterministically.
    """
    seen: dict[str, list[int]] = {}
    for transaction_id, source_row in id_rows:
        seen.setdefault(transaction_id, []).append(source_row)
    for transaction_id, rows in seen.items():
        if len(rows) > 1:
            raise DuplicateSourceError(
                source_rows=tuple(rows),
                transaction_id=transaction_id,
            )


def check_zero_amount_warnings(
    source_file: str,
    entries: Sequence[LedgerEntry],
) -> tuple[Warning, ...]:
    """R-2.16: ``amount_eur == 0`` is permitted but must be surfaced, never silent."""
    return tuple(
        Warning(
            message=f"amount_eur is exactly 0 for entry {entry.entry_id!r}",
            source_file=source_file,
            source_row=entry.source_row,
        )
        for entry in entries
        if entry.amount_eur == 0
    )
