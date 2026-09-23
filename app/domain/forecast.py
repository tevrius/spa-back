"""Deterministic planning: Decimal arithmetic and explicit calendar inputs."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

from app.domain.inventory import ZERO


def average_consumption(consumed: Decimal, window_days: int = 90) -> Decimal:
    if consumed < ZERO or window_days <= 0:
        raise ValueError("Consumption must be nonnegative and window must be positive")
    return consumed / Decimal(window_days)


def reorder_point(daily: Decimal, lead_days: int, safety_days: int) -> Decimal:
    if daily < ZERO or min(lead_days, safety_days) < 0:
        raise ValueError("Planning parameters must be nonnegative")
    return daily * Decimal(lead_days + safety_days)


def round_purchase(required: Decimal | Fraction, pack: Decimal, minimum: Decimal) -> Decimal:
    if pack <= ZERO or minimum < ZERO:
        raise ValueError("Invalid supplier constraints")
    if required <= ZERO:
        return ZERO
    packs = max(Fraction(required), Fraction(minimum)) / Fraction(pack)
    return (-(-packs.numerator // packs.denominator)) * pack


def exact_daily_rate(consumed: Decimal, window_days: int = 90) -> Fraction:
    if consumed < ZERO or window_days <= 0:
        raise ValueError("Consumption must be nonnegative and window must be positive")
    return Fraction(consumed) / window_days


def estimated_cost(quantity: Decimal, price: Decimal | None) -> Decimal | None:
    if quantity < ZERO or (price is not None and price < ZERO):
        raise ValueError("Quantity and price must be nonnegative")
    return None if price is None else quantity * price


def add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


@dataclass(frozen=True)
class PlanningLot:
    id: int
    quantity: Decimal
    expires_on: date


@dataclass(frozen=True)
class Delivery:
    id: int
    quantity: Decimal
    due_on: date


@dataclass(frozen=True)
class Projection:
    opening_stock: Fraction
    closing_stock: Fraction
    unmet_demand: Fraction
    expired_quantity: Fraction
    stockout_date: date | None
    safety_breach_date: date | None


@dataclass
class _ProjectedLot:
    expires_on: date
    id: int
    quantity: Fraction


def project(
    *,
    as_of: date,
    start: date,
    end: date,
    daily: Decimal | Fraction,
    safety: Decimal | Fraction,
    lots: list[PlanningLot],
    deliveries: list[Delivery],
) -> Projection:
    """Project current usable lots and confirmed receipts, consume by FEFO each day.

    Receipts arrive at the start of their due date. Unmet demand is lost demand,
    not negative inventory. Expected receipts have unknown expiry (assumed usable
    through the horizon); the API explicitly discloses that assumption.
    """
    if not as_of <= start <= end or daily < ZERO or safety < ZERO:
        raise ValueError("Invalid projection parameters")
    rate = Fraction(daily)
    threshold = Fraction(safety)
    inventory = [
        _ProjectedLot(lot.expires_on, lot.id, Fraction(lot.quantity))
        for lot in lots
        if lot.quantity > ZERO
    ]
    incoming: dict[date, list[Delivery]] = {}
    for delivery in deliveries:
        if as_of <= delivery.due_on <= end and delivery.quantity > ZERO:
            incoming.setdefault(delivery.due_on, []).append(delivery)
    expired = unmet = opening = Fraction(0)
    stockout = safety_breach = None
    current = as_of
    while current <= end:
        for item in inventory:
            if item.expires_on < current:
                expired += item.quantity
                item.quantity = Fraction(0)
        for delivery in incoming.get(current, []):
            inventory.append(_ProjectedLot(date.max, delivery.id, Fraction(delivery.quantity)))
        inventory.sort(key=lambda item: (item.expires_on, item.id))
        if current == start:
            opening = sum((item.quantity for item in inventory), Fraction(0))
        remaining = rate
        for item in inventory:
            take = min(item.quantity, remaining)
            item.quantity -= take
            remaining -= take
            if remaining == ZERO:
                break
        if remaining > ZERO:
            unmet += remaining
            if stockout is None:
                stockout = current
        closing = sum((item.quantity for item in inventory), Fraction(0))
        if rate > ZERO and closing <= threshold and safety_breach is None:
            safety_breach = current
        current += timedelta(days=1)
    return Projection(opening, closing, unmet, expired, stockout, safety_breach)
