"""Rename aws_sc_planning to sc_planning

Revision ID: 20260119_rename_aws_sc
Revises: 20260119_add_mps_permissions
Create Date: 2026-01-19 06:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260119_rename_aws_sc'
down_revision = '20260116_audit'
branch_labels = None
depends_on = None


def upgrade():
    """Rename use_aws_sc_planning to use_sc_planning in games table."""
    # Rename the column in games table
    op.alter_column(
        'games',
        'use_aws_sc_planning',
        new_column_name='use_sc_planning',
        existing_type=sa.Boolean(),
        existing_nullable=True,
        existing_server_default=sa.false()
    )


def downgrade():
    """Revert use_sc_planning back to use_aws_sc_planning."""
    # Rename the column back
    op.alter_column(
        'games',
        'use_sc_planning',
        new_column_name='use_aws_sc_planning',
        existing_type=sa.Boolean(),
        existing_nullable=True,
        existing_server_default=sa.false()
    )
