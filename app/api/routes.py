from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import (
    AlertPage,
    ErrorResponse,
    ForecastIn,
    ForecastOut,
    MovementCreated,
    MovementIn,
    MovementPage,
    Operation,
    StockDetail,
    StockPage,
)
from app.application.alerts import alerts
from app.application.inventory import (
    batch_balances,
    create_movement,
    movement_dicts,
    require_scope,
    scoped,
    stock_rows,
)
from app.application.planning import forecast
from app.config import business_today, get_settings
from app.domain.errors import DomainError
from app.infrastructure.database import session_factory
from app.infrastructure.models import Movement

router = APIRouter(
    prefix="/api", responses={code: {"model": ErrorResponse} for code in (400, 404, 409, 422)}
)


def write_session():
    with session_factory()() as session:
        yield session


def read_session():
    with session_factory()() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        yield session


ReadSession = Annotated[Session, Depends(read_session)]
WriteSession = Annotated[Session, Depends(write_session)]
Today = Annotated[date, Depends(business_today)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.post("/movements", status_code=201, response_model=MovementCreated)
def post_movement(data: MovementIn, session: WriteSession, today: Today):
    try:
        movement = create_movement(session, data, today)
        result = movement_dicts(session, [movement])[0]
        stock = stock_rows(session, today, data.sku, data.location)[0]
        result.update(
            current_stock=stock["current_stock"], available_stock=stock["available_stock"]
        )
        session.commit()
        return result
    except IntegrityError as exc:
        session.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise DomainError("duplicate_document", "Document number already exists", 409) from exc
        raise


@router.get("/movements", response_model=MovementPage)
def get_movements(
    session: ReadSession,
    sku: str | None = None,
    location: str | None = None,
    operation: Operation | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    require_scope(session, sku, location)
    if date_from and date_to and date_from > date_to:
        raise DomainError("invalid_period", "date_from must not exceed date_to")
    statement = scoped(select(Movement), Movement, sku, location)
    if operation:
        statement = statement.where(Movement.operation == operation)
    if date_from:
        statement = statement.where(Movement.occurred_on >= date_from)
    if date_to:
        statement = statement.where(Movement.occurred_on <= date_to)
    total = session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = list(
        session.scalars(
            statement.order_by(Movement.occurred_on.desc(), Movement.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return {
        "items": movement_dicts(session, rows),
        "limit": limit,
        "offset": offset,
        "total": total,
    }


@router.get("/stock", response_model=StockPage)
def get_stock(
    session: ReadSession,
    today: Today,
    sku: str | None = None,
    location: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    require_scope(session, sku, location)
    rows = stock_rows(session, today, sku, location)
    return {
        "items": rows[offset : offset + limit],
        "limit": limit,
        "offset": offset,
        "total": len(rows),
    }


@router.get("/stock/{sku}", response_model=StockDetail)
def get_stock_detail(sku: str, session: ReadSession, today: Today):
    product = require_scope(session, sku)
    stocks = stock_rows(session, today, sku)
    batches = batch_balances(session, sku)
    documents: dict[int, list[str]] = {}
    for batch_id, document in session.execute(
        select(Movement.batch_id, Movement.document_number)
        .where(Movement.sku == sku, Movement.operation == "receipt")
        .order_by(Movement.occurred_on, Movement.id)
    ):
        documents.setdefault(batch_id, []).append(document)
    for stock in stocks:
        stock["batches"] = [
            {
                "batch": b.code,
                "current_stock": quantity,
                "expires_on": b.expires_on,
                "received_on": b.received_on,
                "unit_price": b.unit_price,
                "receipt_documents": documents.get(b.id, []),
                "expired": b.expires_on < today,
            }
            for b, quantity in sorted(batches, key=lambda row: (row[0].expires_on, row[0].id))
            if b.location == stock["location"]
        ]
    return {"sku": sku, "name": product.name, "unit": product.unit, "locations": stocks}


@router.post("/forecast", response_model=ForecastOut)
def post_forecast(data: ForecastIn, session: ReadSession, today: Today):
    return forecast(session, data, today)


@router.get("/alerts", response_model=AlertPage)
def get_alerts(
    session: ReadSession,
    today: Today,
    sku: str | None = None,
    location: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    require_scope(session, sku, location)
    settings = get_settings()
    rows = alerts(
        session, today, settings.expiry_warning_days, settings.inactivity_days, sku, location
    )
    return {
        "items": rows[offset : offset + limit],
        "limit": limit,
        "offset": offset,
        "total": len(rows),
    }
