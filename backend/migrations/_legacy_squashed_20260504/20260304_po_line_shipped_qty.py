"""Add shipped_quantity to purchase_order_line_item.

The ORM model defines shipped_quantity but it was never added to the DB table.

Revision ID: 20260304_po_line_shipped_qty
Revises: 20260304_transfer_order_sim_cols
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa

revision = "20260304_po_line_shipped_qty"
down_revision = "20260304_transfer_order_sim_cols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {c["name"] for c in inspector.get_columns("purchase_order_line_item")}

    if "shipped_quantity" not in existing:
        op.add_column(
            "purchase_order_line_item",
            sa.Column(
                "shipped_quantity",
                sa.Double(),
                server_default=sa.text("0.0"),
                nullable=True,
                comment="Fulfilled amount (simulation extension)",
            ),
        )


def downgrade() -> None:
    op.drop_column("purchase_order_line_item", "shipped_quantity")
