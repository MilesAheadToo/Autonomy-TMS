"""Add RCCP tables: bill_of_resources and rccp_runs

Revision ID: 20260313_rccp
Revises: None (standalone)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

revision = '20260313_rccp'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Bill of Resources
    op.create_table(
        'bill_of_resources',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('product.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('site.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('resource_id', sa.Integer(), sa.ForeignKey('capacity_resources.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('overall_hours_per_unit', sa.Float(), nullable=True),
        sa.Column('hours_per_unit', sa.Float(), nullable=True),
        sa.Column('setup_hours_per_batch', sa.Float(), nullable=False, server_default='0'),
        sa.Column('typical_batch_size', sa.Float(), nullable=False, server_default='1'),
        sa.Column('phase', sa.Enum('setup', 'run', 'teardown', 'queue', 'move', name='productionphase'), nullable=True),
        sa.Column('lead_time_offset_days', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('phase_hours_per_unit', sa.Float(), nullable=True),
        sa.Column('is_critical', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('production_process_id', sa.Integer(), sa.ForeignKey('production_process.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('config_id', 'product_id', 'site_id', 'resource_id', 'phase',
                            name='uq_bor_config_product_site_resource_phase'),
    )

    # RCCP Runs
    op.create_table(
        'rccp_runs',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('mps_plan_id', sa.Integer(), sa.ForeignKey('mps_plans.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('site.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('method', sa.Enum('cpof', 'bill_of_capacity', 'resource_profile', name='rccpmethod'), nullable=False),
        sa.Column('status', sa.Enum('feasible', 'overloaded', 'levelling_recommended', 'escalate_to_sop', name='rccprunstatus'), nullable=False),
        sa.Column('is_feasible', sa.Boolean(), nullable=False),
        sa.Column('planning_horizon_weeks', sa.Integer(), nullable=False),
        sa.Column('start_date', sa.DateTime(), nullable=False),
        sa.Column('end_date', sa.DateTime(), nullable=False),
        sa.Column('max_utilization_pct', sa.Float(), nullable=True),
        sa.Column('avg_utilization_pct', sa.Float(), nullable=True),
        sa.Column('overloaded_resource_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('overloaded_week_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('chronic_overload_resources', JSON, nullable=False, server_default='[]'),
        sa.Column('overtime_required', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('mps_adjustments', JSON, nullable=False, server_default='[]'),
        sa.Column('resource_loads', JSON, nullable=False, server_default='[]'),
        sa.Column('rules_applied', JSON, nullable=False, server_default='[]'),
        sa.Column('demand_variability_cv', sa.Float(), nullable=True),
        sa.Column('variability_buffer_applied', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('changeover_adjusted', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('total_changeover_hours', sa.Float(), nullable=True),
        sa.Column('changeover_details', JSON, nullable=False, server_default='[]'),
        sa.Column('glenday_summary', JSON, nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table('rccp_runs')
    op.drop_table('bill_of_resources')
    op.execute("DROP TYPE IF EXISTS rccprunstatus")
    op.execute("DROP TYPE IF EXISTS rccpmethod")
    op.execute("DROP TYPE IF EXISTS productionphase")
