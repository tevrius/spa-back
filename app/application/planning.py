from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.inventory import batch_balances, consumption_map, require_scope
from app.domain.errors import DomainError
from app.domain.forecast import (
    Delivery,
    PlanningLot,
    add_months,
    average_consumption,
    estimated_cost,
    exact_daily_rate,
    project,
    reorder_point,
    round_purchase,
)
from app.domain.inventory import ZERO
from app.infrastructure.models import Batch, Movement, PurchaseOrder, PurchaseOrderItem


def q(value: Decimal, places: str = "0.001") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def forecast(session: Session, data, today: date) -> dict:
    product = require_scope(session, data.sku, data.location)
    start = data.start_date or today
    if not today <= start <= today + timedelta(days=366):
        raise DomainError("invalid_period", "Start must be today or within the next 366 days")
    exclusive_end = (
        start + timedelta(days=data.days) if data.days else add_months(start, data.months)
    )
    end = exclusive_end - timedelta(days=1)
    days = (exclusive_end - start).days
    rows = batch_balances(session, data.sku, data.location)
    current = sum((quantity for _, quantity in rows), ZERO)
    available = sum((qty for b, qty in rows if b.expires_on >= today), ZERO)
    consumed = consumption_map(session, today, data.sku, data.location).get(
        (data.sku, data.location), ZERO
    )
    daily = average_consumption(consumed)
    rate = exact_daily_rate(consumed)
    safety = daily * data.safety_stock_days
    rop = reorder_point(daily, product.lead_time_days, data.safety_stock_days)
    order_rows = session.execute(
        select(PurchaseOrderItem, PurchaseOrder)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)
        .where(
            PurchaseOrderItem.sku == data.sku,
            PurchaseOrder.location == data.location,
            PurchaseOrder.status == "open",
            PurchaseOrderItem.quantity > PurchaseOrderItem.received_quantity,
        )
        .order_by(PurchaseOrder.due_on, PurchaseOrderItem.id)
    ).all()
    deliveries = [
        Delivery(item.id, item.quantity - item.received_quantity, order.due_on)
        for item, order in order_rows
        if today <= order.due_on <= end
    ]
    projection = project(
        as_of=today,
        start=start,
        end=end,
        daily=rate,
        safety=rate * data.safety_stock_days,
        lots=[PlanningLot(b.id, qty, b.expires_on) for b, qty in rows if b.expires_on >= today],
        deliveries=deliveries,
    )
    # Purchase must also cover shortages before a deferred forecast period starts.
    required = projection.unmet_demand + max(
        0, rate * data.safety_stock_days - projection.closing_stock
    )
    purchase = round_purchase(required, product.pack_size, product.minimum_order)
    latest = session.execute(
        select(Batch.unit_price, Movement.document_number)
        .join(Movement, Movement.batch_id == Batch.id)
        .where(
            Movement.sku == data.sku,
            Movement.location == data.location,
            Movement.operation == "receipt",
        )
        .order_by(Movement.occurred_on.desc(), Movement.id.desc())
        .limit(1)
    ).first()
    price = latest[0] if latest else None
    cost = estimated_cost(purchase, price)
    nominal_order_date = (
        projection.safety_breach_date - timedelta(days=product.lead_time_days)
        if projection.safety_breach_date
        else end - timedelta(days=product.lead_time_days)
    )
    order_date = max(today, nominal_order_date) if purchase > ZERO else None
    warnings = []

    def warn(code: str, message: str, level: str = "warning"):
        warnings.append({"code": code, "level": level, "message": message})

    if available < rop:
        warn("below_reorder_point", "Доступный остаток ниже точки заказа")
    if current > available:
        warn("expired_stock", "Просроченный остаток исключён из планирования; требуется writeoff")
    if projection.expired_quantity > ZERO:
        warn("projected_expiry", "Часть запаса истечёт до использования в расчётном горизонте")
    if projection.stockout_date:
        warn(
            "projected_shortage",
            "Без новой закупки прогнозируется неудовлетворённый спрос",
            "critical",
        )
        if projection.stockout_date < today + timedelta(days=product.lead_time_days):
            warn(
                "expedite_required", "Обычная поставка не успевает: требуется ускорение", "critical"
            )
    if any(order.due_on < today for _, order in order_rows):
        warn(
            "overdue_delivery", "Просроченные открытые поставки не учтены как гарантированный запас"
        )
    if price is None:
        warn("unknown_price", "Нет цены поступления: стоимость закупки неизвестна")
    first_movement = session.scalar(
        select(func.min(Movement.occurred_on)).where(
            Movement.sku == data.sku, Movement.location == data.location
        )
    )
    if first_movement is None or first_movement > today - timedelta(days=90):
        warn("short_history", "История короче 90 дней; средний расход всё равно делится на 90")
    if daily == ZERO:
        warn("no_consumption", "За последние 90 полных дней нет расхода", "info")
    incoming = sum((d.quantity for d in deliveries), ZERO)
    return {
        "sku": product.sku,
        "name": product.name,
        "unit": product.unit,
        "location": data.location,
        "period": {"from": start, "to": end, "days": days},
        "avg_daily_consumption": q(daily),
        "forecast_demand": q(daily * days),
        "current_stock": current,
        "available_stock": available,
        "incoming_qty": incoming,
        "safety_stock": q(safety),
        "reorder_point": q(rop),
        "recommended_purchase_qty": purchase,
        "unit_price": price,
        "estimated_cost": q(cost, "0.01") if cost is not None else None,
        "recommended_order_date": order_date,
        "stockout_date": projection.stockout_date,
        "warnings": warnings,
        "explanation": {
            "as_of": today,
            "data_used": [
                "Расход consume за последние 90 полных календарных дней",
                "Остатки из движений и сроков годности партий на дату расчёта",
                "Неполученное количество открытых заказов с датой от расчёта до конца периода",
            ],
            "formulas": [
                "средний расход = consume за 90 полных дней / 90",
                "прогноз = средний расход × календарные дни периода",
                "страховой запас = средний расход × страховые дни",
                "точка заказа = средний расход × (срок поставки + страховые дни)",
                "потребность = неудовлетворённый спрос с даты расчёта "
                "+ max(0, страховой запас − конечный запас)",
                "при потребности > 0: закупка = "
                "ceil(max(потребность, минимальный заказ) / упаковка) × упаковка",
                "стоимость = закупка × цена; округление HALF_UP до 0.01",
                "дата заказа = max(дата расчёта, "
                "дата достижения страхового запаса − срок поставки)",
            ],
            "assumptions": [
                "Постоянный средний спрос; "
                "сезонность, загрузка, инфляция и бюджетный лимит не моделируются",
                "Цена последнего поступления этого SKU на этот объект; одна валюта RUB",
                "Остаток на момент запроса принят начальным запасом сегодняшнего дня",
                "Поставка приходит в начале указанного дня; расход происходит после поступлений",
                "Товар пригоден включительно по дату годности; расход по FEFO",
                "Срок годности будущих поставок неизвестен: "
                "считаются пригодными до конца горизонта",
                "Дефицит — потерянный спрос; "
                "потребность учитывает его до и внутри выбранного периода",
                "Даты дефицита показаны без рекомендуемой новой закупки "
                "и только до конца горизонта",
                "Новая закупка — агрегированный объём; "
                "нужна проверка графика и срока годности у поставщика",
            ],
            "inputs": {
                "history_from": today - timedelta(days=90),
                "history_to": today - timedelta(days=1),
                "history_consumption": consumed,
                "avg_daily_unrounded": str(daily),
                "daily_rate_exact": str(rate),
                "lead_time_days": product.lead_time_days,
                "safety_stock_days": data.safety_stock_days,
                "pack_size": product.pack_size,
                "minimum_order": product.minimum_order,
                "price_document": latest[1] if latest else None,
                "projected_opening_stock": str(projection.opening_stock),
                "projected_closing_stock": str(projection.closing_stock),
                "unmet_demand": str(projection.unmet_demand),
                "projected_expiry_loss": str(projection.expired_quantity),
                "required_before_rounding": str(required),
                "bridge_days": (start - today).days,
                "bridge_demand": str(daily * (start - today).days),
                "deliveries": [
                    {"item_id": d.id, "due_on": d.due_on, "remaining_quantity": d.quantity}
                    for d in deliveries
                ],
            },
        },
    }
