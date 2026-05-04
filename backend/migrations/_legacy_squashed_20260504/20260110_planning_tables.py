"""Add AWS SC planning tables

Revision ID: 20260110_planning
Revises: 20260109_phase3_structural
Create Date: 2026-01-10

Add tables required for AWS SC 3-step planning process:
- product_bom: Bill of materials for manufacturing
- production_process: Production process definitions
- forecast: Demand forecasts
- supply_plan: Planning output (PO/TO/MO requests)
- reservation: Inventory reservations
- vendor_lead_time: Supplier lead times
- outbound_order_line: Customer orders
- supply_planning_parameters: Planning configuration
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers
revision = '20260110_planning'
down_revision = '20260109_phase3_structural'
branch_labels = None
depends_on = None


def upgrade():
    # =========================================================================
    # 1. PRODUCTION_PROCESS: Manufacturing process definitions
    # =========================================================================

    op.create_table(
        'production_process',
        sa.Column('id', sa.String(100), primary_key=True),
        sa.Column('description', sa.String(500)),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('nodes.id')),
        sa.Column('manufacturing_leadtime', sa.Integer(), comment='Lead time in days'),
        sa.Column('cycle_time', sa.Integer(), comment='Cycle time in days'),
        sa.Column('yield_percentage', sa.Double, server_default='100.0'),
        sa.Column('capacity_units', sa.Double),
        sa.Column('capacity_period', sa.String(20), comment='day, week, month'),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('idx_prod_process_site', 'production_process', ['site_id'])
    op.create_index('idx_prod_process_config', 'production_process', ['config_id'])

    # =========================================================================
    # 2. PRODUCT_BOM: Bill of materials
    # =========================================================================

    op.create_table(
        'product_bom',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('component_product_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('component_quantity', sa.Double, nullable=False),
        sa.Column('production_process_id', sa.String(100), sa.ForeignKey('production_process.id')),
        sa.Column('alternate_group', sa.Integer(), server_default=sa.text("0")),
        sa.Column('priority', sa.Integer(), server_default=sa.text("1")),
        sa.Column('scrap_percentage', sa.Double, server_default='0.0'),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('idx_bom_product', 'product_bom', ['product_id'])
    op.create_index('idx_bom_component', 'product_bom', ['component_product_id'])
    op.create_index('idx_bom_config', 'product_bom', ['config_id'])

    # =========================================================================
    # 3. FORECAST: Demand forecasts
    # =========================================================================

    op.create_table(
        'forecast',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('nodes.id'), nullable=False),
        sa.Column('forecast_date', sa.Date(), nullable=False),
        sa.Column('forecast_quantity', sa.Double),
        sa.Column('forecast_p50', sa.Double, comment='Median forecast'),
        sa.Column('forecast_p10', sa.Double),
        sa.Column('forecast_p90', sa.Double),
        sa.Column('user_override_quantity', sa.Double),
        sa.Column('is_active', sa.String(10), server_default='true'),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
        sa.Column('game_id', sa.Integer(), sa.ForeignKey('games.id')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('idx_forecast_lookup', 'forecast', ['product_id', 'site_id', 'forecast_date'])
    op.create_index('idx_forecast_config', 'forecast', ['config_id'])
    op.create_index('idx_forecast_game', 'forecast', ['game_id'])

    # =========================================================================
    # 4. SUPPLY_PLAN: Planning output (PO/TO/MO requests)
    # =========================================================================

    op.create_table(
        'supply_plan',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('plan_type', sa.String(20), nullable=False, comment='po_request, to_request, mo_request'),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('destination_site_id', sa.Integer(), sa.ForeignKey('nodes.id'), nullable=False),
        sa.Column('source_site_id', sa.Integer(), sa.ForeignKey('nodes.id')),
        sa.Column('vendor_id', sa.String(100)),
        sa.Column('production_process_id', sa.String(100), sa.ForeignKey('production_process.id')),
        sa.Column('planned_order_quantity', sa.Double, nullable=False),
        sa.Column('planned_order_date', sa.Date(), nullable=False),
        sa.Column('planned_receipt_date', sa.Date(), nullable=False),
        sa.Column('lead_time_days', sa.Integer()),
        sa.Column('unit_cost', sa.Double),
        sa.Column('planning_run_id', sa.String(100)),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
        sa.Column('game_id', sa.Integer(), sa.ForeignKey('games.id')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('idx_supply_plan_lookup', 'supply_plan', ['product_id', 'destination_site_id', 'planned_order_date'])
    op.create_index('idx_supply_plan_config', 'supply_plan', ['config_id'])
    op.create_index('idx_supply_plan_game', 'supply_plan', ['game_id'])

    # =========================================================================
    # 5. RESERVATION: Inventory reservations
    # =========================================================================

    op.create_table(
        'reservation',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('nodes.id'), nullable=False),
        sa.Column('reservation_date', sa.Date(), nullable=False),
        sa.Column('reserved_quantity', sa.Double, nullable=False),
        sa.Column('reservation_type', sa.String(50), comment='component, customer_order, transfer'),
        sa.Column('reference_id', sa.String(100)),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
        sa.Column('game_id', sa.Integer(), sa.ForeignKey('games.id')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('idx_reservation_lookup', 'reservation', ['product_id', 'site_id', 'reservation_date'])
    op.create_index('idx_reservation_config', 'reservation', ['config_id'])

    # =========================================================================
    # 6. VENDOR_LEAD_TIME: Supplier lead times
    # =========================================================================

    op.create_table(
        'vendor_lead_time',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('vendor_id', sa.String(100), nullable=False),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('items.id')),
        sa.Column('product_group_id', sa.String(100)),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('nodes.id')),
        sa.Column('geo_id', sa.String(100)),
        sa.Column('company_id', sa.String(100)),
        sa.Column('lead_time_days', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('idx_vendor_lt_override', 'vendor_lead_time',
                   ['product_id', 'product_group_id', 'site_id', 'geo_id', 'company_id'])
    op.create_index('idx_vendor_lt_config', 'vendor_lead_time', ['config_id'])

    # =========================================================================
    # 7. OUTBOUND_ORDER_LINE: Customer orders (actuals)
    # =========================================================================

    op.create_table(
        'outbound_order_line',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('order_id', sa.String(100), nullable=False),
        sa.Column('line_number', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('nodes.id'), nullable=False),
        sa.Column('ordered_quantity', sa.Double, nullable=False),
        sa.Column('requested_delivery_date', sa.Date(), nullable=False),
        sa.Column('order_date', sa.Date()),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
        sa.Column('game_id', sa.Integer(), sa.ForeignKey('games.id')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('idx_outbound_order_lookup', 'outbound_order_line',
                   ['product_id', 'site_id', 'requested_delivery_date'])
    op.create_index('idx_outbound_order_config', 'outbound_order_line', ['config_id'])

    # =========================================================================
    # 8. SUPPLY_PLANNING_PARAMETERS: Planning configuration
    # =========================================================================

    op.create_table(
        'supply_planning_parameters',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('items.id')),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('nodes.id')),
        sa.Column('planning_time_fence', sa.Integer(), comment='Days frozen'),
        sa.Column('lot_size_rule', sa.String(50)),
        sa.Column('lot_size_value', sa.Double),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('idx_spp_lookup', 'supply_planning_parameters', ['product_id', 'site_id'])


def downgrade():
    op.drop_table('supply_planning_parameters')
    op.drop_table('outbound_order_line')
    op.drop_table('vendor_lead_time')
    op.drop_table('reservation')
    op.drop_table('supply_plan')
    op.drop_table('forecast')
    op.drop_table('product_bom')
    op.drop_table('production_process')
