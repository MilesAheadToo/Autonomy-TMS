"""Add group mode (Training vs Production) fields

Revision ID: 20260201_group_mode
Revises:
Create Date: 2026-02-01

Groups can now be designated as either:
- Training: Simplified navigation, game-like clock, turn-based progression
- Production: Full navigation, real data integration, production planning
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260201_group_mode'
down_revision = '20260201_sync_planning'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types
    group_mode_enum = sa.Enum('training', 'production', name='group_mode_enum')
    clock_mode_enum = sa.Enum('turn_based', 'timed', 'realtime', name='clock_mode_enum')

    # Create enums in database
    group_mode_enum.create(op.get_bind(), checkfirst=True)
    clock_mode_enum.create(op.get_bind(), checkfirst=True)

    # Add columns to customers table
    op.add_column('groups', sa.Column(
        'mode',
        group_mode_enum,
        nullable=False,
        server_default='production',
        comment='Customer operating mode: training or production'
    ))

    op.add_column('groups', sa.Column(
        'clock_mode',
        clock_mode_enum,
        nullable=True,
        comment='Clock progression mode for training groups'
    ))

    op.add_column('groups', sa.Column(
        'round_duration_seconds',
        sa.Integer(),
        nullable=True,
        comment='Round duration in seconds for timed clock mode'
    ))

    op.add_column('groups', sa.Column(
        'data_refresh_schedule',
        sa.String(100),
        nullable=True,
        comment='Cron expression for data refresh schedule (production mode)'
    ))

    op.add_column('groups', sa.Column(
        'last_data_import',
        sa.DateTime(),
        nullable=True,
        comment='Timestamp of last data import (production mode)'
    ))


def downgrade() -> None:
    # Remove columns
    op.drop_column('groups', 'last_data_import')
    op.drop_column('groups', 'data_refresh_schedule')
    op.drop_column('groups', 'round_duration_seconds')
    op.drop_column('groups', 'clock_mode')
    op.drop_column('groups', 'mode')

    # Drop enum types
    sa.Enum(name='clock_mode_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='group_mode_enum').drop(op.get_bind(), checkfirst=True)
