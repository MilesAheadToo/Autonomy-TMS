"""Add order_up_to_level to inv_policy

Revision ID: 20260110_order_up_to
Revises: 20260110_sourcing_sched
Create Date: 2026-01-10

Adds order_up_to_level field to inv_policy table for periodic review inventory systems.

AWS SC periodic review systems:
- Order on fixed schedule (e.g., weekly, monthly)
- Order up to target level: order_qty = order_up_to_level - (on_hand + on_order)
- Typically used with sourcing schedules

Reference: https://docs.aws.amazon.com/aws-supply-chain/latest/userguide/non-transactional.html
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DECIMAL
from sqlalchemy import inspect

# revision identifiers
revision = '20260110_order_up_to'
down_revision = '20260110_sourcing_sched'
branch_labels = None
depends_on = None


def column_exists(table_name, column_name):
    """Check if a column exists in a table"""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade():
    # Add order_up_to_level to inv_policy
    if not column_exists('inv_policy', 'order_up_to_level'):
        op.add_column('inv_policy',
            sa.Column('order_up_to_level', DECIMAL(10, 2), nullable=True))


def downgrade():
    # Drop order_up_to_level from inv_policy
    if column_exists('inv_policy', 'order_up_to_level'):
        op.drop_column('inv_policy', 'order_up_to_level')
