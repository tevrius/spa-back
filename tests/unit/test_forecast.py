from datetime import date, timedelta
from decimal import Decimal as D

import pytest

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

TODAY = date(2026, 9, 23)


@pytest.mark.parametrize(
    "required,pack,minimum,expected",
    [
        ("73.98", "5", "10", "75"),
        ("1", "6", "10", "12"),
        ("0", "5", "10", "0"),
        ("-5", "5", "10", "0"),
        ("10", "5", "10", "10"),
        ("0.751", "0.25", "0.5", "1.00"),
    ],
)
def test_rounding_and_minimum(required, pack, minimum, expected):
    assert round_purchase(D(required), D(pack), D(minimum)) == D(expected)


def test_average_and_reorder_point():
    daily = average_consumption(D(180))
    assert daily == D(2)
    assert reorder_point(daily, 7, 14) == D(42)


def test_cost_uses_exact_decimal_arithmetic():
    assert estimated_cost(D(75), D("1259.05")) == D("94428.75")
    assert estimated_cost(D(75), None) is None


@pytest.mark.parametrize(
    "start,months,end",
    [
        (date(2026, 1, 31), 1, date(2026, 2, 28)),
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2026, 10, 1), 3, date(2027, 1, 1)),
    ],
)
def test_calendar_months(start, months, end):
    assert add_months(start, months) == end


def projection(lots=(), deliveries=(), days=10, start=TODAY, daily=D(1)):
    return project(
        as_of=TODAY,
        start=start,
        end=TODAY + timedelta(days=days - 1),
        daily=daily,
        safety=D(0),
        lots=list(lots),
        deliveries=list(deliveries),
    )


def test_late_delivery_does_not_hide_earlier_shortage():
    result = projection(
        [PlanningLot(1, D(2), TODAY + timedelta(days=30))],
        [Delivery(1, D(20), TODAY + timedelta(days=5))],
    )
    assert result.stockout_date == TODAY + timedelta(days=2)
    assert result.unmet_demand == D(3)
    assert result.closing_stock == D(15)


def test_expiry_loss_increases_uncovered_demand():
    result = projection([PlanningLot(1, D(10), TODAY)])
    assert result.expired_quantity == D(9)
    assert result.unmet_demand == D(9)


def test_future_period_uses_projected_opening_stock():
    result = projection(
        [PlanningLot(1, D(20), TODAY + timedelta(days=30))], start=TODAY + timedelta(days=5)
    )
    assert result.opening_stock == D(15)
    assert result.closing_stock == D(10)


def test_zero_consumption_has_no_stockout():
    result = projection(daily=D(0))
    assert result.stockout_date is None
    assert result.safety_breach_date is None
    assert result.unmet_demand == 0


def test_delivery_on_shortage_date_arrives_before_demand():
    result = projection(deliveries=[Delivery(1, D(10), TODAY)])
    assert result.stockout_date is None
    assert result.unmet_demand == 0


@pytest.mark.parametrize("consumed", ["1", "5", "7", "13"])
def test_repeating_daily_rate_does_not_create_phantom_shortage(consumed):
    result = projection(
        [PlanningLot(1, D(consumed), TODAY + timedelta(days=100))],
        days=90,
        daily=exact_daily_rate(D(consumed)),
    )
    assert result.stockout_date is None
    assert round_purchase(result.unmet_demand, D(1), D(1)) == 0
