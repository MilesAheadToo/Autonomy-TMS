"""Add save_csv and update_tenant_data toggle columns to sap_ingestion_jobs.

- save_csv (BOOLEAN, default false): Save extracted data as CSV files for audit/backup.
- update_tenant_data (BOOLEAN, default true): Create/update DB entities. When false, dry run only.

Revision ID: 20260314_sap_job_toggles
Revises: 20260313_sap_table_status
Create Date: 2026-03-14
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "20260314_sap_job_toggles"
down_revision = "20260313_sap_table_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sap_ingestion_jobs",
        sa.Column("save_csv", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "sap_ingestion_jobs",
        sa.Column("update_tenant_data", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sap_ingestion_jobs", "update_tenant_data")
    op.drop_column("sap_ingestion_jobs", "save_csv")
