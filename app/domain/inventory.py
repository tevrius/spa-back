"""Pure replayable inventory ledger. No database, HTTP, or implicit clock."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.errors import DomainError

ZERO = Decimal("0")


@dataclass(frozen=True)
class Lot:
    id: int
    expires_on: date
    received_on: date


@dataclass(frozen=True)
class Entry:
    id: int
    occurred_on: date
    operation: str
    quantity: Decimal
    batch_id: int | None = None


@dataclass(frozen=True)
class Allocation:
    movement_id: int
    batch_id: int
    delta: Decimal


def allocate_fefo(
    lots: list[Lot], balances: dict[int, Decimal], quantity: Decimal, on: date
) -> list[tuple[int, Decimal]]:
    eligible = sorted(
        (lot for lot in lots if lot.received_on <= on <= lot.expires_on),
        key=lambda lot: (lot.expires_on, lot.received_on, lot.id),
    )
    available = sum((balances.get(lot.id, ZERO) for lot in eligible), ZERO)
    if quantity <= ZERO:
        raise DomainError("invalid_quantity", "Quantity must be positive")
    if quantity > available:
        raise DomainError(
            "insufficient_stock",
            "Consumption exceeds usable stock",
            requested=quantity,
            available=available,
            operation_date=on,
        )
    result = []
    remaining = quantity
    for lot in eligible:
        take = min(remaining, balances.get(lot.id, ZERO))
        if take > ZERO:
            result.append((lot.id, take))
            remaining -= take
        if remaining == ZERO:
            break
    return result


def replay(lots: list[Lot], entries: list[Entry]) -> list[Allocation]:
    """Rebuild allocations in event-date/id order, validating every historical prefix."""
    by_id = {lot.id: lot for lot in lots}
    balances = dict.fromkeys(by_id, ZERO)
    result: list[Allocation] = []
    for entry in sorted(entries, key=lambda row: (row.occurred_on, row.id)):
        if entry.operation == "consume":
            allocations = [
                Allocation(entry.id, batch_id, -amount)
                for batch_id, amount in allocate_fefo(
                    lots, balances, entry.quantity, entry.occurred_on
                )
            ]
        else:
            if entry.operation not in {"receipt", "return", "writeoff", "correction"}:
                raise DomainError("invalid_operation", "Unsupported movement type")
            lot = by_id.get(entry.batch_id)
            if lot is None:
                raise DomainError("unknown_batch", "Batch does not exist", 404)
            if lot.received_on > entry.occurred_on:
                raise DomainError("batch_not_received", "Operation precedes batch receipt")
            if entry.quantity == ZERO or (
                entry.operation != "correction" and entry.quantity < ZERO
            ):
                raise DomainError("invalid_quantity", "Invalid movement quantity")
            delta = -entry.quantity if entry.operation == "writeoff" else entry.quantity
            if balances[lot.id] + delta < ZERO:
                raise DomainError(
                    "insufficient_stock",
                    "Operation exceeds batch stock",
                    requested=abs(delta),
                    available=balances[lot.id],
                    operation_date=entry.occurred_on,
                )
            allocations = [Allocation(entry.id, lot.id, delta)]
        for allocation in allocations:
            balances[allocation.batch_id] += allocation.delta
        result.extend(allocations)
    return result
