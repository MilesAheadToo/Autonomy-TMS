"""Add HANA DB columns to sap_connections

Revision ID: 20260310_hana_db
Revises: None (standalone migration)
Create Date: 2026-03-10

Adds hana_schema and hana_port columns to support direct HANA DB
connections as a 5th connection method alongside RFC, CSV, OData, IDoc.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260310_hana_db"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sap_connections",
        sa.Column("hana_schema", sa.String(100), nullable=True, server_default="SAPHANADB"),
    )
    op.add_column(
        "sap_connections",
        sa.Column("hana_port", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column("sap_connections", "hana_port")
    op.drop_column("sap_connections", "hana_schema")
