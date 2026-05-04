"""Add file_table_mapping JSONB column to sap_connections.

Stores per-file SAP table identification results from connection testing:
[{"filename": "MARA.csv", "table": "MARA", "confidence": 0.95, "row_count": 936, "columns": [...], "confirmed": true}, ...]

Revision ID: 20260313_sap_file_mapping
Revises: 20260313_sap_table_status
Create Date: 2026-03-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "20260313_sap_file_mapping"
down_revision = "20260313_sap_table_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sap_connections",
        sa.Column("file_table_mapping", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sap_connections", "file_table_mapping")
