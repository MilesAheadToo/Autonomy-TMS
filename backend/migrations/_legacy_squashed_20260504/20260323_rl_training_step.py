"""Add rl_training provisioning step columns

Adds rl_training_status, rl_training_at, rl_training_error columns to
config_provisioning_status table for Phase 2 TRM RL fine-tuning step.

Revision ID: 20260323_rl_step
Revises: 20260323_ek
"""

from alembic import op
import sqlalchemy as sa

revision = "20260323_rl_step"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "config_provisioning_status",
        sa.Column("rl_training_status", sa.String(20), server_default="pending"),
    )
    op.add_column(
        "config_provisioning_status",
        sa.Column("rl_training_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "config_provisioning_status",
        sa.Column("rl_training_error", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("config_provisioning_status", "rl_training_error")
    op.drop_column("config_provisioning_status", "rl_training_at")
    op.drop_column("config_provisioning_status", "rl_training_status")
