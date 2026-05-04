"""Add production_capacity table for capacity constraints

Revision ID: 20260112_production_capacity
Revises: 20260112_inbound_order_line
Create Date: 2026-01-12

This migration adds the production_capacity table which enables capacity constraints
in AWS Supply Chain execution mode (Phase 3 - Sprint 2).

Key Features:
- Track max capacity per site (production/transfer/storage)
- Enforce capacity limits (prevent unlimited production)
- Support overflow handling
- Multi-tenancy support

Usage:
    Factory capacity = 100 units/week
    If orders exceed 100, overflow to next period or reject
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = '20260112_production_capacity'
down_revision = '20260112_inbound_order_line'
branch_labels = None
depends_on = None


def upgrade():
    # Create production_capacity table
    op.create_table(
        'production_capacity',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=True),

        # Capacity limits
        sa.Column('max_capacity_per_period', mysql.DOUBLE(), nullable=False),
        sa.Column('current_capacity_used', mysql.DOUBLE(), server_default=sa.text("FALSE"), nullable=True),
        sa.Column('capacity_uom', sa.String(length=20), server_default='CASES', nullable=True),

        # Capacity type and metadata
        sa.Column('capacity_type', sa.String(length=20), server_default='production', nullable=True),
        sa.Column('capacity_period', sa.String(length=20), server_default='week', nullable=True),
        sa.Column('utilization_target', mysql.DOUBLE(), nullable=True),

        # Overflow handling
        sa.Column('allow_overflow', sa.Boolean(), server_default=sa.text("FALSE"), nullable=True),
        sa.Column('overflow_cost_multiplier', mysql.DOUBLE(), server_default='1.5', nullable=True),

        # Multi-tenancy and time range
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('config_id', sa.Integer(), nullable=True),
        sa.Column('effective_start_date', sa.Date(), nullable=True),
        sa.Column('effective_end_date', sa.Date(), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'), nullable=True),

        # Primary key
        sa.PrimaryKeyConstraint('id'),

        # Foreign keys
        sa.ForeignKeyConstraint(['site_id'], ['nodes.id'], ),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ),
    )

    # Create indexes for performance
    op.create_index('idx_capacity_site_product', 'production_capacity', ['site_id', 'product_id'])
    op.create_index('idx_capacity_group_config', 'production_capacity', ['customer_id', 'config_id'])
    op.create_index('idx_capacity_config', 'production_capacity', ['config_id'])
    op.create_index('idx_capacity_type', 'production_capacity', ['capacity_type'])

    print("✓ Created production_capacity table")
    print("  Capacity constraints now available for AWS SC execution mode")


def downgrade():
    # Drop indexes
    op.drop_index('idx_capacity_type', table_name='production_capacity')
    op.drop_index('idx_capacity_config', table_name='production_capacity')
    op.drop_index('idx_capacity_group_config', table_name='production_capacity')
    op.drop_index('idx_capacity_site_product', table_name='production_capacity')

    # Drop table
    op.drop_table('production_capacity')
