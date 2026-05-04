"""Add inventory projection entities (ATP/CTP)

Revision ID: 20260120_inv_proj
Revises: 20260120_add_supplier_entities
Create Date: 2026-01-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '20260120_inv_proj'
down_revision: Union[str, None] = '20260120_add_supplier_entities'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ===================================================================
    # inv_projection table
    # ===================================================================
    op.create_table(
        'inv_projection',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),

        # AWS SC Core Fields
        sa.Column('company_id', sa.Integer(), nullable=False, comment='Company/customer ID'),
        sa.Column('product_id', sa.Integer(), nullable=False, comment='Product ID (items table)'),
        sa.Column('site_id', sa.Integer(), nullable=False, comment='Site ID (nodes table)'),
        sa.Column('projection_date', sa.Date(), nullable=False, comment='Projection date'),

        # AWS SC Inventory Quantities
        sa.Column('on_hand_qty', sa.Double(), nullable=False, server_default='0.0', comment='Physical inventory on hand'),
        sa.Column('in_transit_qty', sa.Double(), nullable=False, server_default='0.0', comment='Inbound shipments'),
        sa.Column('on_order_qty', sa.Double(), nullable=False, server_default='0.0', comment='Purchase orders not yet shipped'),
        sa.Column('allocated_qty', sa.Double(), nullable=False, server_default='0.0', comment='Reserved for customer orders'),
        sa.Column('available_qty', sa.Double(), nullable=False, server_default='0.0', comment='Available for new orders'),
        sa.Column('reserved_qty', sa.Double(), nullable=False, server_default='0.0', comment='Reserved for production/internal use'),

        # AWS SC Supply/Demand Quantities
        sa.Column('supply_qty', sa.Double(), nullable=False, server_default='0.0', comment='Planned supply receipts'),
        sa.Column('demand_qty', sa.Double(), nullable=False, server_default='0.0', comment='Planned demand/consumption'),

        # Opening/Closing Balance
        sa.Column('opening_inventory', sa.Double(), nullable=False, server_default='0.0', comment='Opening inventory balance'),
        sa.Column('closing_inventory', sa.Double(), nullable=False, server_default='0.0', comment='Closing inventory balance'),

        # Extension: ATP/CTP Quantities
        sa.Column('atp_qty', sa.Double(), nullable=False, server_default='0.0', comment='Available-to-Promise'),
        sa.Column('ctp_qty', sa.Double(), nullable=False, server_default='0.0', comment='Capable-to-Promise'),

        # Extension: Stochastic Projections (P10/P50/P90)
        sa.Column('closing_inventory_p10', sa.Double(), nullable=True, comment='10th percentile (optimistic)'),
        sa.Column('closing_inventory_p50', sa.Double(), nullable=True, comment='50th percentile (median)'),
        sa.Column('closing_inventory_p90', sa.Double(), nullable=True, comment='90th percentile (pessimistic)'),
        sa.Column('closing_inventory_std_dev', sa.Double(), nullable=True, comment='Standard deviation'),

        # Extension: Stockout Risk
        sa.Column('stockout_probability', sa.Double(), nullable=True, comment='Probability of stockout (0-1)'),
        sa.Column('days_of_supply', sa.Double(), nullable=True, comment='Inventory coverage in days'),

        # Extension: Scenario Tracking
        sa.Column('scenario_id', sa.String(100), nullable=True, comment='What-if scenario identifier'),
        sa.Column('scenario_name', sa.String(255), nullable=True, comment='Scenario description'),

        # AWS SC Source Tracking
        sa.Column('source', sa.String(100), nullable=True, comment='Source system'),
        sa.Column('source_event_id', sa.String(100), nullable=True, comment='Source event identifier'),
        sa.Column('source_update_dttm', sa.DateTime(), nullable=True, comment='Source update timestamp'),

        # Extension: Beer Game Integration
        sa.Column('config_id', sa.Integer(), nullable=True),
        sa.Column('game_id', sa.Integer(), nullable=True),
        sa.Column('round_number', sa.Integer(), nullable=True, comment='Beer Game round'),

        # Audit Fields
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),

        # Constraints
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['company_id'], ['groups.id'], name='fk_inv_projection_company'),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'], name='fk_inv_projection_product'),
        sa.ForeignKeyConstraint(['site_id'], ['nodes.id'], name='fk_inv_projection_site'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], name='fk_inv_projection_config'),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], name='fk_inv_projection_game'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_inv_projection_created_by'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name='fk_inv_projection_updated_by'),
    )

    # Indexes for inv_projection
    op.create_index('idx_inv_projection_lookup', 'inv_projection', ['product_id', 'site_id', 'projection_date'])
    op.create_index('idx_inv_projection_scenario', 'inv_projection', ['scenario_id', 'projection_date'])
    op.create_index('idx_inv_projection_game', 'inv_projection', ['game_id', 'round_number'])

    # ===================================================================
    # atp_projection table
    # ===================================================================
    op.create_table(
        'atp_projection',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),

        # AWS SC Core Fields
        sa.Column('company_id', sa.Integer(), nullable=False, comment='Company/customer ID'),
        sa.Column('product_id', sa.Integer(), nullable=False, comment='Product ID (items table)'),
        sa.Column('site_id', sa.Integer(), nullable=False, comment='Site ID (nodes table)'),
        sa.Column('atp_date', sa.Date(), nullable=False, comment='ATP date'),

        # ATP Quantities
        sa.Column('atp_qty', sa.Double(), nullable=False, server_default='0.0', comment='Available-to-Promise for this period'),
        sa.Column('cumulative_atp_qty', sa.Double(), nullable=False, server_default='0.0', comment='Cumulative ATP through this date'),

        # Components
        sa.Column('opening_balance', sa.Double(), nullable=False, server_default='0.0', comment='Opening balance'),
        sa.Column('supply_qty', sa.Double(), nullable=False, server_default='0.0', comment='Supply receipts'),
        sa.Column('demand_qty', sa.Double(), nullable=False, server_default='0.0', comment='Demand/consumption'),
        sa.Column('allocated_qty', sa.Double(), nullable=False, server_default='0.0', comment='Already allocated quantity'),

        # Extension: Customer Allocation
        sa.Column('customer_id', sa.String(100), nullable=True, comment='Specific customer allocation'),
        sa.Column('allocation_percentage', sa.Double(), nullable=True, comment='Percentage of ATP allocated to customer'),
        sa.Column('allocation_priority', sa.Integer(), nullable=True, comment='Allocation priority (1=highest)'),

        # Extension: ATP Rules
        sa.Column('atp_rule', sa.String(50), nullable=True, comment='discrete, cumulative, rolling'),
        sa.Column('time_fence_days', sa.Integer(), nullable=True, comment='Planning time fence in days'),

        # Source Tracking
        sa.Column('source', sa.String(100), nullable=True, comment='Source system'),
        sa.Column('source_event_id', sa.String(100), nullable=True, comment='Source event identifier'),
        sa.Column('source_update_dttm', sa.DateTime(), nullable=True, comment='Source update timestamp'),

        # Beer Game Integration
        sa.Column('config_id', sa.Integer(), nullable=True),
        sa.Column('game_id', sa.Integer(), nullable=True),

        # Audit
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),

        # Constraints
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['company_id'], ['groups.id'], name='fk_atp_projection_company'),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'], name='fk_atp_projection_product'),
        sa.ForeignKeyConstraint(['site_id'], ['nodes.id'], name='fk_atp_projection_site'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], name='fk_atp_projection_config'),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], name='fk_atp_projection_game'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_atp_projection_created_by'),
    )

    # Indexes for atp_projection
    op.create_index('idx_atp_projection_lookup', 'atp_projection', ['product_id', 'site_id', 'atp_date'])
    op.create_index('idx_atp_projection_customer', 'atp_projection', ['customer_id', 'atp_date'])

    # ===================================================================
    # ctp_projection table
    # ===================================================================
    op.create_table(
        'ctp_projection',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),

        # AWS SC Core Fields
        sa.Column('company_id', sa.Integer(), nullable=False, comment='Company/customer ID'),
        sa.Column('product_id', sa.Integer(), nullable=False, comment='Product ID (items table)'),
        sa.Column('site_id', sa.Integer(), nullable=False, comment='Site ID (nodes table)'),
        sa.Column('ctp_date', sa.Date(), nullable=False, comment='CTP date'),

        # CTP Quantities
        sa.Column('ctp_qty', sa.Double(), nullable=False, server_default='0.0', comment='Capable-to-Promise'),
        sa.Column('atp_qty', sa.Double(), nullable=False, server_default='0.0', comment='ATP component of CTP'),
        sa.Column('production_capacity_qty', sa.Double(), nullable=False, server_default='0.0', comment='Available production capacity'),

        # Capacity Components
        sa.Column('total_capacity', sa.Double(), nullable=True, comment='Total production capacity'),
        sa.Column('committed_capacity', sa.Double(), nullable=True, comment='Already committed capacity'),
        sa.Column('available_capacity', sa.Double(), nullable=True, comment='Remaining available capacity'),

        # Extension: Component Availability Check
        sa.Column('component_constrained', sa.Boolean(), nullable=False, server_default=sa.text("FALSE"), comment='Limited by component availability'),
        sa.Column('constraining_component_id', sa.Integer(), nullable=True, comment='Component limiting CTP'),

        # Extension: Resource Capacity Check
        sa.Column('resource_constrained', sa.Boolean(), nullable=False, server_default=sa.text("FALSE"), comment='Limited by resource capacity'),
        sa.Column('constraining_resource', sa.String(255), nullable=True, comment='Resource limiting CTP'),

        # Extension: Lead Time
        sa.Column('production_lead_time', sa.Integer(), nullable=True, comment='Production lead time in days'),
        sa.Column('earliest_ship_date', sa.Date(), nullable=True, comment='Earliest possible ship date'),

        # Source Tracking
        sa.Column('source', sa.String(100), nullable=True, comment='Source system'),
        sa.Column('source_event_id', sa.String(100), nullable=True, comment='Source event identifier'),
        sa.Column('source_update_dttm', sa.DateTime(), nullable=True, comment='Source update timestamp'),

        # Beer Game Integration
        sa.Column('config_id', sa.Integer(), nullable=True),
        sa.Column('game_id', sa.Integer(), nullable=True),

        # Audit
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),

        # Constraints
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['company_id'], ['groups.id'], name='fk_ctp_projection_company'),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'], name='fk_ctp_projection_product'),
        sa.ForeignKeyConstraint(['site_id'], ['nodes.id'], name='fk_ctp_projection_site'),
        sa.ForeignKeyConstraint(['constraining_component_id'], ['items.id'], name='fk_ctp_projection_component'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], name='fk_ctp_projection_config'),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], name='fk_ctp_projection_game'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_ctp_projection_created_by'),
    )

    # Indexes for ctp_projection
    op.create_index('idx_ctp_projection_lookup', 'ctp_projection', ['product_id', 'site_id', 'ctp_date'])
    op.create_index('idx_ctp_projection_constraint', 'ctp_projection', ['component_constrained', 'resource_constrained'])

    # ===================================================================
    # order_promise table
    # ===================================================================
    op.create_table(
        'order_promise',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),

        # Order Reference
        sa.Column('order_id', sa.String(100), nullable=False, comment='Customer order ID'),
        sa.Column('order_line_number', sa.Integer(), nullable=False, comment='Order line number'),

        # AWS SC Core Fields
        sa.Column('company_id', sa.Integer(), nullable=False, comment='Company/customer ID'),
        sa.Column('product_id', sa.Integer(), nullable=False, comment='Product ID (items table)'),
        sa.Column('site_id', sa.Integer(), nullable=False, comment='Site ID (nodes table)'),
        sa.Column('customer_id', sa.String(100), nullable=True, comment='Customer ID'),

        # Order Details
        sa.Column('requested_quantity', sa.Double(), nullable=False, comment='Requested quantity'),
        sa.Column('requested_date', sa.Date(), nullable=False, comment='Requested delivery date'),

        # Promise Details
        sa.Column('promised_quantity', sa.Double(), nullable=False, comment='Quantity promised'),
        sa.Column('promised_date', sa.Date(), nullable=False, comment='Promised delivery date'),
        sa.Column('promise_source', sa.String(50), nullable=False, comment='ATP, CTP, or BACKORDER'),

        # Extension: Fulfillment Strategy
        sa.Column('fulfillment_type', sa.String(50), nullable=False, server_default='single', comment='single, partial, split, substitute'),
        sa.Column('partial_promise', sa.Boolean(), nullable=False, server_default=sa.text("FALSE"), comment='Partial quantity promise'),
        sa.Column('backorder_quantity', sa.Double(), nullable=True, comment='Quantity on backorder'),
        sa.Column('backorder_date', sa.Date(), nullable=True, comment='Expected backorder delivery'),

        # Extension: Alternative Options
        sa.Column('alternative_quantity', sa.Double(), nullable=True, comment='Alternative quantity available'),
        sa.Column('alternative_date', sa.Date(), nullable=True, comment='Alternative delivery date'),
        sa.Column('alternative_product_id', sa.Integer(), nullable=True, comment='Substitute product'),

        # Promise Status
        sa.Column('promise_status', sa.String(50), nullable=False, server_default='PROPOSED', comment='PROPOSED, CONFIRMED, FULFILLED, CANCELLED'),
        sa.Column('promise_confidence', sa.Double(), nullable=True, comment='Confidence level (0-1)'),

        # Source Tracking
        sa.Column('source', sa.String(100), nullable=True, comment='Source system'),
        sa.Column('source_event_id', sa.String(100), nullable=True, comment='Source event identifier'),
        sa.Column('source_update_dttm', sa.DateTime(), nullable=True, comment='Source update timestamp'),

        # Audit
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),

        # Constraints
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['company_id'], ['groups.id'], name='fk_order_promise_company'),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'], name='fk_order_promise_product'),
        sa.ForeignKeyConstraint(['site_id'], ['nodes.id'], name='fk_order_promise_site'),
        sa.ForeignKeyConstraint(['alternative_product_id'], ['items.id'], name='fk_order_promise_alt_product'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_order_promise_created_by'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name='fk_order_promise_updated_by'),
    )

    # Indexes for order_promise
    op.create_index('idx_order_promise_order', 'order_promise', ['order_id', 'order_line_number'])
    op.create_index('idx_order_promise_product', 'order_promise', ['product_id', 'site_id', 'requested_date'])
    op.create_index('idx_order_promise_customer', 'order_promise', ['customer_id', 'promise_status'])


def downgrade() -> None:
    # Drop tables in reverse order (respect FK constraints)
    op.drop_table('order_promise')
    op.drop_table('ctp_projection')
    op.drop_table('atp_projection')
    op.drop_table('inv_projection')
