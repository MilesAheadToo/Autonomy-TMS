"""Add Phase 5 Stochastic Lead Time distribution columns

Revision ID: 20260130_stochastic_lead_times
Revises: 20260130_copilot
Create Date: 2026-01-30 16:00:00.000000

Adds distribution columns for stochastic lead time modeling:
- Lane table: supply_lead_time_dist, demand_lead_time_dist
- TransportationLane table: transit_time_dist

Distribution fields store JSON with format:
{
    "type": "normal|lognormal|triangular|beta|mixture|...",
    "mean": 7.0,
    "stddev": 1.5,
    "min": 3.0,
    "max": 12.0
}

NULL = use deterministic value from base field (backward compatible)
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '20260130_stochastic_lead_times'
down_revision = '20260130_copilot'
branch_labels = None
depends_on = None


def upgrade():
    # Add stochastic lead time distribution columns to lanes table
    op.add_column(
        'lanes',
        sa.Column('supply_lead_time_dist', sa.JSON(), nullable=True,
                  comment='Distribution params for supply lead time (material flow)')
    )
    op.add_column(
        'lanes',
        sa.Column('demand_lead_time_dist', sa.JSON(), nullable=True,
                  comment='Distribution params for demand lead time (info flow)')
    )

    # Add stochastic transit time distribution to transportation_lane table
    op.add_column(
        'transportation_lane',
        sa.Column('transit_time_dist', sa.JSON(), nullable=True,
                  comment='Distribution params for transit time variability')
    )

    # Add probabilistic ATP fields to atp_projection table
    # These store P10/P50/P90 percentiles from Monte Carlo simulation
    op.add_column(
        'atp_projection',
        sa.Column('atp_p10', sa.Integer(), nullable=True,
                  comment='10th percentile ATP (pessimistic)')
    )
    op.add_column(
        'atp_projection',
        sa.Column('atp_p90', sa.Integer(), nullable=True,
                  comment='90th percentile ATP (optimistic)')
    )
    op.add_column(
        'atp_projection',
        sa.Column('lead_time_mean', sa.Float(), nullable=True,
                  comment='Mean lead time used in calculation')
    )
    op.add_column(
        'atp_projection',
        sa.Column('lead_time_stddev', sa.Float(), nullable=True,
                  comment='Lead time standard deviation')
    )

    # Add probabilistic fields to ctp_projection table
    op.add_column(
        'ctp_projection',
        sa.Column('ctp_p10', sa.Integer(), nullable=True,
                  comment='10th percentile CTP (pessimistic)')
    )
    op.add_column(
        'ctp_projection',
        sa.Column('ctp_p90', sa.Integer(), nullable=True,
                  comment='90th percentile CTP (optimistic)')
    )
    op.add_column(
        'ctp_projection',
        sa.Column('production_lead_time_mean', sa.Float(), nullable=True,
                  comment='Mean production lead time')
    )
    op.add_column(
        'ctp_projection',
        sa.Column('production_lead_time_stddev', sa.Float(), nullable=True,
                  comment='Production lead time standard deviation')
    )


def downgrade():
    # Remove columns from ctp_projection
    op.drop_column('ctp_projection', 'production_lead_time_stddev')
    op.drop_column('ctp_projection', 'production_lead_time_mean')
    op.drop_column('ctp_projection', 'ctp_p90')
    op.drop_column('ctp_projection', 'ctp_p10')

    # Remove columns from atp_projection
    op.drop_column('atp_projection', 'lead_time_stddev')
    op.drop_column('atp_projection', 'lead_time_mean')
    op.drop_column('atp_projection', 'atp_p90')
    op.drop_column('atp_projection', 'atp_p10')

    # Remove columns from transportation_lane
    op.drop_column('transportation_lane', 'transit_time_dist')

    # Remove columns from lanes
    op.drop_column('lanes', 'demand_lead_time_dist')
    op.drop_column('lanes', 'supply_lead_time_dist')
