"""Add Forecast Baseline TRM decision table

Revision ID: 20260404_forecast_baseline
Revises: 20260403_operating_schedule
Create Date: 2026-04-04

12th TRM: Forecast Baseline — orchestrates the statistical demand forecast
pipeline (model selection, retraining, cross-product features, external
signals, conformal calibration).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


revision: str = '20260404_forecast_baseline'
down_revision: Union[str, None] = '20260403_operating_schedule'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'powell_forecast_baseline_decisions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('product_id', sa.String(100), nullable=False),
        sa.Column('site_id', sa.String(100), nullable=False),

        # Demand classification
        sa.Column('demand_profile', sa.String(30), nullable=False),

        # Model decision
        sa.Column('recommended_model', sa.String(30), nullable=False),
        sa.Column('model_changed', sa.Boolean(), server_default='false'),

        # Retrain decision
        sa.Column('retrain_recommended', sa.Boolean(), server_default='false'),
        sa.Column('retrain_reason', sa.String(50), nullable=True),

        # Accuracy
        sa.Column('current_mape', sa.Float(), nullable=True),
        sa.Column('fva_vs_naive', sa.Float(), nullable=True),

        # Forecast output
        sa.Column('forecast_p50', sa.Float(), nullable=True),
        sa.Column('forecast_p10', sa.Float(), nullable=True),
        sa.Column('forecast_p90', sa.Float(), nullable=True),
        sa.Column('conformal_interval_width', sa.Float(), nullable=True),

        # Feature decisions
        sa.Column('cross_product_enabled', sa.Boolean(), server_default='true'),
        sa.Column('external_signals', JSON, nullable=True),
        sa.Column('censored_demand_corrected', sa.Boolean(), server_default='false'),

        # Demand trend
        sa.Column('demand_trend', sa.String(10), nullable=True),
        sa.Column('demand_trend_magnitude', sa.Float(), nullable=True),

        # Quality
        sa.Column('confidence', sa.Float(), nullable=True),

        # HiveSignalMixin columns
        sa.Column('status', sa.String(20), server_default='ACTIONED'),
        sa.Column('decision_level', sa.String(20), server_default='tactical'),
        sa.Column('signal_context', JSON, nullable=True),
        sa.Column('urgency_at_time', sa.Float(), nullable=True),
        sa.Column('cycle_phase', sa.String(30), nullable=True),
        sa.Column('cycle_id', sa.String(36), nullable=True),
        sa.Column('decision_reasoning', sa.Text(), nullable=True),
        sa.Column('cost_of_inaction', sa.Float(), nullable=True),
        sa.Column('time_pressure', sa.Float(), nullable=True),
        sa.Column('expected_benefit', sa.Float(), nullable=True),

        # Outcome tracking
        sa.Column('was_retrained', sa.Boolean(), nullable=True),
        sa.Column('mape_after_retrain', sa.Float(), nullable=True),

        sa.Column('created_at', sa.DateTime(), server_default=sa.text('NOW()')),
    )

    op.create_index('ix_powell_fb_config', 'powell_forecast_baseline_decisions', ['config_id'])
    op.create_index('ix_powell_fb_product_site', 'powell_forecast_baseline_decisions', ['product_id', 'site_id'])


def downgrade() -> None:
    op.drop_index('ix_powell_fb_product_site', 'powell_forecast_baseline_decisions')
    op.drop_index('ix_powell_fb_config', 'powell_forecast_baseline_decisions')
    op.drop_table('powell_forecast_baseline_decisions')
