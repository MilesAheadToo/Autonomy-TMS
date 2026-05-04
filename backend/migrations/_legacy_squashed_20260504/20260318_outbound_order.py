"""Add outbound_order header table and FK from outbound_order_line.

SC Entity: outbound_order
Mirrors the InboundOrder pattern for the outbound (customer/sales order) side.
OutboundOrderLine.order_id gains a nullable FK to outbound_order.id (SET NULL)
so existing rows without a parent order remain valid.

Revision ID: 20260318_outbound_order
Revises: 20260326_tenant_bsc_config
Create Date: 2026-03-18
"""

from alembic import op
import sqlalchemy as sa

revision = "20260318_outbound_order"
down_revision = "20260326_tenant_bsc_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create the outbound_order header table
    op.create_table(
        "outbound_order",
        sa.Column("id", sa.String(100), nullable=False),
        sa.Column("company_id", sa.String(100), nullable=True),
        sa.Column("order_type", sa.String(50), nullable=False),
        sa.Column("customer_id", sa.String(100), nullable=True),
        sa.Column("customer_name", sa.String(200), nullable=True),
        sa.Column("ship_from_site_id", sa.Integer(), nullable=True),
        sa.Column("ship_to_site_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(30),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("requested_delivery_date", sa.Date(), nullable=True),
        sa.Column("promised_delivery_date", sa.Date(), nullable=True),
        sa.Column("actual_delivery_date", sa.Date(), nullable=True),
        sa.Column(
            "total_ordered_qty",
            sa.Double(),
            server_default="0.0",
            nullable=True,
        ),
        sa.Column(
            "total_fulfilled_qty",
            sa.Double(),
            server_default="0.0",
            nullable=True,
        ),
        sa.Column("total_value", sa.Double(), nullable=True),
        sa.Column(
            "currency",
            sa.String(10),
            server_default="USD",
            nullable=True,
        ),
        sa.Column(
            "priority",
            sa.String(20),
            server_default="STANDARD",
            nullable=True,
        ),
        sa.Column("reference_number", sa.String(100), nullable=True),
        sa.Column("contract_id", sa.String(100), nullable=True),
        sa.Column("config_id", sa.Integer(), nullable=True),
        sa.Column("scenario_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("source_event_id", sa.String(100), nullable=True),
        sa.Column("source_update_dttm", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.ForeignKeyConstraint(["ship_from_site_id"], ["site.id"]),
        sa.ForeignKeyConstraint(["ship_to_site_id"], ["site.id"]),
        sa.ForeignKeyConstraint(
            ["config_id"],
            ["supply_chain_configs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"]),
    )
    op.create_index(
        "idx_outbound_order_status",
        "outbound_order",
        ["status", "order_type"],
    )
    op.create_index(
        "idx_outbound_order_customer",
        "outbound_order",
        ["customer_id"],
    )
    op.create_index(
        "idx_outbound_order_site",
        "outbound_order",
        ["ship_from_site_id", "requested_delivery_date"],
    )
    op.create_index(
        "idx_outbound_order_config",
        "outbound_order",
        ["config_id"],
    )

    # 2. Add nullable FK from outbound_order_line.order_id -> outbound_order.id
    op.create_foreign_key(
        "fk_outbound_order_line_order_id",
        "outbound_order_line",
        "outbound_order",
        ["order_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_outbound_order_line_order_id",
        "outbound_order_line",
        type_="foreignkey",
    )
    op.drop_index("idx_outbound_order_config", table_name="outbound_order")
    op.drop_index("idx_outbound_order_site", table_name="outbound_order")
    op.drop_index("idx_outbound_order_customer", table_name="outbound_order")
    op.drop_index("idx_outbound_order_status", table_name="outbound_order")
    op.drop_table("outbound_order")
