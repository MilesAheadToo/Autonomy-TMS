"""Add table_status JSONB column to sap_ingestion_jobs for per-table progress tracking.

Stores per-table ingestion status as JSON: {"TABLE_NAME": {"status": "completed", "rows": 936}, ...}
Status values: pending, in_progress, completed, failed.

Revision ID: 20260313_sap_table_status
Revises: 20260313_cascade
Create Date: 2026-03-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "20260313_sap_table_status"
down_revision = "20260313_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sap_ingestion_jobs",
        sa.Column("table_status", JSONB, server_default="{}", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sap_ingestion_jobs", "table_status")
