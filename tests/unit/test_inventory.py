from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.domain.errors import DomainError
from app.domain.inventory import Entry, Lot, allocate_fefo, replay

TODAY = date(2026, 9, 23)


def lot(id, expires=10, received=-20):
    return Lot(id, TODAY + timedelta(days=expires), TODAY + timedelta(days=received))


def test_fefo_splits_between_batches_in_expiry_order():
    assert allocate_fefo([lot(1, 30), lot(2, 5)], {1: D(10), 2: D(3)}, D(8), TODAY) == [
        (2, D(3)),
        (1, D(5)),
    ]


def test_expired_batch_is_not_consumable():
    with pytest.raises(DomainError) as error:
        allocate_fefo([lot(1, -1), lot(2)], {1: D(100), 2: D(2)}, D(3), TODAY)
    assert error.value.details["available"] == D(2)


def test_expiry_date_is_inclusive_and_ties_are_stable():
    assert allocate_fefo([lot(2, 0), lot(1, 0)], {1: D(1), 2: D(1)}, D(2), TODAY) == [
        (1, D(1)),
        (2, D(1)),
    ]


def test_ledger_balance_includes_all_operations():
    operations = [
        ("receipt", "10"),
        ("consume", "3"),
        ("return", "1"),
        ("correction", "-2"),
        ("writeoff", "1"),
    ]
    allocations = replay(
        [lot(1)],
        [
            Entry(i, TODAY, op, D(qty), None if op == "consume" else 1)
            for i, (op, qty) in enumerate(operations, 1)
        ],
    )
    assert sum(a.delta for a in allocations) == D(5)


def test_historical_receipt_cannot_cover_earlier_consumption():
    with pytest.raises(DomainError, match="exceeds usable"):
        replay(
            [lot(1)],
            [
                Entry(1, TODAY, "receipt", D(10), 1),
                Entry(2, TODAY - timedelta(days=1), "consume", D(5)),
            ],
        )


def test_backdated_receipt_rebuilds_later_fefo():
    allocations = replay(
        [lot(1, 20), lot(2, 5)],
        [
            Entry(1, TODAY - timedelta(days=5), "receipt", D(10), 1),
            Entry(2, TODAY, "consume", D(4)),
            Entry(3, TODAY - timedelta(days=1), "receipt", D(3), 2),
        ],
    )
    assert [(a.batch_id, a.delta) for a in allocations if a.movement_id == 2] == [
        (2, D(-3)),
        (1, D(-1)),
    ]


def test_expired_stock_can_be_written_off():
    allocations = replay(
        [lot(1, -1)],
        [
            Entry(1, TODAY - timedelta(days=5), "receipt", D(10), 1),
            Entry(2, TODAY, "writeoff", D(10), 1),
        ],
    )
    assert sum(a.delta for a in allocations) == 0


@pytest.mark.parametrize("operation,quantity", [("writeoff", "11"), ("correction", "-11")])
def test_negative_batch_balance_rejected(operation, quantity):
    with pytest.raises(DomainError):
        replay(
            [lot(1)],
            [Entry(1, TODAY, "receipt", D(10), 1), Entry(2, TODAY, operation, D(quantity), 1)],
        )
