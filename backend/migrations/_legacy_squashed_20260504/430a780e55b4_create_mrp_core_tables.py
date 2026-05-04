"""create_mrp_core_tables

Revision ID: 430a780e55b4
Revises: 2baddc291757
Create Date: 2026-01-21 13:04:16.876847

Creates 4 core AWS Supply Chain planning tables required for MRP:
1. production_process - Manufacturing process definitions
2. product_bom - Bill of Materials (AWS SC compliant)
3. sourcing_rules - Buy/Transfer/Manufacture rules with priorities (AWS SC compliant)
4. inv_policy - Inventory policies with 4 AWS SC policy types (AWS SC compliant)
5. inv_level - Current inventory levels (AWS SC compliant)
6. forecast - Demand forecasts
7. supply_plan - Planning output (PO/TO/MO requests)

All tables follow AWS Supply Chain Data Model specifications.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '430a780e55b4'
down_revision: Union[str, None] = '2baddc291757'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id', ondelete='CASCADE')),
    )
    op.create_index('idx_prod_process_site', 'production_process', ['site_id'])
    op.create_index('idx_prod_process_config', 'production_process', ['config_id'])

    # =========================================================================
    # 2. PRODUCT_BOM: Bill of Materials (AWS SC)
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
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id', ondelete='CASCADE')),
    )
    op.create_index('idx_bom_product', 'product_bom', ['product_id'])
    op.create_index('idx_bom_component', 'product_bom', ['component_product_id'])
    op.create_index('idx_bom_config', 'product_bom', ['config_id'])

    # =========================================================================
    # 3. SOURCING_RULES: Buy/Transfer/Manufacture rules (AWS SC)
    # =========================================================================
    op.create_table(
        'sourcing_rules',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('nodes.id'), nullable=False),
        sa.Column('supplier_site_id', sa.Integer(), sa.ForeignKey('nodes.id'), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('sourcing_rule_type', sa.String(50), nullable=False, comment='transfer, buy, manufacture'),
        sa.Column('allocation_percent', sa.Double, nullable=False, server_default='100.0'),
        sa.Column('min_qty', sa.Double),
        sa.Column('max_qty', sa.Double),
        sa.Column('qty_multiple', sa.Double),
        sa.Column('lead_time', sa.Integer(), comment='Lead time in days'),
        sa.Column('unit_cost', sa.Double),
        sa.Column('eff_start_date', sa.DateTime, nullable=False, server_default='1900-01-01 00:00:00'),
        sa.Column('eff_end_date', sa.DateTime, nullable=False, server_default='9999-12-31 23:59:59'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id', ondelete='CASCADE')),
        sa.Column('tpartner_id', sa.Integer(), comment='Trading partner for buy rules'),
        sa.Column('transportation_lane_id', sa.String(100), comment='Lane for transfer rules'),
        sa.Column('production_process_id', sa.String(100), sa.ForeignKey('production_process.id')),
        sa.Column('product_group_id', sa.String(100), comment='Hierarchical override level 2'),
        sa.Column('company_id', sa.String(100), comment='Hierarchical override level 3'),
    )
    op.create_index('idx_sourcing_product_site', 'sourcing_rules', ['product_id', 'site_id'])
    op.create_index('idx_sourcing_priority', 'sourcing_rules', ['product_id', 'site_id', 'priority'])
    op.create_index('idx_sourcing_config', 'sourcing_rules', ['config_id'])

    # =========================================================================
    # 4. INV_POLICY: Inventory policies with AWS SC 4 policy types
    # =========================================================================
    op.create_table(
        'inv_policy',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('nodes.id'), nullable=False),
        sa.Column('policy_type', sa.String(50), nullable=False, server_default='base_stock'),
        sa.Column('target_qty', sa.Double),
        sa.Column('min_qty', sa.Double),
        sa.Column('max_qty', sa.Double),
        sa.Column('reorder_point', sa.Double),
        sa.Column('order_qty', sa.Double),
        sa.Column('review_period', sa.Integer()),
        sa.Column('service_level', sa.Double),
        sa.Column('holding_cost', sa.Double),
        sa.Column('backlog_cost', sa.Double),
        sa.Column('selling_price', sa.Double),
        sa.Column('eff_start_date', sa.DateTime, nullable=False, server_default='1900-01-01 00:00:00'),
        sa.Column('eff_end_date', sa.DateTime, nullable=False, server_default='9999-12-31 23:59:59'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id', ondelete='CASCADE')),
        # AWS SC Safety Stock Policy Type fields
        sa.Column('ss_policy', sa.String(20), comment='Safety stock policy: abs_level, doc_dem, doc_fcst, sl'),
        sa.Column('ss_days', sa.Integer(), comment='Days of coverage for doc_dem/doc_fcst'),
        sa.Column('ss_quantity', sa.Double, comment='Absolute quantity for abs_level'),
        sa.Column('policy_value', sa.Double),
        sa.Column('order_up_to_level', sa.Double, comment='Target level for periodic review'),
        # Hierarchical override fields
        sa.Column('product_group_id', sa.String(100)),
        sa.Column('dest_geo_id', sa.String(100)),
        sa.Column('segment_id', sa.String(100)),
        sa.Column('company_id', sa.String(100)),
    )
    op.create_index('idx_inv_policy_product_site', 'inv_policy', ['product_id', 'site_id'])
    op.create_index('idx_inv_policy_config', 'inv_policy', ['config_id'])

    # =========================================================================
    # 5. INV_LEVEL: Current inventory levels (AWS SC)
    # =========================================================================
    op.create_table(
        'inv_level',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('nodes.id'), nullable=False),
        sa.Column('on_hand_quantity', sa.Double, server_default='0.0'),
        sa.Column('allocated_quantity', sa.Double, server_default='0.0'),
        sa.Column('available_quantity', sa.Double, server_default='0.0'),
        sa.Column('in_transit_quantity', sa.Double, server_default='0.0'),
        sa.Column('snapshot_date', sa.Date(), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id', ondelete='CASCADE')),
    )
    op.create_index('idx_inv_level_product_site', 'inv_level', ['product_id', 'site_id'])
    op.create_index('idx_inv_level_snapshot', 'inv_level', ['snapshot_date'])
    op.create_index('idx_inv_level_config', 'inv_level', ['config_id'])

    # =========================================================================
    # 6. FORECAST: Demand forecasts
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
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id', ondelete='CASCADE')),
        sa.Column('game_id', sa.Integer(), sa.ForeignKey('games.id')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_forecast_lookup', 'forecast', ['product_id', 'site_id', 'forecast_date'])
    op.create_index('idx_forecast_config', 'forecast', ['config_id'])

    # =========================================================================
    # 7. SUPPLY_PLAN: Planning output (PO/TO/MO requests)
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
        sa.Column('quantity', sa.Double, nullable=False, comment='Planned order quantity'),
        sa.Column('planned_order_quantity', sa.Double),
        sa.Column('planned_order_date', sa.Date(), nullable=False),
        sa.Column('planned_receipt_date', sa.Date(), nullable=False),
        sa.Column('lead_time_days', sa.Integer()),
        sa.Column('unit_cost', sa.Double),
        sa.Column('status', sa.String(20), server_default='DRAFT'),
        sa.Column('planning_run_id', sa.String(100)),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id', ondelete='CASCADE')),
        sa.Column('game_id', sa.Integer(), sa.ForeignKey('games.id')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_supply_plan_lookup', 'supply_plan', ['product_id', 'destination_site_id', 'planned_order_date'])
    op.create_index('idx_supply_plan_config', 'supply_plan', ['config_id'])
    op.create_index('idx_supply_plan_run', 'supply_plan', ['planning_run_id'])


def downgrade() -> None:
    # Drop tables in reverse order (respecting foreign keys)
    op.drop_table('supply_plan')
    op.drop_table('forecast')
    op.drop_table('inv_level')
    op.drop_table('inv_policy')
    op.drop_table('sourcing_rules')
    op.drop_table('product_bom')
    op.drop_table('production_process')
