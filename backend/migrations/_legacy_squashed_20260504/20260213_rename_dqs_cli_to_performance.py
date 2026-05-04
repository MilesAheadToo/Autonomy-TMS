"""Rename DQS/CLI to Agent Performance / Human Overrides

Revision ID: 20260213_rename_dqs_cli
Revises: 20260211_merge_inc
Create Date: 2026-02-13

Renames:
- Table: dqs_metrics → performance_metrics
- Columns: agent_dqs → agent_score, planner_dqs → planner_score,
           cli_percentage → override_rate
- In agent_decision_metrics: agent_dqs → agent_score,
           user_dqs → user_score, cognitive_load_index → human_override_rate
"""
from alembic import op
import sqlalchemy as sa

revision = '20260213_rename_dqs_cli'
down_revision = '20260211_merge_inc'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # =========================================================================
    # Rename dqs_metrics → performance_metrics
    # =========================================================================
    if 'dqs_metrics' in existing_tables and 'performance_metrics' not in existing_tables:
        op.rename_table('dqs_metrics', 'performance_metrics')

        # Rename columns
        op.alter_column('performance_metrics', 'agent_dqs',
                         new_column_name='agent_score', existing_type=sa.Float)
        op.alter_column('performance_metrics', 'planner_dqs',
                         new_column_name='planner_score', existing_type=sa.Float)
        op.alter_column('performance_metrics', 'cli_percentage',
                         new_column_name='override_rate', existing_type=sa.Float)

        # Rename index
        try:
            op.drop_index('ix_dqs_metrics_group_period', table_name='performance_metrics')
        except Exception:
            pass
        op.create_index('ix_performance_metrics_group_period', 'performance_metrics',
                        ['customer_id', 'period_start', 'period_type'])

    # =========================================================================
    # Rename columns in agent_decision_metrics
    # =========================================================================
    if 'agent_decision_metrics' in existing_tables:
        existing_cols = [c['name'] for c in inspector.get_columns('agent_decision_metrics')]

        if 'agent_dqs' in existing_cols:
            op.alter_column('agent_decision_metrics', 'agent_dqs',
                             new_column_name='agent_score', existing_type=sa.Float)
        if 'user_dqs' in existing_cols:
            op.alter_column('agent_decision_metrics', 'user_dqs',
                             new_column_name='user_score', existing_type=sa.Float)
        if 'cognitive_load_index' in existing_cols:
            op.alter_column('agent_decision_metrics', 'cognitive_load_index',
                             new_column_name='human_override_rate', existing_type=sa.Float)


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'performance_metrics' in existing_tables:
        op.alter_column('performance_metrics', 'agent_score',
                         new_column_name='agent_dqs', existing_type=sa.Float)
        op.alter_column('performance_metrics', 'planner_score',
                         new_column_name='planner_dqs', existing_type=sa.Float)
        op.alter_column('performance_metrics', 'override_rate',
                         new_column_name='cli_percentage', existing_type=sa.Float)
        op.rename_table('performance_metrics', 'dqs_metrics')

        try:
            op.drop_index('ix_performance_metrics_group_period', table_name='dqs_metrics')
        except Exception:
            pass
        op.create_index('ix_dqs_metrics_group_period', 'dqs_metrics',
                        ['customer_id', 'period_start', 'period_type'])

    if 'agent_decision_metrics' in existing_tables:
        existing_cols = [c['name'] for c in inspector.get_columns('agent_decision_metrics')]

        if 'agent_score' in existing_cols:
            op.alter_column('agent_decision_metrics', 'agent_score',
                             new_column_name='agent_dqs', existing_type=sa.Float)
        if 'user_score' in existing_cols:
            op.alter_column('agent_decision_metrics', 'user_score',
                             new_column_name='user_dqs', existing_type=sa.Float)
        if 'human_override_rate' in existing_cols:
            op.alter_column('agent_decision_metrics', 'human_override_rate',
                             new_column_name='cognitive_load_index', existing_type=sa.Float)
