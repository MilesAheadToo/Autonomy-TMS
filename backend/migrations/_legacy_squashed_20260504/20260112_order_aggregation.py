"""Add order aggregation and scheduling tables

Revision ID: 20260112_order_aggregation
Revises: 20260112_production_capacity
Create Date: 2026-01-12

This migration adds tables for order aggregation and advanced scheduling (Phase 3 - Sprint 3).

Key Features:
- Order aggregation policies (periodic ordering, time windows, quantity constraints)
- Aggregated order tracking (cost savings, order batching)
- Multi-tenancy support

Usage:
    Order every Monday, min 50 units, multiple of 10
    Aggregate 3 orders (20+30+25) → 75 units → round to 80 (multiple of 10)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = '20260112_order_aggregation'
down_revision = '20260112_production_capacity'
branch_labels = None
depends_on = None


def upgrade():
    # Create order_aggregation_policy table
    op.create_table(
        'order_aggregation_policy',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),

        # Policy scope
        sa.Column('from_site_id', sa.Integer(), nullable=False),
        sa.Column('to_site_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=True),

        # Periodic ordering
        sa.Column('ordering_period_days', sa.Integer(), server_default=sa.text("1"), nullable=True),
        sa.Column('ordering_day_of_week', sa.Integer(), nullable=True),
        sa.Column('ordering_day_of_month', sa.Integer(), nullable=True),

        # Time windows
        sa.Column('order_window_start_hour', sa.Integer(), nullable=True),
        sa.Column('order_window_end_hour', sa.Integer(), nullable=True),

        # Quantity constraints
        sa.Column('min_order_quantity', mysql.DOUBLE(), nullable=True),
        sa.Column('max_order_quantity', mysql.DOUBLE(), nullable=True),
        sa.Column('order_multiple', mysql.DOUBLE(), server_default='1.0', nullable=True),

        # Aggregation settings
        sa.Column('aggregate_within_period', sa.Boolean(), server_default=sa.text("TRUE"), nullable=True),
        sa.Column('aggregation_window_days', sa.Integer(), server_default=sa.text("1"), nullable=True),

        # Cost savings
        sa.Column('fixed_order_cost', mysql.DOUBLE(), nullable=True),
        sa.Column('variable_cost_per_unit', mysql.DOUBLE(), nullable=True),

        # Policy status
        sa.Column('is_active', sa.Boolean(), server_default=sa.text("TRUE"), nullable=True),
        sa.Column('priority', sa.Integer(), server_default='100', nullable=True),

        # Multi-tenancy
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('config_id', sa.Integer(), nullable=True),

        # Effective date range
        sa.Column('effective_start_date', sa.Date(), nullable=True),
        sa.Column('effective_end_date', sa.Date(), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'), nullable=True),

        # Primary key
        sa.PrimaryKeyConstraint('id'),

        # Foreign keys
        sa.ForeignKeyConstraint(['from_site_id'], ['nodes.id'], ),
        sa.ForeignKeyConstraint(['to_site_id'], ['nodes.id'], ),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ),
    )

    # Create indexes for order_aggregation_policy
    op.create_index('idx_agg_policy_sites', 'order_aggregation_policy', ['from_site_id', 'to_site_id'])
    op.create_index('idx_agg_policy_product', 'order_aggregation_policy', ['product_id'])
    op.create_index('idx_agg_policy_group_config', 'order_aggregation_policy', ['customer_id', 'config_id'])
    op.create_index('idx_agg_policy_active', 'order_aggregation_policy', ['is_active'])

    print("✓ Created order_aggregation_policy table")

    # Create aggregated_order table
    op.create_table(
        'aggregated_order',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),

        # Aggregation details
        sa.Column('policy_id', sa.Integer(), nullable=True),
        sa.Column('game_id', sa.Integer(), nullable=True),
        sa.Column('round_number', sa.Integer(), nullable=False),

        # Sites and product
        sa.Column('from_site_id', sa.Integer(), nullable=False),
        sa.Column('to_site_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),

        # Quantities
        sa.Column('total_quantity', mysql.DOUBLE(), nullable=False),
        sa.Column('adjusted_quantity', mysql.DOUBLE(), nullable=True),
        sa.Column('num_orders_aggregated', sa.Integer(), server_default=sa.text("1"), nullable=True),

        # Individual order references
        sa.Column('source_order_ids', sa.String(length=500), nullable=True),

        # Dates
        sa.Column('aggregation_date', sa.Date(), nullable=False),
        sa.Column('scheduled_order_date', sa.Date(), nullable=True),

        # Cost tracking
        sa.Column('fixed_cost_saved', mysql.DOUBLE(), server_default='0.0', nullable=True),
        sa.Column('total_order_cost', mysql.DOUBLE(), nullable=True),

        # Status
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=True),

        # Multi-tenancy
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('config_id', sa.Integer(), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'), nullable=True),

        # Primary key
        sa.PrimaryKeyConstraint('id'),

        # Foreign keys
        sa.ForeignKeyConstraint(['policy_id'], ['order_aggregation_policy.id'], ),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
        sa.ForeignKeyConstraint(['from_site_id'], ['nodes.id'], ),
        sa.ForeignKeyConstraint(['to_site_id'], ['nodes.id'], ),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ),
    )

    # Create indexes for aggregated_order
    op.create_index('idx_agg_order_game_round', 'aggregated_order', ['game_id', 'round_number'])
    op.create_index('idx_agg_order_sites', 'aggregated_order', ['from_site_id', 'to_site_id'])
    op.create_index('idx_agg_order_status', 'aggregated_order', ['status'])
    op.create_index('idx_agg_order_scheduled', 'aggregated_order', ['scheduled_order_date'])

    print("✓ Created aggregated_order table")
    print("  Order aggregation and scheduling now available")


def downgrade():
    # Drop aggregated_order table and indexes
    op.drop_index('idx_agg_order_scheduled', table_name='aggregated_order')
    op.drop_index('idx_agg_order_status', table_name='aggregated_order')
    op.drop_index('idx_agg_order_sites', table_name='aggregated_order')
    op.drop_index('idx_agg_order_game_round', table_name='aggregated_order')
    op.drop_table('aggregated_order')

    # Drop order_aggregation_policy table and indexes
    op.drop_index('idx_agg_policy_active', table_name='order_aggregation_policy')
    op.drop_index('idx_agg_policy_group_config', table_name='order_aggregation_policy')
    op.drop_index('idx_agg_policy_product', table_name='order_aggregation_policy')
    op.drop_index('idx_agg_policy_sites', table_name='order_aggregation_policy')
    op.drop_table('order_aggregation_policy')
