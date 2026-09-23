"""Initial inventory ledger and expected deliveries."""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "products",
        sa.Column("sku", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("pack_size", sa.Numeric(18, 3), nullable=False),
        sa.Column("minimum_order", sa.Numeric(18, 3), nullable=False),
        sa.Column("lead_time_days", sa.Integer, nullable=False),
        sa.CheckConstraint("pack_size > 0 AND minimum_order >= 0 AND lead_time_days >= 0"),
    )
    op.create_table(
        "locations",
        sa.Column("code", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
    )
    op.create_table(
        "batches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("sku", sa.String(64), sa.ForeignKey("products.sku"), nullable=False),
        sa.Column("location", sa.String(64), sa.ForeignKey("locations.code"), nullable=False),
        sa.Column("received_on", sa.Date, nullable=False),
        sa.Column("expires_on", sa.Date, nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("receipt_document", sa.String(128), nullable=False),
        sa.UniqueConstraint("sku", "location", "code", name="uq_batch_scope_code"),
        sa.CheckConstraint("unit_price >= 0"),
        sa.CheckConstraint("expires_on >= received_on"),
    )
    op.create_table(
        "movements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sku", sa.String(64), sa.ForeignKey("products.sku"), nullable=False),
        sa.Column("location", sa.String(64), sa.ForeignKey("locations.code"), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("occurred_on", sa.Date, nullable=False),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("batch_id", sa.Integer, sa.ForeignKey("batches.id")),
        sa.Column("document_number", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("operation IN ('receipt','consume','writeoff','return','correction')"),
        sa.CheckConstraint("quantity <> 0 AND (operation = 'correction' OR quantity > 0)"),
        sa.CheckConstraint(
            "(operation = 'consume' AND batch_id IS NULL) OR "
            "(operation <> 'consume' AND batch_id IS NOT NULL)"
        ),
    )
    op.create_index("ix_movement_scope_date", "movements", ["sku", "location", "occurred_on", "id"])
    op.create_table(
        "movement_allocations",
        sa.Column("movement_id", sa.Integer, sa.ForeignKey("movements.id"), primary_key=True),
        sa.Column("batch_id", sa.Integer, sa.ForeignKey("batches.id"), primary_key=True),
        sa.Column("delta", sa.Numeric(18, 3), nullable=False),
        sa.CheckConstraint("delta <> 0"),
    )
    op.create_index("ix_allocation_batch", "movement_allocations", ["batch_id"])
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_number", sa.String(128), nullable=False, unique=True),
        sa.Column("location", sa.String(64), sa.ForeignKey("locations.code"), nullable=False),
        sa.Column("due_on", sa.Date, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.CheckConstraint("status IN ('open','received','cancelled')"),
    )
    op.create_table(
        "purchase_order_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("purchase_orders.id"), nullable=False),
        sa.Column("sku", sa.String(64), sa.ForeignKey("products.sku"), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("received_quantity", sa.Numeric(18, 3), nullable=False),
        sa.UniqueConstraint("order_id", "sku"),
        sa.CheckConstraint(
            "quantity > 0 AND received_quantity >= 0 AND received_quantity <= quantity"
        ),
    )


def downgrade():
    for table in (
        "purchase_order_items",
        "purchase_orders",
        "movement_allocations",
        "movements",
        "batches",
        "locations",
        "products",
    ):
        op.drop_table(table)
