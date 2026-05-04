"""Decision Tracking Tables for DQS/CLI Metrics

Revision ID: 20260208_decision_tracking
Revises: 20260208_powell_role
Create Date: 2026-02-08

Creates tables for tracking agent decisions and calculating DQS/CLI metrics
for the Powell Framework demonstration dashboards.

Tables:
- agent_decisions: Individual agent decision records
- dqs_metrics: Aggregated DQS/CLI metrics by period
- sop_worklist_items: S&OP worklist items for tactical review
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260208_decision_tracking'
down_revision = '20260208_powell_role'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    dialect_name = conn.dialect.name
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # =========================================================================
    # Create agent_decisions Table
    # =========================================================================
    if 'agent_decisions' not in existing_tables:
        op.create_table(
            'agent_decisions',
            sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),

            # Context
            sa.Column('customer_id', sa.Integer, sa.ForeignKey('groups.id', ondelete='CASCADE'), nullable=False, index=True),
            sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True),

            # Decision identification - using String for simplicity
            sa.Column('decision_type', sa.String(50), nullable=False, index=True),
            sa.Column('item_code', sa.String(50), nullable=False, index=True),
            sa.Column('item_name', sa.String(200), nullable=False),
            sa.Column('category', sa.String(100), nullable=True, index=True),

            # The issue/recommendation
            sa.Column('issue_summary', sa.Text, nullable=False),
            sa.Column('impact_value', sa.Float, nullable=True),
            sa.Column('impact_description', sa.String(255), nullable=True),

            # Agent's recommendation
            sa.Column('agent_recommendation', sa.Text, nullable=False),
            sa.Column('agent_reasoning', sa.Text, nullable=True),
            sa.Column('agent_confidence', sa.Float, nullable=True),

            # Numeric recommendation
            sa.Column('recommended_value', sa.Float, nullable=True),
            sa.Column('previous_value', sa.Float, nullable=True),

            # Status and urgency - using String for simplicity
            sa.Column('status', sa.String(20), nullable=False, default='pending', index=True),
            sa.Column('urgency', sa.String(20), nullable=False, default='standard'),
            sa.Column('due_date', sa.DateTime, nullable=True),

            # User response
            sa.Column('user_action', sa.String(50), nullable=True),
            sa.Column('user_value', sa.Float, nullable=True),
            sa.Column('override_reason', sa.Text, nullable=True),
            sa.Column('action_timestamp', sa.DateTime, nullable=True),

            # Outcome tracking
            sa.Column('outcome_measured', sa.Boolean, default=False),
            sa.Column('outcome_value', sa.Float, nullable=True),
            sa.Column('outcome_quality_score', sa.Float, nullable=True),
            sa.Column('outcome_notes', sa.Text, nullable=True),

            # Agent metadata
            sa.Column('agent_type', sa.String(50), nullable=False, default='trm'),
            sa.Column('agent_version', sa.String(20), nullable=True),

            # Timestamps
            sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('NOW()'), index=True),
            sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.text('NOW()')),

            # Planning cycle reference
            sa.Column('planning_cycle', sa.String(20), nullable=True, index=True),

            # Additional context
            sa.Column('context_data',
                postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
                nullable=True),
        )

        # Create compound indexes
        op.create_index('ix_agent_decisions_group_status', 'agent_decisions', ['customer_id', 'status'])
        op.create_index('ix_agent_decisions_group_type_cycle', 'agent_decisions', ['customer_id', 'decision_type', 'planning_cycle'])
        op.create_index('ix_agent_decisions_user_status', 'agent_decisions', ['user_id', 'status'])

    # =========================================================================
    # Create dqs_metrics Table
    # =========================================================================
    if 'dqs_metrics' not in existing_tables:
        op.create_table(
            'dqs_metrics',
            sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),

            sa.Column('customer_id', sa.Integer, sa.ForeignKey('groups.id', ondelete='CASCADE'), nullable=False, index=True),

            # Time period
            sa.Column('period_start', sa.DateTime, nullable=False, index=True),
            sa.Column('period_end', sa.DateTime, nullable=False),
            sa.Column('period_type', sa.String(20), nullable=False),

            # Optional category breakdown
            sa.Column('category', sa.String(100), nullable=True, index=True),
            sa.Column('decision_type', sa.String(50), nullable=True, index=True),

            # Decision counts
            sa.Column('total_decisions', sa.Integer, default=0),
            sa.Column('agent_decisions', sa.Integer, default=0),
            sa.Column('planner_decisions', sa.Integer, default=0),

            # DQS scores
            sa.Column('agent_dqs', sa.Float, nullable=True),
            sa.Column('planner_dqs', sa.Float, nullable=True),

            # CLI
            sa.Column('cli_percentage', sa.Float, nullable=True),
            sa.Column('override_count', sa.Integer, default=0),

            # Automation metrics
            sa.Column('automation_percentage', sa.Float, nullable=True),

            # Active resources
            sa.Column('active_agents', sa.Integer, default=0),
            sa.Column('active_planners', sa.Integer, default=0),

            # SKU metrics
            sa.Column('total_skus', sa.Integer, default=0),
            sa.Column('skus_per_planner', sa.Float, nullable=True),

            sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('NOW()')),
        )

        op.create_index('ix_dqs_metrics_group_period', 'dqs_metrics', ['customer_id', 'period_start', 'period_type'])

    # =========================================================================
    # Create sop_worklist_items Table
    # =========================================================================
    if 'sop_worklist_items' not in existing_tables:
        op.create_table(
            'sop_worklist_items',
            sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),

            sa.Column('customer_id', sa.Integer, sa.ForeignKey('groups.id', ondelete='CASCADE'), nullable=False, index=True),

            # Item identification
            sa.Column('item_code', sa.String(50), nullable=False, index=True),
            sa.Column('item_name', sa.String(200), nullable=False),
            sa.Column('category', sa.String(100), nullable=False, index=True),

            # Issue details
            sa.Column('issue_type', sa.String(50), nullable=False),
            sa.Column('issue_summary', sa.Text, nullable=False),

            # Impact
            sa.Column('impact_value', sa.Float, nullable=True),
            sa.Column('impact_description', sa.String(255), nullable=False),
            sa.Column('impact_type', sa.String(20), default='negative'),

            # Timeline - using String for urgency
            sa.Column('due_description', sa.String(50), nullable=False),
            sa.Column('due_date', sa.DateTime, nullable=True),
            sa.Column('urgency', sa.String(20), nullable=False, default='standard'),

            # AI recommendation
            sa.Column('agent_recommendation', sa.Text, nullable=True),
            sa.Column('agent_reasoning', sa.Text, nullable=True),

            # Status - using String
            sa.Column('status', sa.String(20), nullable=False, default='pending'),

            # User response
            sa.Column('resolved_by', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('resolution_action', sa.String(50), nullable=True),
            sa.Column('resolution_notes', sa.Text, nullable=True),
            sa.Column('resolved_at', sa.DateTime, nullable=True),

            # Timestamps
            sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('NOW()'), index=True),
            sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.text('NOW()')),
        )

        op.create_index('ix_sop_worklist_group_status', 'sop_worklist_items', ['customer_id', 'status'])


def downgrade():
    op.drop_table('sop_worklist_items')
    op.drop_table('dqs_metrics')
    op.drop_table('agent_decisions')
