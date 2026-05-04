"""Powell Allocation and TRM Execution tables

Revision ID: 20260202_powell_allocation_tables
Revises: 20260202_powell_framework
Create Date: 2026-02-02 15:00:00.000000

Tables for narrow TRM execution layer:
- powell_allocations: Priority-based inventory allocations (AATP)
- powell_atp_decisions: ATP decision history for TRM training
- powell_rebalance_decisions: Rebalancing decision history
- powell_po_decisions: PO creation decision history
- powell_order_exceptions: Order tracking exception history
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260202_powell_allocation_tables'
down_revision = '20260202_powell_framework'
branch_labels = None
depends_on = None


def upgrade():
    """
    Add tables for Powell narrow TRM execution services.
    """

    # 1. powell_allocations table
    # Stores priority × product × location allocations from tGNN
    op.create_table(
        'powell_allocations',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.String(100), nullable=False),
        sa.Column('location_id', sa.String(100), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),  # 1=highest, 5=lowest

        # Allocation quantities
        sa.Column('allocated_qty', sa.Float(), nullable=False, server_default='0'),
        sa.Column('consumed_qty', sa.Float(), nullable=False, server_default='0'),
        sa.Column('reserved_qty', sa.Float(), nullable=False, server_default='0'),  # Soft reserves

        # Source
        sa.Column('allocation_source', sa.String(50), nullable=True),  # 'tgnn', 'manual', 'rule'
        sa.Column('allocation_cadence', sa.String(20), nullable=False, server_default='weekly'),

        # Validity period
        sa.Column('valid_from', sa.DateTime(), nullable=False),
        sa.Column('valid_to', sa.DateTime(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),

        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE')
    )

    op.create_index('idx_alloc_config_product_loc', 'powell_allocations', ['config_id', 'product_id', 'location_id'])
    op.create_index('idx_alloc_priority', 'powell_allocations', ['priority'])
    op.create_index('idx_alloc_active', 'powell_allocations', ['is_active'])
    op.create_index('idx_alloc_validity', 'powell_allocations', ['valid_from', 'valid_to'])

    # Unique constraint for config+product+location+priority (within validity period)
    op.create_unique_constraint(
        'uq_powell_alloc_key',
        'powell_allocations',
        ['config_id', 'product_id', 'location_id', 'priority', 'valid_from']
    )

    # 2. powell_atp_decisions table
    # Stores ATP decision history for TRM training
    op.create_table(
        'powell_atp_decisions',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.String(100), nullable=False),

        # Request
        sa.Column('product_id', sa.String(100), nullable=False),
        sa.Column('location_id', sa.String(100), nullable=False),
        sa.Column('requested_qty', sa.Float(), nullable=False),
        sa.Column('order_priority', sa.Integer(), nullable=False),

        # Decision
        sa.Column('can_fulfill', sa.Boolean(), nullable=False),
        sa.Column('promised_qty', sa.Float(), nullable=False),
        sa.Column('consumption_breakdown', sa.JSON(), nullable=True),  # {priority: qty}

        # Context (state features for TRM)
        sa.Column('state_features', sa.JSON(), nullable=True),
        sa.Column('decision_method', sa.String(50), nullable=True),  # 'trm', 'heuristic'
        sa.Column('confidence', sa.Float(), nullable=True),

        # Outcome (for training)
        sa.Column('was_committed', sa.Boolean(), nullable=True),
        sa.Column('actual_fulfilled_qty', sa.Float(), nullable=True),
        sa.Column('fulfillment_date', sa.DateTime(), nullable=True),

        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE')
    )

    op.create_index('idx_atp_config_order', 'powell_atp_decisions', ['config_id', 'order_id'])
    op.create_index('idx_atp_product_loc', 'powell_atp_decisions', ['product_id', 'location_id'])
    op.create_index('idx_atp_created', 'powell_atp_decisions', ['created_at'])

    # 3. powell_rebalance_decisions table
    # Stores rebalancing decision history for TRM training
    op.create_table(
        'powell_rebalance_decisions',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('config_id', sa.Integer(), nullable=False),

        # Transfer details
        sa.Column('product_id', sa.String(100), nullable=False),
        sa.Column('from_site', sa.String(100), nullable=False),
        sa.Column('to_site', sa.String(100), nullable=False),
        sa.Column('recommended_qty', sa.Float(), nullable=False),

        # Context
        sa.Column('reason', sa.String(50), nullable=False),  # stockout_risk, excess_inventory, etc.
        sa.Column('urgency', sa.Float(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),

        # Expected impact
        sa.Column('source_dos_before', sa.Float(), nullable=True),
        sa.Column('source_dos_after', sa.Float(), nullable=True),
        sa.Column('dest_dos_before', sa.Float(), nullable=True),
        sa.Column('dest_dos_after', sa.Float(), nullable=True),
        sa.Column('expected_cost', sa.Float(), nullable=True),

        # Outcome
        sa.Column('was_executed', sa.Boolean(), nullable=True),
        sa.Column('actual_qty', sa.Float(), nullable=True),
        sa.Column('actual_cost', sa.Float(), nullable=True),
        sa.Column('service_impact', sa.Float(), nullable=True),

        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE')
    )

    op.create_index('idx_rebalance_config', 'powell_rebalance_decisions', ['config_id'])
    op.create_index('idx_rebalance_product', 'powell_rebalance_decisions', ['product_id'])
    op.create_index('idx_rebalance_sites', 'powell_rebalance_decisions', ['from_site', 'to_site'])
    op.create_index('idx_rebalance_created', 'powell_rebalance_decisions', ['created_at'])

    # 4. powell_po_decisions table
    # Stores PO creation decision history for TRM training
    op.create_table(
        'powell_po_decisions',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('config_id', sa.Integer(), nullable=False),

        # PO details
        sa.Column('product_id', sa.String(100), nullable=False),
        sa.Column('location_id', sa.String(100), nullable=False),
        sa.Column('supplier_id', sa.String(100), nullable=False),
        sa.Column('recommended_qty', sa.Float(), nullable=False),

        # Context
        sa.Column('trigger_reason', sa.String(50), nullable=False),  # reorder_point, safety_stock, etc.
        sa.Column('urgency', sa.String(20), nullable=False),  # critical, high, normal, low
        sa.Column('confidence', sa.Float(), nullable=True),

        # Inventory state at decision
        sa.Column('inventory_position', sa.Float(), nullable=True),
        sa.Column('days_of_supply', sa.Float(), nullable=True),
        sa.Column('forecast_30_day', sa.Float(), nullable=True),

        # Expected outcome
        sa.Column('expected_receipt_date', sa.Date(), nullable=True),
        sa.Column('expected_cost', sa.Float(), nullable=True),

        # Outcome
        sa.Column('was_executed', sa.Boolean(), nullable=True),
        sa.Column('actual_qty', sa.Float(), nullable=True),
        sa.Column('actual_receipt_date', sa.Date(), nullable=True),
        sa.Column('actual_cost', sa.Float(), nullable=True),

        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE')
    )

    op.create_index('idx_po_config', 'powell_po_decisions', ['config_id'])
    op.create_index('idx_po_product_loc', 'powell_po_decisions', ['product_id', 'location_id'])
    op.create_index('idx_po_supplier', 'powell_po_decisions', ['supplier_id'])
    op.create_index('idx_po_created', 'powell_po_decisions', ['created_at'])

    # 5. powell_order_exceptions table
    # Stores order exception detection history for TRM training
    op.create_table(
        'powell_order_exceptions',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.String(100), nullable=False),

        # Order context
        sa.Column('order_type', sa.String(50), nullable=False),  # purchase_order, transfer_order, etc.
        sa.Column('order_status', sa.String(50), nullable=False),

        # Exception details
        sa.Column('exception_type', sa.String(50), nullable=False),  # late_delivery, quantity_shortage, etc.
        sa.Column('severity', sa.String(20), nullable=False),  # info, warning, high, critical
        sa.Column('recommended_action', sa.String(50), nullable=False),  # expedite, find_alternate, etc.

        # Context
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('impact_assessment', sa.Text(), nullable=True),
        sa.Column('estimated_impact_cost', sa.Float(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),

        # State features for TRM
        sa.Column('state_features', sa.JSON(), nullable=True),

        # Outcome
        sa.Column('action_taken', sa.String(50), nullable=True),
        sa.Column('resolution_time_hours', sa.Float(), nullable=True),
        sa.Column('actual_impact_cost', sa.Float(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),

        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE')
    )

    op.create_index('idx_exception_config_order', 'powell_order_exceptions', ['config_id', 'order_id'])
    op.create_index('idx_exception_type', 'powell_order_exceptions', ['exception_type'])
    op.create_index('idx_exception_severity', 'powell_order_exceptions', ['severity'])
    op.create_index('idx_exception_created', 'powell_order_exceptions', ['created_at'])


def downgrade():
    """Remove Powell allocation and TRM execution tables."""

    # Drop powell_order_exceptions
    op.drop_index('idx_exception_created', table_name='powell_order_exceptions')
    op.drop_index('idx_exception_severity', table_name='powell_order_exceptions')
    op.drop_index('idx_exception_type', table_name='powell_order_exceptions')
    op.drop_index('idx_exception_config_order', table_name='powell_order_exceptions')
    op.drop_table('powell_order_exceptions')

    # Drop powell_po_decisions
    op.drop_index('idx_po_created', table_name='powell_po_decisions')
    op.drop_index('idx_po_supplier', table_name='powell_po_decisions')
    op.drop_index('idx_po_product_loc', table_name='powell_po_decisions')
    op.drop_index('idx_po_config', table_name='powell_po_decisions')
    op.drop_table('powell_po_decisions')

    # Drop powell_rebalance_decisions
    op.drop_index('idx_rebalance_created', table_name='powell_rebalance_decisions')
    op.drop_index('idx_rebalance_sites', table_name='powell_rebalance_decisions')
    op.drop_index('idx_rebalance_product', table_name='powell_rebalance_decisions')
    op.drop_index('idx_rebalance_config', table_name='powell_rebalance_decisions')
    op.drop_table('powell_rebalance_decisions')

    # Drop powell_atp_decisions
    op.drop_index('idx_atp_created', table_name='powell_atp_decisions')
    op.drop_index('idx_atp_product_loc', table_name='powell_atp_decisions')
    op.drop_index('idx_atp_config_order', table_name='powell_atp_decisions')
    op.drop_table('powell_atp_decisions')

    # Drop powell_allocations
    op.drop_constraint('uq_powell_alloc_key', 'powell_allocations', type_='unique')
    op.drop_index('idx_alloc_validity', table_name='powell_allocations')
    op.drop_index('idx_alloc_active', table_name='powell_allocations')
    op.drop_index('idx_alloc_priority', table_name='powell_allocations')
    op.drop_index('idx_alloc_config_product_loc', table_name='powell_allocations')
    op.drop_table('powell_allocations')
