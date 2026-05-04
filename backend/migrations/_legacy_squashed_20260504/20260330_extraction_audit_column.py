"""Add extraction_audit JSONB column to config_provisioning_status.

Revision ID: 20260330_extract_audit
Revises: 20260329_backtest_evaluation_step
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "20260330_extract_audit"
down_revision = "20260329_backtest_eval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "config_provisioning_status",
        sa.Column("extraction_audit", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("config_provisioning_status", "extraction_audit")
