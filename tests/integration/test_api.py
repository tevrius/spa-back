from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.infrastructure.database import session_factory
from app.infrastructure.models import Movement, PurchaseOrder, PurchaseOrderItem
from tests.integration.conftest import TODAY

pytestmark = pytest.mark.integration


def payload(doc="R1", **changes):
    data = {
        "date": str(TODAY - timedelta(days=90)),
        "sku": "OIL-001",
        "location": "MS-01",
        "operation": "receipt",
        "quantity": "100",
        "batch": "B1",
        "document_number": doc,
        "expires_on": str(TODAY + timedelta(days=180)),
        "unit_price": "12.55",
    }
    data.update(changes)
    return data


def consume(client, doc="C1", quantity="1", **changes):
    data = {
        "date": str(TODAY),
        "sku": "OIL-001",
        "location": "MS-01",
        "operation": "consume",
        "quantity": quantity,
        "document_number": doc,
    }
    data.update(changes)
    return client.post("/api/movements", json=data)


def test_receipt_detail_and_pagination(client):
    result = client.post("/api/movements", json=payload())
    assert result.status_code == 201, result.text
    assert result.json()["current_stock"] == 100
    details = client.get("/api/stock/OIL-001").json()
    assert details["locations"][0]["batches"][0]["receipt_documents"] == ["R1"]
    page = client.get(
        "/api/movements?sku=OIL-001&location=MS-01&operation=receipt&limit=1&offset=1"
    )
    assert page.json() == {"items": [], "limit": 1, "offset": 1, "total": 1}


def test_api_fefo_and_expired_stock(client):
    assert client.post("/api/movements", json=payload(quantity="5")).status_code == 201
    assert (
        client.post(
            "/api/movements", json=payload("R2", batch="B2", quantity="3", expires_on=str(TODAY))
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/movements",
            json=payload(
                "R3", batch="B3", quantity="10", expires_on=str(TODAY - timedelta(days=1))
            ),
        ).status_code
        == 201
    )
    result = consume(client, quantity="6")
    assert result.status_code == 201, result.text
    assert result.json()["allocations"] == [
        {"batch": "B2", "quantity_delta": -3},
        {"batch": "B1", "quantity_delta": -3},
    ]
    assert result.json()["current_stock"] == 12
    assert result.json()["available_stock"] == 2
    alerts = client.get("/api/alerts").json()["items"]
    assert any(a["code"] == "expired_batch" and a["batch"] == "B3" for a in alerts)


def test_insufficient_stock_rolls_back(client):
    client.post("/api/movements", json=payload(quantity="5"))
    result = consume(client, quantity="6")
    assert result.status_code == 422
    assert Decimal(result.json()["error"]["details"]["available"]) == 5
    assert client.get("/api/movements").json()["total"] == 1
    assert client.get("/api/stock").json()["items"][0]["current_stock"] == 5


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"sku": "missing"}, 404),
        ({"location": "missing"}, 404),
        ({"quantity": "0"}, 422),
        ({"quantity": "-1"}, 422),
        ({"date": str(TODAY + timedelta(days=1))}, 422),
        ({"quantity": "0.0001"}, 422),
        ({"quantity": "NaN"}, 422),
        ({"expires_on": None, "unit_price": None}, 422),
    ],
)
def test_movement_validation(client, changes, status):
    response = client.post("/api/movements", json=payload(**changes))
    assert response.status_code == status, response.text
    assert "error" in response.json()


def test_duplicate_document_is_conflict(client):
    assert client.post("/api/movements", json=payload()).status_code == 201
    assert client.post("/api/movements", json=payload()).status_code == 409


def test_invalid_json_is_400(client):
    result = client.post(
        "/api/movements", content="{", headers={"Content-Type": "application/json"}
    )
    assert result.status_code == 400


def test_future_receipt_does_not_cover_past_consumption(client):
    client.post("/api/movements", json=payload(date=str(TODAY)))
    result = consume(client, date=str(TODAY - timedelta(days=1)))
    assert result.status_code == 422
    assert client.get("/api/movements").json()["total"] == 1


def test_backdated_receipt_reallocates_existing_consumption(client):
    client.post("/api/movements", json=payload(quantity="10"))
    result = consume(client, quantity="5")
    assert result.json()["allocations"][0]["batch"] == "B1"
    added = client.post(
        "/api/movements",
        json=payload(
            "R2",
            batch="B2",
            quantity="3",
            date=str(TODAY - timedelta(days=1)),
            expires_on=str(TODAY + timedelta(days=2)),
        ),
    )
    assert added.status_code == 201
    movement = client.get("/api/movements?operation=consume").json()["items"][0]
    assert movement["allocations"] == [
        {"batch": "B2", "quantity_delta": -3},
        {"batch": "B1", "quantity_delta": -2},
    ]


def test_rejected_backdated_correction_keeps_existing_allocations(client):
    client.post("/api/movements", json=payload(quantity="10"))
    consume(client, quantity="8")
    before = client.get("/api/movements").json()
    correction = {
        "date": str(TODAY - timedelta(days=1)),
        "sku": "OIL-001",
        "location": "MS-01",
        "operation": "correction",
        "quantity": "-3",
        "batch": "B1",
        "document_number": "X1",
    }
    assert client.post("/api/movements", json=correction).status_code == 422
    assert client.get("/api/movements").json() == before


def test_locations_are_isolated(client):
    client.post("/api/movements", json=payload())
    assert consume(client, location="MS-02").status_code == 422


def test_concurrent_consumption_cannot_overdraw(client):
    client.post("/api/movements", json=payload(quantity="10"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda doc: consume(client, doc, "8"), ["C1", "C2"]))
    assert sorted(r.status_code for r in results) == [201, 422]
    assert client.get("/api/stock").json()["items"][0]["current_stock"] == 2


def test_concurrent_duplicate_document_has_one_winner(client):
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda loc: client.post("/api/movements", json=payload(location=loc)),
                ["MS-01", "MS-02"],
            )
        )
    assert sorted(r.status_code for r in results) == [201, 409]
    assert client.get("/api/movements").json()["total"] == 1


def test_forecast_contract_math_and_explanation(client):
    client.post("/api/movements", json=payload(quantity="100"))
    assert consume(client, quantity="90", date=str(TODAY - timedelta(days=1))).status_code == 201
    response = client.post(
        "/api/forecast",
        json={"sku": "OIL-001", "location": "MS-01", "days": 30, "safety_stock_days": 14},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["avg_daily_consumption"] == 1
    assert data["reorder_point"] == 21
    assert data["forecast_demand"] == 30
    assert data["recommended_purchase_qty"] == 35
    assert data["estimated_cost"] == 439.25
    assert data["stockout_date"] == str(TODAY + timedelta(days=10))
    assert data["recommended_order_date"] == str(TODAY)
    assert data["explanation"]["inputs"]["price_document"] == "R1"


def test_late_and_overdue_orders_do_not_hide_shortage(client):
    client.post("/api/movements", json=payload(quantity="92"))
    consume(client, quantity="90", date=str(TODAY - timedelta(days=1)))
    with session_factory()() as session, session.begin():
        for n, days, status in [(1, 5, "open"), (2, -1, "open"), (3, 1, "cancelled")]:
            order = PurchaseOrder(
                document_number=f"P{n}",
                location="MS-01",
                due_on=TODAY + timedelta(days=days),
                status=status,
            )
            session.add(order)
            session.flush()
            session.add(
                PurchaseOrderItem(
                    order_id=order.id,
                    sku="OIL-001",
                    quantity=Decimal(25),
                    received_quantity=Decimal(5),
                )
            )
    data = client.post(
        "/api/forecast",
        json={"sku": "OIL-001", "location": "MS-01", "days": 10, "safety_stock_days": 0},
    ).json()
    assert data["incoming_qty"] == 20
    assert data["stockout_date"] == str(TODAY + timedelta(days=2))
    assert data["recommended_purchase_qty"] == 10
    assert "overdue_delivery" in {warning["code"] for warning in data["warnings"]}


def test_forecast_without_history_or_price(client):
    response = client.post(
        "/api/forecast", json={"sku": "NEW-001", "location": "MS-01", "months": 3}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["recommended_purchase_qty"] == 0
    assert result["unit_price"] is None
    assert result["estimated_cost"] is None
    assert result["stockout_date"] is None
    assert result["recommended_order_date"] is None
    assert result["period"]["days"] == 91


@pytest.mark.parametrize(
    "horizon", [{}, {"days": 0}, {"days": 1, "months": 1}, {"months": 13}, {"days": 1.5}]
)
def test_forecast_horizon_validation(client, horizon):
    assert (
        client.post(
            "/api/forecast", json={"sku": "OIL-001", "location": "MS-01", **horizon}
        ).status_code
        == 422
    )


def test_alerts_inactivity_and_never_moved(client):
    client.post("/api/movements", json=payload())
    codes = {row["code"] for row in client.get("/api/alerts").json()["items"]}
    assert {"no_movement", "never_moved"} <= codes


def test_seed_is_idempotent(client, monkeypatch):
    import scripts.seed as seed_module

    monkeypatch.setattr(seed_module, "business_today", lambda: TODAY)
    seed_module.seed()
    with session_factory()() as session:
        before = session.scalar(select(func.count()).select_from(Movement))
    monkeypatch.setattr(seed_module, "business_today", lambda: TODAY + timedelta(days=30))
    seed_module.seed()
    with session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(Movement)) == before
    alerts = client.get("/api/alerts").json()["items"]
    assert "expired_batch" in {a["code"] for a in alerts}


def test_database_schema_matches_migrations(client):
    from alembic import command
    from alembic.config import Config

    command.check(Config("alembic.ini"))


@pytest.mark.parametrize("consumed", ["1", "5", "7", "13"])
def test_exact_daily_rate_does_not_order_an_extra_pack(client, consumed):
    client.post("/api/movements", json=payload(quantity=str(2 * Decimal(consumed))))
    consume(client, quantity=consumed, date=str(TODAY - timedelta(days=1)))
    result = client.post(
        "/api/forecast",
        json={
            "sku": "OIL-001",
            "location": "MS-01",
            "days": 90,
            "safety_stock_days": 0,
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["recommended_purchase_qty"] == 0
    assert result.json()["stockout_date"] is None


def test_history_window_includes_day_90_but_excludes_today_and_day_91(client):
    client.post("/api/movements", json=payload(date=str(TODAY - timedelta(days=100))))
    consume(client, "C-91", "20", date=str(TODAY - timedelta(days=91)))
    consume(client, "C-90", "9", date=str(TODAY - timedelta(days=90)))
    consume(client, "C0", "10")
    stock = client.get("/api/stock").json()["items"][0]
    assert stock["avg_daily_consumption"] == 0.1
    assert stock["current_stock"] == 61


def test_return_correction_and_writeoff_are_not_consumption(client):
    client.post("/api/movements", json=payload(quantity="10"))
    for op, qty in [("return", "2"), ("correction", "-1"), ("writeoff", "3")]:
        response = client.post(
            "/api/movements",
            json={
                "date": str(TODAY - timedelta(days=1)),
                "sku": "OIL-001",
                "location": "MS-01",
                "batch": "B1",
                "operation": op,
                "quantity": qty,
                "document_number": op,
            },
        )
        assert response.status_code == 201, response.text
    stock = client.get("/api/stock").json()["items"][0]
    assert stock["current_stock"] == 8
    assert stock["avg_daily_consumption"] == 0
    assert stock["days_of_stock"] is None


def test_expired_batch_writeoff_removes_expiry_alert(client):
    client.post(
        "/api/movements", json=payload(quantity="5", expires_on=str(TODAY - timedelta(days=1)))
    )
    assert consume(client).status_code == 422
    result = client.post(
        "/api/movements",
        json={
            "date": str(TODAY),
            "sku": "OIL-001",
            "location": "MS-01",
            "batch": "B1",
            "operation": "writeoff",
            "quantity": "5",
            "document_number": "WRITE-OFF",
        },
    )
    assert result.status_code == 201
    assert result.json()["current_stock"] == 0
    assert not any(a["code"] == "expired_batch" for a in client.get("/api/alerts").json()["items"])


def test_batch_metadata_cannot_be_overwritten(client):
    client.post("/api/movements", json=payload())
    assert client.post("/api/movements", json=payload("R2", unit_price="20")).status_code == 422
    assert client.get("/api/movements").json()["total"] == 1


def test_forecast_uses_bridge_demand_and_expiry_losses(client):
    client.post(
        "/api/movements", json=payload(quantity="95", expires_on=str(TODAY + timedelta(days=1)))
    )
    consume(client, quantity="90", date=str(TODAY - timedelta(days=1)))
    result = client.post(
        "/api/forecast",
        json={
            "sku": "OIL-001",
            "location": "MS-01",
            "days": 10,
            "safety_stock_days": 0,
            "start_date": str(TODAY + timedelta(days=5)),
        },
    )
    assert result.status_code == 200
    data = result.json()
    assert data["forecast_demand"] == 10
    assert data["recommended_purchase_qty"] == 15
    assert data["stockout_date"] == str(TODAY + timedelta(days=2))
    assert data["explanation"]["inputs"]["projected_expiry_loss"] == "3"


def test_openapi_and_health_endpoints(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_migration_downgrade_and_upgrade(client):
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)
    assert client.get("/health/ready").status_code == 200
