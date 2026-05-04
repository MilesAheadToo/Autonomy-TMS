"""Add AWS SC inventory policy type fields

Revision ID: 20260110_policy_types
Revises: 20260110_hierarchical_safe
Create Date: 2026-01-10

Adds AWS SC standard policy type fields to inv_policy table:
- ss_policy: Safety stock policy type (abs_level, doc_dem, doc_fcst, sl)
- ss_days: Days of coverage for doc_dem/doc_fcst policies
- ss_quantity: Absolute quantity for abs_level policy
- policy_value: Generic policy value field for future use

Reference: https://docs.aws.amazon.com/aws-supply-chain/latest/userguide/non-transactional.html
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers
revision = '20260110_policy_types'
down_revision = '20260110_hierarchical_safe'
branch_labels = None
depends_on = None


def column_exists(table_name, column_name):
    """Check if a column exists in a table"""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def index_exists(table_name, index_name):
    """Check if an index exists"""
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = [idx['name'] for idx in inspector.get_indexes(table_name)]
    return index_name in indexes


def upgrade():
    # Add AWS SC policy type fields to inv_policy table

    # ss_policy: Safety stock policy type
    # Values: 'abs_level', 'doc_dem', 'doc_fcst', 'sl'
    if not column_exists('inv_policy', 'ss_policy'):
        op.add_column('inv_policy',
            sa.Column('ss_policy', sa.String(20), nullable=True))

    # ss_days: Days of coverage (for doc_dem and doc_fcst)
    if not column_exists('inv_policy', 'ss_days'):
        op.add_column('inv_policy',
            sa.Column('ss_days', sa.Integer(), nullable=True))

    # ss_quantity: Absolute safety stock quantity (for abs_level)
    if not column_exists('inv_policy', 'ss_quantity'):
        op.add_column('inv_policy',
            sa.Column('ss_quantity', sa.Float(), nullable=True))

    # policy_value: Generic policy value field for future use
    if not column_exists('inv_policy', 'policy_value'):
        op.add_column('inv_policy',
            sa.Column('policy_value', sa.Float(), nullable=True))

    # Create index on ss_policy for filtering by policy type
    if not index_exists('inv_policy', 'idx_inv_policy_ss_policy'):
        op.create_index('idx_inv_policy_ss_policy', 'inv_policy', ['ss_policy'])


def downgrade():
    # Drop index
    if index_exists('inv_policy', 'idx_inv_policy_ss_policy'):
        op.drop_index('idx_inv_policy_ss_policy', 'inv_policy')

    # Drop columns
    if column_exists('inv_policy', 'policy_value'):
        op.drop_column('inv_policy', 'policy_value')

    if column_exists('inv_policy', 'ss_quantity'):
        op.drop_column('inv_policy', 'ss_quantity')

    if column_exists('inv_policy', 'ss_days'):
        op.drop_column('inv_policy', 'ss_days')

    if column_exists('inv_policy', 'ss_policy'):
        op.drop_column('inv_policy', 'ss_policy')
