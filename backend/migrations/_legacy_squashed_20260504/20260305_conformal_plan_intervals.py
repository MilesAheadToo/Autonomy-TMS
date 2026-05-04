"""conformal_plan_intervals

Revision ID: a1ded8e0aa88
Revises: 20260305_belief_cols
Create Date: 2026-03-05

Add conformal prediction interval metadata to supply plans and
plan-level confidence score to supply plan results.

supply_plan: 8 nullable columns for demand/lead-time interval bounds,
    coverage guarantees, and conformal method used to generate each plan line.

supply_plan_results: 1 nullable JSON column for composite plan confidence
    score with sub-scores and diagnostics.

All columns are nullable — existing data is unaffected.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1ded8e0aa88'
down_revision: Union[str, None] = '20260305_belief_cols'
branch_labels: Union[str, Sequence[str], None] = ('conformal_intervals',)
depends_on = None


def upgrade() -> None:
    # -- supply_plan: conformal interval metadata per plan line --
    op.add_column('supply_plan', sa.Column('demand_lower', sa.Double(), nullable=True))
    op.add_column('supply_plan', sa.Column('demand_upper', sa.Double(), nullable=True))
    op.add_column('supply_plan', sa.Column('demand_coverage', sa.Double(), nullable=True))
    op.add_column('supply_plan', sa.Column('lead_time_lower', sa.Double(), nullable=True))
    op.add_column('supply_plan', sa.Column('lead_time_upper', sa.Double(), nullable=True))
    op.add_column('supply_plan', sa.Column('lead_time_coverage', sa.Double(), nullable=True))
    op.add_column('supply_plan', sa.Column('joint_coverage', sa.Double(), nullable=True))
    op.add_column('supply_plan', sa.Column('conformal_method', sa.String(50), nullable=True))

    # -- supply_plan_results: plan-level confidence score --
    op.add_column('supply_plan_results', sa.Column('plan_confidence', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('supply_plan_results', 'plan_confidence')

    op.drop_column('supply_plan', 'conformal_method')
    op.drop_column('supply_plan', 'joint_coverage')
    op.drop_column('supply_plan', 'lead_time_coverage')
    op.drop_column('supply_plan', 'lead_time_upper')
    op.drop_column('supply_plan', 'lead_time_lower')
    op.drop_column('supply_plan', 'demand_coverage')
    op.drop_column('supply_plan', 'demand_upper')
    op.drop_column('supply_plan', 'demand_lower')
