from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"
    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    unit: Mapped[str] = mapped_column(String(32))
    pack_size: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    minimum_order: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    lead_time_days: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        CheckConstraint("pack_size > 0 AND minimum_order >= 0 AND lead_time_days >= 0"),
    )


class Location(Base):
    __tablename__ = "locations"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))


class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(128))
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"))
    location: Mapped[str] = mapped_column(ForeignKey("locations.code"))
    received_on: Mapped[date] = mapped_column(Date)
    expires_on: Mapped[date] = mapped_column(Date)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    receipt_document: Mapped[str] = mapped_column(String(128))
    __table_args__ = (
        UniqueConstraint("sku", "location", "code", name="uq_batch_scope_code"),
        CheckConstraint("unit_price >= 0"),
        CheckConstraint("expires_on >= received_on"),
    )


class Movement(Base):
    __tablename__ = "movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"))
    location: Mapped[str] = mapped_column(ForeignKey("locations.code"))
    operation: Mapped[str] = mapped_column(String(16))
    occurred_on: Mapped[date] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id"))
    document_number: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("operation IN ('receipt','consume','writeoff','return','correction')"),
        CheckConstraint("quantity <> 0 AND (operation = 'correction' OR quantity > 0)"),
        CheckConstraint(
            "(operation = 'consume' AND batch_id IS NULL) OR "
            "(operation <> 'consume' AND batch_id IS NOT NULL)"
        ),
        Index("ix_movement_scope_date", "sku", "location", "occurred_on", "id"),
    )


class MovementAllocation(Base):
    __tablename__ = "movement_allocations"
    movement_id: Mapped[int] = mapped_column(ForeignKey("movements.id"), primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), primary_key=True)
    delta: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    __table_args__ = (CheckConstraint("delta <> 0"), Index("ix_allocation_batch", "batch_id"))


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_number: Mapped[str] = mapped_column(String(128), unique=True)
    location: Mapped[str] = mapped_column(ForeignKey("locations.code"))
    due_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))
    __table_args__ = (CheckConstraint("status IN ('open','received','cancelled')"),)


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    received_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"))
    __table_args__ = (
        UniqueConstraint("order_id", "sku"),
        CheckConstraint(
            "quantity > 0 AND received_quantity >= 0 AND received_quantity <= quantity"
        ),
    )
