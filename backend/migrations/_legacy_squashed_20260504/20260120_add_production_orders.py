"""Add Production Orders tables

Revision ID: 20260120_add_production_orders
Revises: 20260119_add_mps_permissions
Create Date: 2026-01-20

Adds production_orders and production_order_components tables
for AWS Supply Chain compliance (Phase 2).
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers
revision = '20260120_add_production_orders'
down_revision = '20260119_add_mps_permissions'
branch_labels = None
depends_on = None


def upgrade():
    """Create production_orders and production_order_components tables"""

    # Create production_orders table
    op.create_table(
        'production_orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mps_plan_id', sa.Integer(), nullable=True),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('order_number', sa.String(length=100), nullable=False),
        sa.Column('planned_quantity', sa.Integer(), nullable=False),
        sa.Column('actual_quantity', sa.Integer(), nullable=True),
        sa.Column('scrap_quantity', sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column('yield_percentage', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='PLANNED'),
        sa.Column('planned_start_date', sa.DateTime(), nullable=False),
        sa.Column('planned_completion_date', sa.DateTime(), nullable=False),
        sa.Column('actual_start_date', sa.DateTime(), nullable=True),
        sa.Column('actual_completion_date', sa.DateTime(), nullable=True),
        sa.Column('released_date', sa.DateTime(), nullable=True),
        sa.Column('closed_date', sa.DateTime(), nullable=True),
        sa.Column('lead_time_planned', sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column('lead_time_actual', sa.Integer(), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=True, server_default='5'),
        sa.Column('resource_hours_planned', sa.Float(), nullable=True),
        sa.Column('resource_hours_actual', sa.Float(), nullable=True),
        sa.Column('setup_cost', sa.Float(), nullable=True, server_default='0.0'),
        sa.Column('unit_cost', sa.Float(), nullable=True, server_default='0.0'),
        sa.Column('total_cost', sa.Float(), nullable=True, server_default='0.0'),
        sa.Column('notes', sa.String(length=500), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True, onupdate=datetime.utcnow),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=True, server_default=sa.text("FALSE")),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_number')
    )

    # Create indexes for production_orders
    op.create_index('ix_production_orders_id', 'production_orders', ['id'])
    op.create_index('ix_production_orders_order_number', 'production_orders', ['order_number'], unique=True)
    op.create_index('ix_production_orders_mps_plan_id', 'production_orders', ['mps_plan_id'])
    op.create_index('ix_production_orders_item_id', 'production_orders', ['item_id'])
    op.create_index('ix_production_orders_site_id', 'production_orders', ['site_id'])
    op.create_index('ix_production_orders_config_id', 'production_orders', ['config_id'])
    op.create_index('ix_production_orders_status', 'production_orders', ['status'])
    op.create_index('ix_production_orders_is_deleted', 'production_orders', ['is_deleted'])

    # Create foreign key constraints for production_orders
    # Note: Only create FK if the referenced table exists
    inspector = sa.inspect(op.get_bind())
    existing_tables = inspector.get_table_names()

    if 'mps_plans' in existing_tables:
        op.create_foreign_key(
            'fk_production_orders_mps_plan_id',
            'production_orders', 'mps_plans',
            ['mps_plan_id'], ['id'],
            ondelete='SET NULL'
        )

    if 'items' in existing_tables:
        op.create_foreign_key(
            'fk_production_orders_item_id',
            'production_orders', 'items',
            ['item_id'], ['id'],
            ondelete='CASCADE'
        )

    if 'nodes' in existing_tables:
        op.create_foreign_key(
            'fk_production_orders_site_id',
            'production_orders', 'nodes',
            ['site_id'], ['id'],
            ondelete='CASCADE'
        )

    if 'supply_chain_configs' in existing_tables:
        op.create_foreign_key(
            'fk_production_orders_config_id',
            'production_orders', 'supply_chain_configs',
            ['config_id'], ['id'],
            ondelete='CASCADE'
        )

    if 'users' in existing_tables:
        op.create_foreign_key(
            'fk_production_orders_created_by_id',
            'production_orders', 'users',
            ['created_by_id'], ['id'],
            ondelete='SET NULL'
        )
        op.create_foreign_key(
            'fk_production_orders_updated_by_id',
            'production_orders', 'users',
            ['updated_by_id'], ['id'],
            ondelete='SET NULL'
        )

    # Create production_order_components table
    op.create_table(
        'production_order_components',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('production_order_id', sa.Integer(), nullable=False),
        sa.Column('component_item_id', sa.Integer(), nullable=False),
        sa.Column('planned_quantity', sa.Float(), nullable=False),
        sa.Column('actual_quantity', sa.Float(), nullable=True),
        sa.Column('scrap_quantity', sa.Float(), nullable=True, server_default='0.0'),
        sa.Column('unit_of_measure', sa.String(length=20), nullable=True, server_default='EA'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True, onupdate=datetime.utcnow),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes for production_order_components
    op.create_index('ix_production_order_components_id', 'production_order_components', ['id'])
    op.create_index('ix_production_order_components_production_order_id', 'production_order_components', ['production_order_id'])
    op.create_index('ix_production_order_components_component_item_id', 'production_order_components', ['component_item_id'])

    # Create foreign key constraints for production_order_components
    op.create_foreign_key(
        'fk_production_order_components_production_order_id',
        'production_order_components', 'production_orders',
        ['production_order_id'], ['id'],
        ondelete='CASCADE'
    )

    if 'items' in existing_tables:
        op.create_foreign_key(
            'fk_production_order_components_component_item_id',
            'production_order_components', 'items',
            ['component_item_id'], ['id'],
            ondelete='CASCADE'
        )

    print("✅ Production orders tables created successfully")


def downgrade():
    """Drop production_orders and production_order_components tables"""

    # Drop production_order_components table (child first)
    op.drop_table('production_order_components')
    print("✅ Dropped production_order_components table")

    # Drop production_orders table
    op.drop_table('production_orders')
    print("✅ Dropped production_orders table")

    print("✅ Production orders migration downgrade completed")
