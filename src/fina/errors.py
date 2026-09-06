"""Error taxonomy for fina (spec R-1.23).

Every failure in the pipeline raises a subclass of :class:`FinaError`, never a bare
``Exception``, and every subclass carries the structured fields the spec requires so a
reviewer can locate the offending row without re-parsing a message string.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class FinaError(Exception):
    """Base class for every error this package raises."""


@dataclass(frozen=True)
class ParseError(FinaError):
    """A field could not be parsed as its declared type (R-1.23)."""

    source_file: str
    source_row: int
    column: str
    raw_value: str
    expected: str

    def __post_init__(self) -> None:
        Exception.__init__(
            self,
            f"{self.source_file}:{self.source_row} column={self.column!r}: "
            f"cannot parse {self.raw_value!r}, expected {self.expected}",
        )


@dataclass(frozen=True)
class UnknownMovementError(FinaError):
    """A ``(category, type)`` pair or a ``Concepto`` string matches no mapping rule (R-1.23)."""

    source_file: str
    source_row: int
    observed: str

    def __post_init__(self) -> None:
        Exception.__init__(
            self,
            f"{self.source_file}:{self.source_row}: unrecognized movement {self.observed!r}",
        )


@dataclass(frozen=True)
class UnsupportedCurrencyError(FinaError):
    """A row's currency is not EUR (R-1.7)."""

    source_file: str
    source_row: int
    currency: str

    def __post_init__(self) -> None:
        Exception.__init__(
            self,
            f"{self.source_file}:{self.source_row}: unsupported currency {self.currency!r}",
        )


@dataclass(frozen=True)
class MigrationPairError(FinaError):
    """R-6.10..R-6.13 (DELIVERY/MIGRATION pairing) violated."""

    source_file: str
    date: date
    asset: str
    rows: tuple[int, ...]

    def __post_init__(self) -> None:
        Exception.__init__(
            self,
            f"{self.source_file}: MIGRATION group for {self.asset!r} on {self.date} "
            f"at rows {self.rows} is invalid",
        )


@dataclass(frozen=True)
class ReconciliationError(FinaError):
    """R-8.2/R-8.5 violated: computed balance disagrees with the declared one."""

    source_file: str
    source_row: int
    expected: Decimal
    declared: Decimal
    delta: Decimal

    def __post_init__(self) -> None:
        Exception.__init__(
            self,
            f"{self.source_file}:{self.source_row}: expected balance {self.expected}, "
            f"declared {self.declared}, delta {self.delta}",
        )


@dataclass(frozen=True)
class AccountConflictError(FinaError):
    """R-3.3 violated: same IBAN declared under two different holder names."""

    iban: str
    holder_names: tuple[str, ...]
    files: tuple[str, ...]

    def __post_init__(self) -> None:
        Exception.__init__(
            self,
            f"IBAN {self.iban!r} declared with conflicting holders {self.holder_names} "
            f"across files {self.files}",
        )


@dataclass(frozen=True)
class DuplicateSourceError(FinaError):
    """R-2.14 (duplicate transaction_id within a file) or R-2.15 (duplicate file content)."""

    source_rows: tuple[int, ...]
    files: tuple[str, ...] | None = None
    transaction_id: str | None = None

    def __post_init__(self) -> None:
        if (self.files is None) == (self.transaction_id is None):
            raise ValueError(
                "DuplicateSourceError requires exactly one of 'files' or 'transaction_id'"
            )
        if self.transaction_id is not None:
            Exception.__init__(
                self,
                f"duplicate transaction_id {self.transaction_id!r} at rows {self.source_rows}",
            )
        else:
            Exception.__init__(
                self,
                f"duplicate file content across {self.files} (rows {self.source_rows})",
            )


@dataclass(frozen=True)
class ValidationError(FinaError):
    """A sign/consistency invariant in spec section 2.5 failed."""

    source_file: str
    source_row: int
    invariant: str

    def __post_init__(self) -> None:
        Exception.__init__(
            self,
            f"{self.source_file}:{self.source_row}: invariant violated: {self.invariant}",
        )
