"""Add AWS SC advanced features

Revision ID: 20260110_advanced_feat
Revises: 20260110_order_up_to
Create Date: 2026-01-10

Adds AWS SC advanced features to complete 100% certification:
- Frozen horizon for production (lock orders within planning horizon)
- Setup time and changeover costs
- Batch size constraints

These features enable:
1. Frozen Horizon: Lock production orders within X days (stability)
2. Setup Time: Account for setup before production starts
3. Changeover Time/Cost: Sequence-dependent changeovers between products
4. Batch Sizing: Min/max production quantities

Reference: https://docs.aws.amazon.com/aws-supply-chain/latest/userguide/non-transactional.html
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DECIMAL
from sqlalchemy import inspect

# revision identifiers
revision = '20260110_advanced_feat'
down_revision = '20260110_order_up_to'
branch_labels = None
depends_on = None


def column_exists(table_name, column_name):
    """Check if a column exists in a table"""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade():
    # Add advanced features to production_process table

    if not column_exists('production_process', 'frozen_horizon_days'):
        op.add_column('production_process',
            sa.Column('frozen_horizon_days', sa.Integer(), nullable=True))

    if not column_exists('production_process', 'setup_time'):
        op.add_column('production_process',
            sa.Column('setup_time', sa.Integer(), nullable=True))

    if not column_exists('production_process', 'changeover_time'):
        op.add_column('production_process',
            sa.Column('changeover_time', sa.Integer(), nullable=True))

    if not column_exists('production_process', 'changeover_cost'):
        op.add_column('production_process',
            sa.Column('changeover_cost', DECIMAL(10, 2), nullable=True))

    if not column_exists('production_process', 'min_batch_size'):
        op.add_column('production_process',
            sa.Column('min_batch_size', DECIMAL(10, 2), nullable=True))

    if not column_exists('production_process', 'max_batch_size'):
        op.add_column('production_process',
            sa.Column('max_batch_size', DECIMAL(10, 2), nullable=True))


def downgrade():
    # Drop advanced features columns

    if column_exists('production_process', 'max_batch_size'):
        op.drop_column('production_process', 'max_batch_size')

    if column_exists('production_process', 'min_batch_size'):
        op.drop_column('production_process', 'min_batch_size')

    if column_exists('production_process', 'changeover_cost'):
        op.drop_column('production_process', 'changeover_cost')

    if column_exists('production_process', 'changeover_time'):
        op.drop_column('production_process', 'changeover_time')

    if column_exists('production_process', 'setup_time'):
        op.drop_column('production_process', 'setup_time')

    if column_exists('production_process', 'frozen_horizon_days'):
        op.drop_column('production_process', 'frozen_horizon_days')
