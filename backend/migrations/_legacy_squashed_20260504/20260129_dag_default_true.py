"""Change use_dag_sequential default to True for new games

Revision ID: dag_default_true_001
Revises: dag_order_tracking_001
Create Date: 2026-01-29

This migration:
1. Changes the server default for use_dag_sequential from False to True
2. Existing games keep their current value (no data change)
3. New games will default to DAG sequential execution

Why not convert ALL existing games?
- Games in progress were started with single-decision mechanics
- Switching mid-game would confuse players and invalidate strategies
- Existing PlayerRound records don't have fulfillment_qty/replenishment_qty populated
- The DAG execution logic expects these fields for dual-decision flow
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'dag_default_true_001'
down_revision = 'dag_order_tracking_001'
branch_labels = None
depends_on = None


def upgrade():
    # Change server default to True for new games
    # Existing games retain their current value
    op.alter_column(
        'games',
        'use_dag_sequential',
        server_default='1',  # True for new games
        existing_type=sa.Boolean(),
        existing_nullable=False
    )


def downgrade():
    # Revert to False default
    op.alter_column(
        'games',
        'use_dag_sequential',
        server_default='0',  # False for new games
        existing_type=sa.Boolean(),
        existing_nullable=False
    )
