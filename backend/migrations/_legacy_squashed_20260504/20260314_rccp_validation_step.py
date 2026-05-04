"""Add rccp_validation step to config_provisioning_status.

Revision ID: 20260314_rccp_validation
Revises: None (standalone)
"""
from alembic import op
import sqlalchemy as sa

revision = "20260314_rccp_validation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "config_provisioning_status",
        sa.Column("rccp_validation_status", sa.String(20), server_default="pending"),
    )
    op.add_column(
        "config_provisioning_status",
        sa.Column("rccp_validation_at", sa.DateTime, nullable=True),
    )
    op.add_column(
        "config_provisioning_status",
        sa.Column("rccp_validation_error", sa.Text, nullable=True),
    )


def downgrade():
    op.drop_column("config_provisioning_status", "rccp_validation_error")
    op.drop_column("config_provisioning_status", "rccp_validation_at")
    op.drop_column("config_provisioning_status", "rccp_validation_status")
