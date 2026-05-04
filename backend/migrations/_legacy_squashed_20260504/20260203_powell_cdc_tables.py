"""Add Powell CDC (Change Detection and Control) tables

Revision ID: 20260203_powell_cdc_tables
Revises: 20260203_add_function_assignments
Create Date: 2026-02-03

Tables:
- powell_cdc_trigger_log: Audit trail for CDC trigger events
- powell_cdc_thresholds: Site-specific threshold configuration
- powell_site_agent_checkpoints: Model checkpoint metadata
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = '20260203_powell_cdc_tables'
down_revision = ('20260203_add_function_assignments', '20260203_fix_item_node')
branch_labels = None
depends_on = None


def upgrade():
    # CDC Trigger Log - audit trail for event-driven replanning
    op.create_table(
        'powell_cdc_trigger_log',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('site_key', sa.String(100), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('triggered_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('reasons', sa.JSON(), nullable=False),  # ["demand_deviation", "inventory_low"]
        sa.Column('action_taken', sa.String(50), nullable=False),  # "full_cfa", "allocation", "param_adj"
        sa.Column('severity', sa.String(20), nullable=False),  # "low", "medium", "high", "critical"
        sa.Column('metrics_snapshot', sa.JSON(), nullable=False),  # Full SiteMetrics at trigger time
        sa.Column('human_approved', sa.Boolean(), nullable=True),  # NULL=autonomous, TRUE/FALSE=copilot
        sa.Column('approved_by', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('execution_result', sa.JSON(), nullable=True),  # Outcome of action taken
        sa.Column('execution_duration_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ondelete='SET NULL'),
    )

    op.create_index(
        'ix_powell_cdc_trigger_log_site_triggered',
        'powell_cdc_trigger_log',
        ['site_key', 'triggered_at']
    )
    op.create_index(
        'ix_powell_cdc_trigger_log_group_triggered',
        'powell_cdc_trigger_log',
        ['customer_id', 'triggered_at']
    )
    op.create_index(
        'ix_powell_cdc_trigger_log_severity',
        'powell_cdc_trigger_log',
        ['severity', 'triggered_at']
    )

    # CDC Thresholds - site-specific threshold configuration
    op.create_table(
        'powell_cdc_thresholds',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('site_key', sa.String(100), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('threshold_type', sa.String(50), nullable=False),
        # Types: demand_deviation, inventory_ratio_low, inventory_ratio_high,
        #        service_level_drop, lead_time_increase, backlog_growth_days,
        #        supplier_reliability_drop
        sa.Column('threshold_value', sa.Float(), nullable=False),
        sa.Column('cooldown_hours', sa.Integer(), nullable=False, server_default='24'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=True, onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('site_key', 'threshold_type', 'effective_from',
                           name='uk_powell_cdc_thresholds_site_type_date'),
    )

    op.create_index(
        'ix_powell_cdc_thresholds_site',
        'powell_cdc_thresholds',
        ['site_key']
    )

    # Site Agent Checkpoints - model checkpoint metadata
    op.create_table(
        'powell_site_agent_checkpoints',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('site_key', sa.String(100), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('checkpoint_name', sa.String(100), nullable=False),  # "final", "bc_best", "epoch_50"
        sa.Column('checkpoint_path', sa.String(500), nullable=False),
        sa.Column('model_config', sa.JSON(), nullable=False),  # SiteAgentModelConfig
        sa.Column('training_config', sa.JSON(), nullable=True),  # TrainingConfig
        sa.Column('param_counts', sa.JSON(), nullable=False),  # {encoder: X, atp_head: Y, ...}
        sa.Column('training_metrics', sa.JSON(), nullable=True),  # Final metrics
        sa.Column('training_phases', sa.JSON(), nullable=True),  # ["bc", "supervised"]
        sa.Column('training_duration_seconds', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('activated_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id'], ondelete='CASCADE'),
    )

    op.create_index(
        'ix_powell_site_agent_checkpoints_site_active',
        'powell_site_agent_checkpoints',
        ['site_key', 'is_active']
    )

    # Site Agent Decision History - for training data and audit
    op.create_table(
        'powell_site_agent_decisions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('site_key', sa.String(100), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('decision_type', sa.String(50), nullable=False),  # "atp", "inventory", "po_timing"
        sa.Column('decision_timestamp', sa.DateTime(), nullable=False, server_default=sa.func.now()),

        # Input state (for training replay)
        sa.Column('state_features', sa.JSON(), nullable=False),  # Encoded state
        sa.Column('context_features', sa.JSON(), nullable=True),  # Order/PO context

        # Decision outputs
        sa.Column('engine_decision', sa.JSON(), nullable=False),  # Deterministic baseline
        sa.Column('trm_adjustment', sa.JSON(), nullable=True),  # TRM suggestion
        sa.Column('final_decision', sa.JSON(), nullable=False),  # What was actually applied
        sa.Column('trm_confidence', sa.Float(), nullable=True),

        # Source tracking
        sa.Column('decision_source', sa.String(20), nullable=False),  # "deterministic", "trm_adjusted"
        sa.Column('checkpoint_id', sa.BigInteger(), nullable=True),  # Which model made the decision

        # Human override (if any)
        sa.Column('human_override', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('override_by', sa.Integer(), nullable=True),
        sa.Column('override_reason', sa.String(500), nullable=True),

        # Outcomes (populated later for RL training)
        sa.Column('outcome_recorded', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('outcome_timestamp', sa.DateTime(), nullable=True),
        sa.Column('outcome_service_level', sa.Float(), nullable=True),
        sa.Column('outcome_cost', sa.Float(), nullable=True),
        sa.Column('outcome_data', sa.JSON(), nullable=True),  # Additional outcome metrics

        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['checkpoint_id'], ['powell_site_agent_checkpoints.id'],
                               ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['override_by'], ['users.id'], ondelete='SET NULL'),
    )

    op.create_index(
        'ix_powell_site_agent_decisions_site_type_time',
        'powell_site_agent_decisions',
        ['site_key', 'decision_type', 'decision_timestamp']
    )
    op.create_index(
        'ix_powell_site_agent_decisions_training',
        'powell_site_agent_decisions',
        ['site_key', 'outcome_recorded', 'human_override']
    )


def downgrade():
    op.drop_table('powell_site_agent_decisions')
    op.drop_table('powell_site_agent_checkpoints')
    op.drop_table('powell_cdc_thresholds')
    op.drop_table('powell_cdc_trigger_log')
