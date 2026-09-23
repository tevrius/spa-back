from datetime import date
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.documentation import (
    ALERT_RESPONSE,
    FORECAST_EXAMPLES,
    FORECAST_RESPONSE,
    MOVEMENT_EXAMPLES,
    MOVEMENT_RESPONSE,
    STOCK_RESPONSE,
    error_responses,
    success_response,
)
from app.api.schemas import (
    AlertPage,
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

router = APIRouter(prefix="/api", responses=error_responses(404, 422, 503))


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
Limit = Annotated[
    int, Query(ge=1, le=100, description="Размер страницы; total не зависит от limit.")
]
Offset = Annotated[int, Query(ge=0, description="Количество пропущенных записей после фильтрации.")]
SkuFilter = Annotated[
    str | None, Query(description="Фильтр по SKU. Неизвестный SKU — 404.", examples=["OIL-001"])
]
LocationFilter = Annotated[
    str | None, Query(description="Код объекта. Неизвестный код — 404.", examples=["MS-01"])
]


@router.post(
    "/movements",
    status_code=201,
    response_model=MovementCreated,
    tags=["movements"],
    summary="Зарегистрировать движение товара",
    operation_id="createMovement",
    description=(
        "Атомарная запись и пересчёт остатка. Для новой партии receipt нужны batch, expires_on "
        "и unit_price. Для существующей партии метаданные должны совпадать. "
        "consume запрещает batch и распределяется по FEFO. Остальные операции требуют batch. "
        "return означает возврат на склад, correction — знаковое изменение, не абсолютный остаток. "
        "Будущие даты и отрицательные остатки запрещены. Заднее число "
        "пересчитывает последующий FEFO; "
        "при историческом недостатке вся запись откатывается. Повтор документа возвращает 409."
    ),
    responses={
        **error_responses(400, 409, 422, insufficient_stock=True),
        201: success_response(
            "Движение сохранено; остатки включают результат операции",
            {**MOVEMENT_RESPONSE, "current_stock": 70, "available_stock": 70},
        ),
    },
)
def post_movement(
    data: Annotated[MovementIn, Body(openapi_examples=MOVEMENT_EXAMPLES)],
    session: WriteSession,
    today: Today,
):
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


@router.get(
    "/movements",
    response_model=MovementPage,
    tags=["movements"],
    summary="Получить историю движений",
    operation_id="listMovements",
    description=(
        "Фильтры объединяются через AND. Границы дат включены. Порядок: дата и ID убыванию."
    ),
    responses={
        200: success_response(
            "Страница отфильтрованной истории",
            {
                "items": [MOVEMENT_RESPONSE],
                "limit": 50,
                "offset": 0,
                "total": 1,
            },
        )
    },
)
def get_movements(
    session: ReadSession,
    sku: SkuFilter = None,
    location: LocationFilter = None,
    operation: Annotated[Operation | None, Query(description="Тип складской операции.")] = None,
    date_from: Annotated[date | None, Query(description="Начальная дата включительно.")] = None,
    date_to: Annotated[
        date | None, Query(description="Конечная дата включительно; не раньше date_from.")
    ] = None,
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


@router.get(
    "/stock",
    response_model=StockPage,
    tags=["stock"],
    summary="Получить текущие остатки",
    operation_id="listStock",
    description=(
        "Остатки вычисляются из движений. Показаны пары SKU–объект с заведёнными партиями, "
        "включая нулевые остатки. Порядок: SKU, объект. Расход — consume за 90 полных дней "
        "до сегодня / 90. Запас в днях рассчитан по available_stock. "
        "При нулевом расходе days_of_stock = null. nearest_expiry может быть в прошлом."
    ),
    responses={
        200: success_response(
            "Страница складских остатков",
            {
                "items": [STOCK_RESPONSE],
                "limit": 50,
                "offset": 0,
                "total": 1,
            },
        )
    },
)
def get_stock(
    session: ReadSession,
    today: Today,
    sku: SkuFilter = None,
    location: LocationFilter = None,
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


@router.get(
    "/stock/{sku}",
    response_model=StockDetail,
    tags=["stock"],
    summary="Получить остатки и партии товара",
    operation_id="getStockBySku",
    description=(
        "Детализация по объектам и партиям, включая пустые партии. "
        "Партии упорядочены по годности и ID. Известный товар без партий: locations = []. "
        "receipt_documents содержит все приходные документы партии."
    ),
    responses={
        200: success_response(
            "Объекты и партии товара",
            {
                "sku": "OIL-001",
                "name": "Массажное масло",
                "unit": "л",
                "locations": [
                    {
                        **STOCK_RESPONSE,
                        "batches": [
                            {
                                "batch": "MANUAL-BATCH-001",
                                "current_stock": 10,
                                "expires_on": "2027-09-23",
                                "received_on": "2026-09-23",
                                "unit_price": 1500,
                                "receipt_documents": ["MANUAL-RECEIPT-001"],
                                "expired": False,
                            }
                        ],
                    }
                ],
            },
        )
    },
)
def get_stock_detail(
    sku: Annotated[str, Path(description="Артикул товара.", examples=["OIL-001"])],
    session: ReadSession,
    today: Today,
):
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


@router.post(
    "/forecast",
    response_model=ForecastOut,
    tags=["forecast"],
    summary="Рассчитать потребность в закупке",
    operation_id="calculateForecast",
    description=(
        "Расчёт без изменения БД. Передайте ровно один горизонт: days или months. "
        "Месяцы календарные, конец периода включён. При отложенном start_date учитывается "
        "дефицит до начала периода. Календарная модель учитывает FEFO, истечение годности "
        "и даты неполученных открытых поставок; просроченные поставки исключены. "
        "Закупка покрывает непокрытый спрос и страховой запас, округляется до упаковки "
        "и минимального заказа. Цена последнего поступления; при её отсутствии стоимость null. "
        "stockout_date показана без новой закупки только в пределах горизонта. "
        "explanation содержит исходные данные, формулы и ограничения модели."
    ),
    responses={
        **error_responses(400),
        200: success_response(
            "Рекомендация и объяснение без размещения заказа",
            FORECAST_RESPONSE,
        ),
    },
)
def post_forecast(
    data: Annotated[ForecastIn, Body(openapi_examples=FORECAST_EXAMPLES)],
    session: ReadSession,
    today: Today,
):
    return forecast(session, data, today)


@router.get(
    "/alerts",
    response_model=AlertPage,
    tags=["alerts"],
    summary="Получить предупреждения склада",
    operation_id="listAlerts",
    description=(
        "Коды: stock_shortage, expiring_batch, expired_batch, no_movement, never_moved. "
        "Дефицит: available_stock < средний расход × срок поставки, без входящих заказов. "
        "Пороги годности и бездействия заданы EXPIRY_WARNING_DAYS и INACTIVITY_DAYS. "
        "never_moved имеет location = null и возвращается только без фильтра объекта. "
        "Порядок: critical, warning, info; затем SKU, объект, код, партия."
    ),
    responses={
        200: success_response(
            "Страница предупреждений с исходными показателями",
            {
                "items": [ALERT_RESPONSE],
                "limit": 50,
                "offset": 0,
                "total": 1,
            },
        )
    },
)
def get_alerts(
    session: ReadSession,
    today: Today,
    sku: SkuFilter = None,
    location: LocationFilter = None,
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
