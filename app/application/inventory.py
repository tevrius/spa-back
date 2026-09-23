from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.domain.errors import DomainError
from app.domain.forecast import average_consumption
from app.domain.inventory import ZERO, Entry, Lot, replay
from app.infrastructure.models import Batch, Location, Movement, MovementAllocation, Product


def require_scope(session: Session, sku: str | None = None, location: str | None = None):
    product = None
    if sku is not None:
        product = session.get(Product, sku)
        if product is None:
            raise DomainError("unknown_sku", "SKU does not exist", 404, sku=sku)
    if location is not None and session.get(Location, location) is None:
        raise DomainError("unknown_location", "Location does not exist", 404, location=location)
    return product


def scoped(statement, model, sku: str | None, location: str | None):
    if sku is not None:
        statement = statement.where(model.sku == sku)
    if location is not None:
        statement = statement.where(model.location == location)
    return statement


def create_movement(session: Session, data, today: date) -> Movement:
    if data.date > today:
        raise DomainError("future_date", "Movement date cannot be in the future")
    # READ COMMITTED: queries after waiting for this lock see the previous writer's commit.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{len(data.sku)}:{data.sku}:{data.location}"},
    )
    require_scope(session, data.sku, data.location)
    if session.scalar(select(Movement.id).where(Movement.document_number == data.document_number)):
        raise DomainError("duplicate_document", "Document number already exists", 409)
    batch = None
    if data.batch is not None:
        batch = session.scalar(
            select(Batch).where(
                Batch.sku == data.sku, Batch.location == data.location, Batch.code == data.batch
            )
        )
        if batch is None:
            if data.operation != "receipt":
                raise DomainError("unknown_batch", "Batch does not exist in this scope", 404)
            if data.expires_on is None or data.unit_price is None:
                raise DomainError("missing_batch_metadata", "New receipt requires expiry and price")
            if data.expires_on < data.date:
                raise DomainError("invalid_expiry", "Expiry precedes receipt date")
            batch = Batch(
                code=data.batch,
                sku=data.sku,
                location=data.location,
                received_on=data.date,
                expires_on=data.expires_on,
                unit_price=data.unit_price,
                receipt_document=data.document_number,
            )
            session.add(batch)
            session.flush()
        elif data.operation == "receipt":
            if (data.expires_on is not None and data.expires_on != batch.expires_on) or (
                data.unit_price is not None and data.unit_price != batch.unit_price
            ):
                raise DomainError(
                    "batch_metadata_conflict", "Existing batch metadata cannot change"
                )
            if data.date < batch.received_on:
                batch.received_on = data.date
                batch.receipt_document = data.document_number
    movement = Movement(
        sku=data.sku,
        location=data.location,
        occurred_on=data.date,
        operation=data.operation,
        quantity=data.quantity,
        batch_id=batch.id if batch else None,
        document_number=data.document_number,
    )
    session.add(movement)
    session.flush()
    batches = list(session.scalars(scoped(select(Batch), Batch, data.sku, data.location)))
    entries = list(session.scalars(scoped(select(Movement), Movement, data.sku, data.location)))
    allocations = replay(
        [Lot(row.id, row.expires_on, row.received_on) for row in batches],
        [
            Entry(row.id, row.occurred_on, row.operation, row.quantity, row.batch_id)
            for row in entries
        ],
    )
    session.execute(
        delete(MovementAllocation).where(
            MovementAllocation.movement_id.in_([row.id for row in entries])
        )
    )
    session.add_all(
        [
            MovementAllocation(movement_id=row.movement_id, batch_id=row.batch_id, delta=row.delta)
            for row in allocations
        ]
    )
    session.flush()
    return movement


def batch_balances(session: Session, sku: str | None = None, location: str | None = None):
    statement = (
        select(Batch, func.coalesce(func.sum(MovementAllocation.delta), 0).label("balance"))
        .outerjoin(MovementAllocation, MovementAllocation.batch_id == Batch.id)
        .group_by(Batch.id)
    )
    return session.execute(scoped(statement, Batch, sku, location).order_by(Batch.id)).all()


def consumption_map(session: Session, today: date, sku=None, location=None):
    statement = (
        select(Movement.sku, Movement.location, func.sum(Movement.quantity))
        .where(
            Movement.operation == "consume",
            Movement.occurred_on >= today - timedelta(days=90),
            Movement.occurred_on < today,
        )
        .group_by(Movement.sku, Movement.location)
    )
    return {
        (s, loc): quantity
        for s, loc, quantity in session.execute(scoped(statement, Movement, sku, location))
    }


def stock_rows(session: Session, today: date, sku=None, location=None):
    products = {row.sku: row for row in session.scalars(select(Product))}
    consumed = consumption_map(session, today, sku, location)
    grouped: dict[tuple[str, str], list] = {}
    for batch, balance in batch_balances(session, sku, location):
        grouped.setdefault((batch.sku, batch.location), []).append((batch, balance))
    result = []
    for (product_sku, loc), batches in sorted(grouped.items()):
        product = products[product_sku]
        current = sum((balance for _, balance in batches), ZERO)
        available = sum((q for b, q in batches if b.expires_on >= today), ZERO)
        daily = average_consumption(consumed.get((product_sku, loc), ZERO))
        result.append(
            {
                "sku": product_sku,
                "name": product.name,
                "unit": product.unit,
                "location": loc,
                "current_stock": current,
                "available_stock": available,
                "expired_stock": current - available,
                "avg_daily_consumption": daily.quantize(Decimal("0.001")),
                "days_of_stock": (available / daily).quantize(Decimal("0.01")) if daily else None,
                "nearest_expiry": min((b.expires_on for b, q in batches if q > ZERO), default=None),
            }
        )
    return result


def movement_dicts(session: Session, movements: list[Movement]):
    ids = [row.id for row in movements]
    allocations: dict[int, list] = {}
    if ids:
        for mid, code, delta in session.execute(
            select(MovementAllocation.movement_id, Batch.code, MovementAllocation.delta)
            .join(Batch)
            .where(MovementAllocation.movement_id.in_(ids))
            .order_by(Batch.expires_on, Batch.received_on, Batch.id)
        ):
            allocations.setdefault(mid, []).append({"batch": code, "quantity_delta": delta})
    return [
        {
            "id": row.id,
            "date": row.occurred_on,
            "sku": row.sku,
            "location": row.location,
            "operation": row.operation,
            "quantity": row.quantity,
            "document_number": row.document_number,
            "allocations": allocations.get(row.id, []),
        }
        for row in movements
    ]
