"""Add config_id and tenant_id to shipment table for SAP import context.

Revision ID: 20260313_shipment_config
Revises: -
"""
from alembic import op
import sqlalchemy as sa

revision = "20260313_shipment_config"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("shipment", sa.Column("config_id", sa.Integer(), nullable=True))
    op.add_column("shipment", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_shipment_config_id", "shipment", "supply_chain_configs",
        ["config_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_shipment_tenant_id", "shipment", "tenants",
        ["tenant_id"], ["id"],
    )
    op.create_index("idx_shipment_config", "shipment", ["config_id"])


def downgrade():
    op.drop_index("idx_shipment_config", table_name="shipment")
    op.drop_constraint("fk_shipment_tenant_id", "shipment", type_="foreignkey")
    op.drop_constraint("fk_shipment_config_id", "shipment", type_="foreignkey")
    op.drop_column("shipment", "tenant_id")
    op.drop_column("shipment", "config_id")
