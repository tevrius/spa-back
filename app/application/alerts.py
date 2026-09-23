from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.inventory import batch_balances, consumption_map, scoped, stock_rows
from app.domain.forecast import average_consumption
from app.domain.inventory import ZERO
from app.infrastructure.models import Movement, Product


def alerts(
    session: Session, today: date, expiry_days: int, inactivity_days: int, sku=None, location=None
):
    stocks = stock_rows(session, today, sku, location)
    batches = batch_balances(session, sku, location)
    consumption = consumption_map(session, today, sku, location)
    products = {p.sku: p for p in session.scalars(select(Product))}
    last = {
        (s, loc): day
        for s, loc, day in session.execute(
            scoped(
                select(Movement.sku, Movement.location, func.max(Movement.occurred_on)).group_by(
                    Movement.sku, Movement.location
                ),
                Movement,
                sku,
                location,
            )
        )
    }
    result = []
    for stock in stocks:
        key = stock["sku"], stock["location"]
        daily = average_consumption(consumption.get(key, ZERO))
        lead = products[key[0]].lead_time_days
        if daily > ZERO and stock["available_stock"] < daily * lead:
            result.append(
                {
                    "code": "stock_shortage",
                    "level": "critical",
                    "message": "Доступного запаса меньше, чем расхода за срок поставки",
                    "sku": key[0],
                    "location": key[1],
                    "metrics": {
                        "available_stock": stock["available_stock"],
                        "avg_daily_consumption": str(daily),
                        "lead_time_days": lead,
                        "days_of_stock": stock["days_of_stock"],
                        "incoming_included": False,
                    },
                }
            )
        last_date = last.get(key)
        if last_date and (today - last_date).days >= inactivity_days:
            result.append(
                {
                    "code": "no_movement",
                    "level": "info",
                    "message": "Давно не было движений",
                    "sku": key[0],
                    "location": key[1],
                    "metrics": {
                        "last_movement_date": last_date,
                        "days_without_movement": (today - last_date).days,
                        "threshold_days": inactivity_days,
                    },
                }
            )
    # An unstocked catalog item has no meaningful location scope yet.
    if location is None:
        for product in products.values():
            if sku is not None and product.sku != sku:
                continue
            if not any(key[0] == product.sku for key in last):
                result.append(
                    {
                        "code": "never_moved",
                        "level": "info",
                        "message": "У товара нет истории движения",
                        "sku": product.sku,
                        "location": None,
                        "metrics": {"last_movement_date": None, "threshold_days": inactivity_days},
                    }
                )
    for batch, quantity in batches:
        left = (batch.expires_on - today).days
        if quantity > ZERO and left <= expiry_days:
            result.append(
                {
                    "code": "expired_batch" if left < 0 else "expiring_batch",
                    "level": "critical" if left < 0 else "warning",
                    "message": "Партия просрочена: требуется writeoff"
                    if left < 0
                    else "Приближается срок годности",
                    "sku": batch.sku,
                    "location": batch.location,
                    "batch": batch.code,
                    "metrics": {
                        "remaining_quantity": quantity,
                        "expires_on": batch.expires_on,
                        "days_to_expiry": left,
                        "threshold_days": expiry_days,
                    },
                }
            )
    ranks = {"critical": 0, "warning": 1, "info": 2}
    return sorted(
        result,
        key=lambda a: (
            ranks[a["level"]],
            a["sku"],
            a["location"] or "",
            a["code"],
            a.get("batch", ""),
        ),
    )
