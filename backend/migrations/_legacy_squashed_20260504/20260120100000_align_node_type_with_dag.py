"""Store DAG node identifiers on nodes.type

Revision ID: 20260120100000_align_node_type_with_dag
Revises: 20260115090000_add_manufacturer_group_node_types
Create Date: 2026-01-20 10:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260120100000_align_node_type_with_dag"
down_revision = "20260115090000_add_manufacturer_group_node_types"
branch_labels = None
depends_on = None

NODE_TYPE_ENUM = sa.Enum(
    "RETAILER",
    "WHOLESALER",
    "DISTRIBUTOR",
    "INVENTORY",
    "MANUFACTURER",
    "SUPPLIER",
    "MARKET_DEMAND",
    "MARKET_SUPPLY",
    name="nodetype",
)


def upgrade() -> None:
    bind = op.get_bind()
    op.alter_column(
        "nodes",
        "type",
        existing_type=NODE_TYPE_ENUM,
        type_=sa.String(length=100),
        existing_nullable=False,
    )
    NODE_TYPE_ENUM.drop(bind, checkfirst=True)
    op.execute(
        sa.text("UPDATE nodes SET type = COALESCE(dag_type, LOWER(type))")
    )


def downgrade() -> None:
    bind = op.get_bind()
    NODE_TYPE_ENUM.create(bind, checkfirst=True)
    op.execute(
        sa.text("UPDATE nodes SET type = UPPER(type) WHERE type IS NOT NULL")
    )
    op.alter_column(
        "nodes",
        "type",
        existing_type=sa.String(length=100),
        type_=NODE_TYPE_ENUM,
        existing_nullable=False,
    )
