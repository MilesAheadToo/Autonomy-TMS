"""Create TRM training data tables

Revision ID: 20260207_trm_data
Revises:
Create Date: 2026-02-07

Creates tables for TRM training data:
- trm_atp_decision_log: ATP executor decisions
- trm_atp_outcome: ATP decision outcomes
- trm_rebalancing_decision_log: Rebalancing decisions
- trm_rebalancing_outcome: Rebalancing outcomes
- trm_po_decision_log: PO creation decisions
- trm_po_outcome: PO decision outcomes
- trm_order_tracking_decision_log: Order tracking decisions
- trm_order_tracking_outcome: Order tracking outcomes
- trm_replay_buffer: Unified RL replay buffer
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260207_trm_data'
down_revision = '20260206_site_type'
branch_labels = None
depends_on = None


def upgrade():
    # Create enums
    decision_source_enum = postgresql.ENUM(
        'expert_human', 'ai_accepted', 'ai_modified', 'ai_rejected', 'ai_autonomous', 'synthetic',
        name='decision_source_enum',
        create_type=False
    )
    decision_source_enum.create(op.get_bind(), checkfirst=True)

    outcome_status_enum = postgresql.ENUM(
        'pending', 'measured', 'partial',
        name='outcome_status_enum',
        create_type=False
    )
    outcome_status_enum.create(op.get_bind(), checkfirst=True)

    # ATP Decision Log
    op.create_table(
        'trm_atp_decision_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=True),
        sa.Column('product_id', sa.Integer(), nullable=True),
        sa.Column('decision_date', sa.Date(), nullable=False),
        sa.Column('order_id', sa.String(100), nullable=True),
        sa.Column('customer_id', sa.String(100), nullable=True),
        sa.Column('requested_qty', sa.Float(), nullable=False),
        sa.Column('requested_date', sa.Date(), nullable=True),
        sa.Column('priority', sa.Integer(), default=3),
        sa.Column('state_inventory', sa.Float(), nullable=True),
        sa.Column('state_pipeline', sa.Float(), nullable=True),
        sa.Column('state_backlog', sa.Float(), nullable=True),
        sa.Column('state_allocated', sa.Float(), nullable=True),
        sa.Column('state_available_atp', sa.Float(), nullable=True),
        sa.Column('state_demand_forecast', sa.Float(), nullable=True),
        sa.Column('state_other_orders_pending', sa.Integer(), nullable=True),
        sa.Column('state_features', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('action_type', sa.String(50), nullable=True),
        sa.Column('action_qty_fulfilled', sa.Float(), nullable=True),
        sa.Column('action_qty_backordered', sa.Float(), default=0),
        sa.Column('action_promise_date', sa.Date(), nullable=True),
        sa.Column('action_allocation_tier', sa.Integer(), nullable=True),
        sa.Column('action_reason', sa.Text(), nullable=True),
        sa.Column('source', decision_source_enum, default='expert_human'),
        sa.Column('decision_maker_id', sa.Integer(), nullable=True),
        sa.Column('ai_recommendation', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('ai_confidence', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), default=sa.func.now()),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id']),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id']),
        sa.ForeignKeyConstraint(['decision_maker_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_atp_decision_group_date', 'trm_atp_decision_log', ['customer_id', 'decision_date'])
    op.create_index('idx_atp_decision_site', 'trm_atp_decision_log', ['site_id'])
    op.create_index('idx_atp_decision_product', 'trm_atp_decision_log', ['product_id'])
    op.create_index('idx_atp_decision_date', 'trm_atp_decision_log', ['decision_date'])

    # ATP Outcome
    op.create_table(
        'trm_atp_outcome',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('decision_id', sa.Integer(), nullable=False),
        sa.Column('status', outcome_status_enum, default='pending'),
        sa.Column('measured_at', sa.DateTime(), nullable=True),
        sa.Column('actual_qty_shipped', sa.Float(), nullable=True),
        sa.Column('actual_ship_date', sa.Date(), nullable=True),
        sa.Column('actual_delivery_date', sa.Date(), nullable=True),
        sa.Column('on_time', sa.Boolean(), nullable=True),
        sa.Column('in_full', sa.Boolean(), nullable=True),
        sa.Column('otif', sa.Boolean(), nullable=True),
        sa.Column('days_late', sa.Integer(), nullable=True),
        sa.Column('fill_rate', sa.Float(), nullable=True),
        sa.Column('customer_satisfaction_impact', sa.Float(), nullable=True),
        sa.Column('revenue_impact', sa.Float(), nullable=True),
        sa.Column('cost_impact', sa.Float(), nullable=True),
        sa.Column('reward', sa.Float(), nullable=True),
        sa.Column('reward_components', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('next_state_inventory', sa.Float(), nullable=True),
        sa.Column('next_state_backlog', sa.Float(), nullable=True),
        sa.Column('next_state_features', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(['decision_id'], ['trm_atp_decision_log.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_atp_outcome_decision', 'trm_atp_outcome', ['decision_id'])

    # Rebalancing Decision Log
    op.create_table(
        'trm_rebalancing_decision_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=True),
        sa.Column('decision_date', sa.Date(), nullable=False),
        sa.Column('state_site_inventories', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('state_site_backlogs', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('state_site_demands', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('state_transit_matrix', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('state_network_imbalance', sa.Float(), nullable=True),
        sa.Column('state_features', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('action_type', sa.String(50), nullable=True),
        sa.Column('action_from_site_id', sa.Integer(), nullable=True),
        sa.Column('action_to_site_id', sa.Integer(), nullable=True),
        sa.Column('action_qty', sa.Float(), default=0),
        sa.Column('action_urgency', sa.String(20), default='normal'),
        sa.Column('action_reason', sa.Text(), nullable=True),
        sa.Column('source', decision_source_enum, default='expert_human'),
        sa.Column('decision_maker_id', sa.Integer(), nullable=True),
        sa.Column('ai_recommendation', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('ai_confidence', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), default=sa.func.now()),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id']),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id']),
        sa.ForeignKeyConstraint(['decision_maker_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_rebalancing_decision_group', 'trm_rebalancing_decision_log', ['customer_id'])
    op.create_index('idx_rebalancing_decision_product', 'trm_rebalancing_decision_log', ['product_id'])
    op.create_index('idx_rebalancing_decision_date', 'trm_rebalancing_decision_log', ['decision_date'])

    # Rebalancing Outcome
    op.create_table(
        'trm_rebalancing_outcome',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('decision_id', sa.Integer(), nullable=False),
        sa.Column('status', outcome_status_enum, default='pending'),
        sa.Column('measured_at', sa.DateTime(), nullable=True),
        sa.Column('actual_transfer_qty', sa.Float(), nullable=True),
        sa.Column('actual_arrival_date', sa.Date(), nullable=True),
        sa.Column('transfer_completed', sa.Boolean(), nullable=True),
        sa.Column('from_site_stockout_prevented', sa.Boolean(), nullable=True),
        sa.Column('to_site_stockout_prevented', sa.Boolean(), nullable=True),
        sa.Column('service_level_before', sa.Float(), nullable=True),
        sa.Column('service_level_after', sa.Float(), nullable=True),
        sa.Column('transfer_cost', sa.Float(), nullable=True),
        sa.Column('holding_cost_delta', sa.Float(), nullable=True),
        sa.Column('reward', sa.Float(), nullable=True),
        sa.Column('reward_components', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('next_state_site_inventories', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('next_state_network_imbalance', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(['decision_id'], ['trm_rebalancing_decision_log.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_rebalancing_outcome_decision', 'trm_rebalancing_outcome', ['decision_id'])

    # PO Decision Log
    op.create_table(
        'trm_po_decision_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=True),
        sa.Column('product_id', sa.Integer(), nullable=True),
        sa.Column('supplier_id', sa.Integer(), nullable=True),
        sa.Column('decision_date', sa.Date(), nullable=False),
        sa.Column('state_inventory', sa.Float(), nullable=True),
        sa.Column('state_pipeline', sa.Float(), nullable=True),
        sa.Column('state_backlog', sa.Float(), nullable=True),
        sa.Column('state_reorder_point', sa.Float(), nullable=True),
        sa.Column('state_safety_stock', sa.Float(), nullable=True),
        sa.Column('state_days_of_supply', sa.Float(), nullable=True),
        sa.Column('state_demand_forecast', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('state_demand_variability', sa.Float(), nullable=True),
        sa.Column('state_supplier_lead_time', sa.Float(), nullable=True),
        sa.Column('state_supplier_reliability', sa.Float(), nullable=True),
        sa.Column('state_features', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('action_type', sa.String(50), nullable=True),
        sa.Column('action_order_qty', sa.Float(), default=0),
        sa.Column('action_requested_date', sa.Date(), nullable=True),
        sa.Column('action_expedite', sa.Boolean(), default=False),
        sa.Column('action_reason', sa.Text(), nullable=True),
        sa.Column('po_number', sa.String(100), nullable=True),
        sa.Column('po_unit_cost', sa.Float(), nullable=True),
        sa.Column('source', decision_source_enum, default='expert_human'),
        sa.Column('decision_maker_id', sa.Integer(), nullable=True),
        sa.Column('ai_recommendation', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('ai_confidence', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), default=sa.func.now()),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id']),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id']),
        sa.ForeignKeyConstraint(['decision_maker_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_po_decision_group', 'trm_po_decision_log', ['customer_id'])
    op.create_index('idx_po_decision_site', 'trm_po_decision_log', ['site_id'])
    op.create_index('idx_po_decision_product', 'trm_po_decision_log', ['product_id'])
    op.create_index('idx_po_decision_date', 'trm_po_decision_log', ['decision_date'])

    # PO Outcome
    op.create_table(
        'trm_po_outcome',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('decision_id', sa.Integer(), nullable=False),
        sa.Column('status', outcome_status_enum, default='pending'),
        sa.Column('measured_at', sa.DateTime(), nullable=True),
        sa.Column('actual_receipt_qty', sa.Float(), nullable=True),
        sa.Column('actual_receipt_date', sa.Date(), nullable=True),
        sa.Column('lead_time_actual', sa.Integer(), nullable=True),
        sa.Column('stockout_occurred', sa.Boolean(), nullable=True),
        sa.Column('stockout_days', sa.Integer(), nullable=True),
        sa.Column('excess_inventory_cost', sa.Float(), nullable=True),
        sa.Column('expedite_cost', sa.Float(), nullable=True),
        sa.Column('dos_at_receipt', sa.Float(), nullable=True),
        sa.Column('reward', sa.Float(), nullable=True),
        sa.Column('reward_components', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('next_state_inventory', sa.Float(), nullable=True),
        sa.Column('next_state_days_of_supply', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(['decision_id'], ['trm_po_decision_log.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_po_outcome_decision', 'trm_po_outcome', ['decision_id'])

    # Order Tracking Decision Log
    op.create_table(
        'trm_order_tracking_decision_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.String(100), nullable=False),
        sa.Column('order_type', sa.String(50), nullable=True),
        sa.Column('decision_date', sa.Date(), nullable=False),
        sa.Column('exception_type', sa.String(50), nullable=True),
        sa.Column('exception_severity', sa.String(20), nullable=True),
        sa.Column('days_from_expected', sa.Integer(), nullable=True),
        sa.Column('qty_variance', sa.Float(), nullable=True),
        sa.Column('state_order_status', sa.String(50), nullable=True),
        sa.Column('state_order_qty', sa.Float(), nullable=True),
        sa.Column('state_expected_date', sa.Date(), nullable=True),
        sa.Column('state_inventory_position', sa.Float(), nullable=True),
        sa.Column('state_other_pending_orders', sa.Integer(), nullable=True),
        sa.Column('state_customer_impact', sa.String(50), nullable=True),
        sa.Column('state_features', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('action_type', sa.String(50), nullable=True),
        sa.Column('action_new_expected_date', sa.Date(), nullable=True),
        sa.Column('action_reorder_qty', sa.Float(), nullable=True),
        sa.Column('action_escalated_to', sa.String(100), nullable=True),
        sa.Column('action_reason', sa.Text(), nullable=True),
        sa.Column('source', decision_source_enum, default='expert_human'),
        sa.Column('decision_maker_id', sa.Integer(), nullable=True),
        sa.Column('ai_recommendation', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('ai_confidence', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), default=sa.func.now()),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id']),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id']),
        sa.ForeignKeyConstraint(['decision_maker_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_order_tracking_decision_group', 'trm_order_tracking_decision_log', ['customer_id'])
    op.create_index('idx_order_tracking_decision_order', 'trm_order_tracking_decision_log', ['order_id'])
    op.create_index('idx_order_tracking_decision_date', 'trm_order_tracking_decision_log', ['decision_date'])

    # Order Tracking Outcome
    op.create_table(
        'trm_order_tracking_outcome',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('decision_id', sa.Integer(), nullable=False),
        sa.Column('status', outcome_status_enum, default='pending'),
        sa.Column('measured_at', sa.DateTime(), nullable=True),
        sa.Column('exception_resolved', sa.Boolean(), nullable=True),
        sa.Column('resolution_time_hours', sa.Float(), nullable=True),
        sa.Column('final_order_status', sa.String(50), nullable=True),
        sa.Column('customer_notified', sa.Boolean(), nullable=True),
        sa.Column('customer_satisfied', sa.Boolean(), nullable=True),
        sa.Column('additional_cost', sa.Float(), nullable=True),
        sa.Column('service_recovery_successful', sa.Boolean(), nullable=True),
        sa.Column('reward', sa.Float(), nullable=True),
        sa.Column('reward_components', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('next_state_order_status', sa.String(50), nullable=True),
        sa.ForeignKeyConstraint(['decision_id'], ['trm_order_tracking_decision_log.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_order_tracking_outcome_decision', 'trm_order_tracking_outcome', ['decision_id'])

    # Unified Replay Buffer
    op.create_table(
        'trm_replay_buffer',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('trm_type', sa.String(50), nullable=False),
        sa.Column('decision_log_id', sa.Integer(), nullable=True),
        sa.Column('decision_log_table', sa.String(100), nullable=True),
        sa.Column('state_vector', postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column('state_dim', sa.Integer(), nullable=False),
        sa.Column('action_discrete', sa.Integer(), nullable=True),
        sa.Column('action_continuous', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('action_dim', sa.Integer(), default=1),
        sa.Column('reward', sa.Float(), nullable=False),
        sa.Column('reward_components', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('next_state_vector', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('done', sa.Boolean(), default=False),
        sa.Column('is_expert', sa.Boolean(), default=False),
        sa.Column('priority', sa.Float(), default=1.0),
        sa.Column('td_error', sa.Float(), nullable=True),
        sa.Column('transition_date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), default=sa.func.now()),
        sa.Column('times_sampled', sa.Integer(), default=0),
        sa.Column('last_sampled_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id']),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_replay_buffer_group', 'trm_replay_buffer', ['customer_id'])
    op.create_index('idx_replay_buffer_trm_type_date', 'trm_replay_buffer', ['trm_type', 'transition_date'])
    op.create_index('idx_replay_buffer_priority', 'trm_replay_buffer', ['trm_type', 'priority'])
    op.create_index('idx_replay_buffer_expert', 'trm_replay_buffer', ['is_expert'])


def downgrade():
    # Drop tables in reverse order
    op.drop_index('idx_replay_buffer_expert', 'trm_replay_buffer')
    op.drop_index('idx_replay_buffer_priority', 'trm_replay_buffer')
    op.drop_index('idx_replay_buffer_trm_type_date', 'trm_replay_buffer')
    op.drop_index('idx_replay_buffer_group', 'trm_replay_buffer')
    op.drop_table('trm_replay_buffer')

    op.drop_index('idx_order_tracking_outcome_decision', 'trm_order_tracking_outcome')
    op.drop_table('trm_order_tracking_outcome')

    op.drop_index('idx_order_tracking_decision_date', 'trm_order_tracking_decision_log')
    op.drop_index('idx_order_tracking_decision_order', 'trm_order_tracking_decision_log')
    op.drop_index('idx_order_tracking_decision_group', 'trm_order_tracking_decision_log')
    op.drop_table('trm_order_tracking_decision_log')

    op.drop_index('idx_po_outcome_decision', 'trm_po_outcome')
    op.drop_table('trm_po_outcome')

    op.drop_index('idx_po_decision_date', 'trm_po_decision_log')
    op.drop_index('idx_po_decision_product', 'trm_po_decision_log')
    op.drop_index('idx_po_decision_site', 'trm_po_decision_log')
    op.drop_index('idx_po_decision_group', 'trm_po_decision_log')
    op.drop_table('trm_po_decision_log')

    op.drop_index('idx_rebalancing_outcome_decision', 'trm_rebalancing_outcome')
    op.drop_table('trm_rebalancing_outcome')

    op.drop_index('idx_rebalancing_decision_date', 'trm_rebalancing_decision_log')
    op.drop_index('idx_rebalancing_decision_product', 'trm_rebalancing_decision_log')
    op.drop_index('idx_rebalancing_decision_group', 'trm_rebalancing_decision_log')
    op.drop_table('trm_rebalancing_decision_log')

    op.drop_index('idx_atp_outcome_decision', 'trm_atp_outcome')
    op.drop_table('trm_atp_outcome')

    op.drop_index('idx_atp_decision_date', 'trm_atp_decision_log')
    op.drop_index('idx_atp_decision_product', 'trm_atp_decision_log')
    op.drop_index('idx_atp_decision_site', 'trm_atp_decision_log')
    op.drop_index('idx_atp_decision_group_date', 'trm_atp_decision_log')
    op.drop_table('trm_atp_decision_log')

    # Drop enums
    op.execute('DROP TYPE IF EXISTS outcome_status_enum')
    op.execute('DROP TYPE IF EXISTS decision_source_enum')
