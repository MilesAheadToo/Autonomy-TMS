"""Add missing AWS SC history tables: consensus_demand, supplementary_time_series,
inventory_projection, backorder

Revision ID: 20260315_history
Revises: 20260311_config_mode
Create Date: 2026-03-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '20260315_history'
down_revision: Union[str, None] = '20260311_config_mode'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ===================================================================
    # consensus_demand
    # ===================================================================
    op.create_table(
        'consensus_demand',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('company_id', sa.String(100), nullable=True),
        sa.Column('product_id', sa.String(100), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=True),
        sa.Column('customer_id', sa.String(100), nullable=True),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('period_type', sa.String(20), server_default='MONTHLY', nullable=True),
        sa.Column('statistical_forecast', sa.Double(), nullable=True),
        sa.Column('sales_forecast', sa.Double(), nullable=True),
        sa.Column('marketing_forecast', sa.Double(), nullable=True),
        sa.Column('management_override', sa.Double(), nullable=True),
        sa.Column('consensus_quantity', sa.Double(), nullable=False),
        sa.Column('confidence_level', sa.Double(), nullable=True),
        sa.Column('consensus_p10', sa.Double(), nullable=True),
        sa.Column('consensus_p50', sa.Double(), nullable=True),
        sa.Column('consensus_p90', sa.Double(), nullable=True),
        sa.Column('adjustment_reason', sa.String(500), nullable=True),
        sa.Column('adjustment_type', sa.String(50), nullable=True),
        sa.Column('approved_by', sa.String(100), nullable=True),
        sa.Column('approval_date', sa.DateTime(), nullable=True),
        sa.Column('sop_cycle_id', sa.String(100), nullable=True),
        sa.Column('version', sa.Integer(), server_default='1', nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['company.id']),
        sa.ForeignKeyConstraint(['product_id'], ['product.id']),
        sa.ForeignKeyConstraint(['site_id'], ['site.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('product_id', 'site_id', 'period_start', 'version', name='uq_consensus_demand'),
    )
    op.create_index('idx_consensus_demand_lookup', 'consensus_demand', ['product_id', 'site_id', 'period_start'])

    # ===================================================================
    # supplementary_time_series
    # ===================================================================
    op.create_table(
        'supplementary_time_series',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('company_id', sa.String(100), nullable=True),
        sa.Column('series_name', sa.String(200), nullable=False),
        sa.Column('series_type', sa.String(50), nullable=False),
        sa.Column('product_id', sa.String(100), nullable=True),
        sa.Column('site_id', sa.Integer(), nullable=True),
        sa.Column('observation_date', sa.Date(), nullable=False),
        sa.Column('value', sa.Double(), nullable=False),
        sa.Column('unit', sa.String(50), nullable=True),
        sa.Column('confidence', sa.Double(), nullable=True),
        sa.Column('source_channel', sa.String(50), nullable=True),
        sa.Column('signal_direction', sa.String(20), nullable=True),
        sa.Column('magnitude', sa.Double(), nullable=True),
        sa.Column('is_processed', sa.Boolean(), server_default=sa.text('false'), nullable=True),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.Column('forecast_impact', sa.Double(), nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['company.id']),
        sa.ForeignKeyConstraint(['product_id'], ['product.id']),
        sa.ForeignKeyConstraint(['site_id'], ['site.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_supp_ts_lookup', 'supplementary_time_series', ['product_id', 'site_id', 'observation_date'])
    op.create_index('idx_supp_ts_type', 'supplementary_time_series', ['series_type', 'observation_date'])
    op.create_index('idx_supp_ts_unprocessed', 'supplementary_time_series', ['is_processed', 'series_type'])

    # ===================================================================
    # inventory_projection
    # ===================================================================
    op.create_table(
        'inventory_projection',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('company_id', sa.String(100), nullable=True),
        sa.Column('site_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.String(100), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('period_type', sa.String(20), server_default='WEEKLY', nullable=True),
        sa.Column('beginning_on_hand', sa.Double(), server_default='0', nullable=True),
        sa.Column('gross_requirements', sa.Double(), server_default='0', nullable=True),
        sa.Column('scheduled_receipts', sa.Double(), server_default='0', nullable=True),
        sa.Column('planned_receipts', sa.Double(), server_default='0', nullable=True),
        sa.Column('projected_on_hand', sa.Double(), server_default='0', nullable=True),
        sa.Column('atp_quantity', sa.Double(), server_default='0', nullable=True),
        sa.Column('ctp_quantity', sa.Double(), nullable=True),
        sa.Column('cumulative_atp', sa.Double(), server_default='0', nullable=True),
        sa.Column('safety_stock', sa.Double(), server_default='0', nullable=True),
        sa.Column('projected_available', sa.Double(), server_default='0', nullable=True),
        sa.Column('projected_on_hand_p10', sa.Double(), nullable=True),
        sa.Column('projected_on_hand_p50', sa.Double(), nullable=True),
        sa.Column('projected_on_hand_p90', sa.Double(), nullable=True),
        sa.Column('atp_p10', sa.Double(), nullable=True),
        sa.Column('atp_p90', sa.Double(), nullable=True),
        sa.Column('supply_plan_id', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['company.id']),
        sa.ForeignKeyConstraint(['product_id'], ['product.id']),
        sa.ForeignKeyConstraint(['site_id'], ['site.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('site_id', 'product_id', 'period_start', 'source', name='uq_inv_projection_unique'),
    )
    op.create_index('idx_inv_projection_lookup', 'inventory_projection', ['site_id', 'product_id', 'period_start'])
    op.create_index('idx_inv_projection_product_period', 'inventory_projection', ['product_id', 'period_start'])

    # ===================================================================
    # backorder
    # ===================================================================
    op.create_table(
        'backorder',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('company_id', sa.String(100), nullable=True),
        sa.Column('backorder_id', sa.String(100), nullable=False),
        sa.Column('order_id', sa.String(100), nullable=False),
        sa.Column('order_line_id', sa.Integer(), nullable=True),
        sa.Column('product_id', sa.String(100), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.String(100), nullable=True),
        sa.Column('backorder_quantity', sa.Double(), nullable=False),
        sa.Column('allocated_quantity', sa.Double(), server_default='0', nullable=True),
        sa.Column('fulfilled_quantity', sa.Double(), server_default='0', nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='CREATED'),
        sa.Column('requested_delivery_date', sa.Date(), nullable=True),
        sa.Column('expected_fill_date', sa.Date(), nullable=True),
        sa.Column('created_date', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('allocated_date', sa.DateTime(), nullable=True),
        sa.Column('fulfilled_date', sa.DateTime(), nullable=True),
        sa.Column('closed_date', sa.DateTime(), nullable=True),
        sa.Column('priority', sa.Integer(), server_default='3', nullable=True),
        sa.Column('priority_code', sa.String(20), server_default='STANDARD', nullable=True),
        sa.Column('aging_days', sa.Integer(), server_default='0', nullable=True),
        sa.Column('allocated_supply_plan_id', sa.Integer(), nullable=True),
        sa.Column('supply_commit_id', sa.Integer(), nullable=True),
        sa.Column('config_id', sa.Integer(), nullable=True),
        sa.Column('scenario_id', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['company.id']),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['order_line_id'], ['outbound_order_line.id']),
        sa.ForeignKeyConstraint(['product_id'], ['product.id']),
        sa.ForeignKeyConstraint(['scenario_id'], ['scenarios.id']),
        sa.ForeignKeyConstraint(['site_id'], ['site.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_backorder_lookup', 'backorder', ['product_id', 'site_id', 'status'])
    op.create_index('idx_backorder_order', 'backorder', ['order_id'])
    op.create_index('idx_backorder_priority', 'backorder', ['priority', 'created_date'])
    op.create_index('idx_backorder_aging', 'backorder', ['aging_days', 'status'])
    op.create_index('idx_backorder_id', 'backorder', ['backorder_id'], unique=True)


def downgrade() -> None:
    op.drop_table('backorder')
    op.drop_table('inventory_projection')
    op.drop_table('supplementary_time_series')
    op.drop_table('consensus_demand')
