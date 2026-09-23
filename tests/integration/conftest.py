import os
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command
from alembic.config import Config
from app.config import business_today, get_settings
from app.infrastructure.database import session_factory
from app.infrastructure.models import Location, Product
from app.main import app

TODAY = date(2026, 9, 23)


@pytest.fixture(scope="session")
def test_database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set; use docker compose --profile test run --build --rm tests"
        )
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("Refusing database cleanup: test database name must end with _test")
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    session_factory.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    yield engine
    engine.dispose()
    session_factory().kw["bind"].dispose()
    session_factory.cache_clear()
    get_settings.cache_clear()
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous


@pytest.fixture
def client(test_database):
    with test_database.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE movement_allocations, movements, batches, purchase_order_items, "
                "purchase_orders, products, locations RESTART IDENTITY CASCADE"
            )
        )
    with session_factory()() as session, session.begin():
        session.add_all(
            [
                Product(
                    sku="OIL-001",
                    name="Oil",
                    unit="l",
                    pack_size=Decimal(5),
                    minimum_order=Decimal(10),
                    lead_time_days=7,
                ),
                Product(
                    sku="NEW-001",
                    name="New",
                    unit="pcs",
                    pack_size=Decimal(1),
                    minimum_order=Decimal(1),
                    lead_time_days=1,
                ),
                Location(code="MS-01", name="First"),
                Location(code="MS-02", name="Second"),
            ]
        )
    app.dependency_overrides[business_today] = lambda: TODAY
    with TestClient(app) as http:
        yield http
    app.dependency_overrides.clear()
