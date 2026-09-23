"""Idempotent demo fixture. Uses the same validated movement workflow as the API."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text

from app.api.schemas import MovementIn
from app.application.inventory import create_movement
from app.config import business_today
from app.infrastructure.database import session_factory
from app.infrastructure.models import Location, Movement, Product, PurchaseOrder, PurchaseOrderItem


def seed():
    today = business_today()
    with session_factory()() as session, session.begin():
        session.execute(text("SELECT pg_advisory_xact_lock(782398451)"))
        for code, name in [("MS-01", "Основной спа"), ("MS-02", "Городской спа")]:
            if session.get(Location, code) is None:
                session.add(Location(code=code, name=name))
        catalog = [
            ("OIL-001", "Массажное масло базовое (миндаль)", "л", "5", "10", 7),
            ("MASK-001", "Маска для обёртывания", "кг", "2", "6", 10),
            ("SALT-001", "Соль для процедур", "кг", "5", "10", 5),
            ("NEW-001", "Новый крем без истории", "шт", "1", "1", 3),
        ]
        for sku, name, unit, pack, minimum, lead in catalog:
            if session.get(Product, sku) is None:
                session.add(
                    Product(
                        sku=sku,
                        name=name,
                        unit=unit,
                        pack_size=Decimal(pack),
                        minimum_order=Decimal(minimum),
                        lead_time_days=lead,
                    )
                )
        session.flush()
        # A completed marker freezes the original seed dates on subsequent starts.
        if session.scalar(select(Movement.id).where(Movement.document_number == "DEMO-COMPLETE")):
            return

        def move(sku, op, qty, days_ago, doc, batch=None, expires_in=None, price=None, loc="MS-01"):
            payload = {
                "date": today - timedelta(days=days_ago),
                "sku": sku,
                "location": loc,
                "operation": op,
                "quantity": str(qty),
                "document_number": doc,
                "batch": batch,
            }
            if expires_in is not None:
                payload.update(expires_on=today + timedelta(days=expires_in), unit_price=price)
            create_movement(session, MovementIn(**payload), today)

        move("OIL-001", "receipt", 160, 100, "DEMO-OIL-A", "OIL-A", 15, "1259.05")
        move("OIL-001", "receipt", 80, 95, "DEMO-OIL-B", "OIL-B", 180, "1300.00")
        for n, days in enumerate(range(89, 0, -10), 1):
            move("OIL-001", "consume", 20, days, f"DEMO-OIL-C{n}")
        move("OIL-001", "receipt", 10, 20, "DEMO-OIL-MS02", "OIL-C", 120, "1270.00", "MS-02")
        move("MASK-001", "receipt", 10, 80, "DEMO-MASK-OLD", "MASK-OLD", -2, "800.00")
        move("MASK-001", "receipt", 45, 75, "DEMO-MASK-FRESH", "MASK-FRESH", 30, "850.00")
        move("MASK-001", "consume", 53, 10, "DEMO-MASK-C")
        # A separate untouched expired batch demonstrates physical versus usable stock.
        move("MASK-001", "receipt", 4, 5, "DEMO-MASK-EXPIRED", "MASK-EXPIRED", -1, "810.00")
        move("SALT-001", "receipt", 30, 120, "DEMO-SALT", "SALT-A", 300, "150.00")
        order = PurchaseOrder(
            document_number="DEMO-PO-001",
            location="MS-01",
            due_on=today + timedelta(days=5),
            status="open",
        )
        session.add(order)
        session.flush()
        session.add(
            PurchaseOrderItem(
                order_id=order.id,
                sku="OIL-001",
                quantity=Decimal("20"),
                received_quantity=Decimal("0"),
            )
        )
        move("SALT-001", "correction", 1, 120, "DEMO-COMPLETE", "SALT-A")
    print("Demo data loaded")


if __name__ == "__main__":
    seed()
