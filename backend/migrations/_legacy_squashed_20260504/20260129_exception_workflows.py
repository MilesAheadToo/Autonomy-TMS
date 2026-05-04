"""Exception Workflow Tables and ForecastException Workflow Fields

Adds:
- exception_workflow_template table
- exception_escalation_log table
- Workflow tracking fields to forecast_exception table

Revision ID: 20260129_exception_workflows
Revises: 8145adf51ea2
Create Date: 2026-01-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20260129_exception_workflows'
down_revision = '8145adf51ea2'
branch_labels = None
depends_on = None


def upgrade():
    # Create exception_workflow_template table
    op.create_table(
        'exception_workflow_template',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('code', sa.String(50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('config_id', sa.Integer(), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('exception_types', sa.JSON(), nullable=True),
        sa.Column('severity_levels', sa.JSON(), nullable=True),
        sa.Column('product_categories', sa.JSON(), nullable=True),
        sa.Column('min_impact_value', sa.Double(), nullable=True),
        sa.Column('initial_assignment', sa.JSON(), nullable=True),
        sa.Column('escalation_levels', sa.JSON(), nullable=True),
        sa.Column('auto_resolve_config', sa.JSON(), nullable=True),
        sa.Column('notification_channels', sa.JSON(), nullable=True),
        sa.Column('sla_hours', sa.Integer(), nullable=True),
        sa.Column('sla_warning_hours', sa.Integer(), nullable=True),
        sa.Column('priority', sa.Integer(), default=100),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('is_default', sa.Boolean(), default=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id']),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
    )
    op.create_index('idx_ewt_config', 'exception_workflow_template', ['config_id'])
    op.create_index('idx_ewt_group', 'exception_workflow_template', ['customer_id'])
    op.create_index('idx_ewt_active', 'exception_workflow_template', ['is_active', 'priority'])

    # Create exception_escalation_log table
    # Note: FK to forecast_exception deferred - will be added when forecast_exception exists
    op.create_table(
        'exception_escalation_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('exception_id', sa.Integer(), nullable=False),  # FK to forecast_exception - deferred
        sa.Column('escalation_level', sa.Integer(), nullable=False),
        sa.Column('escalated_from_id', sa.Integer(), nullable=True),
        sa.Column('escalated_to_id', sa.Integer(), nullable=False),
        sa.Column('escalation_reason', sa.String(200), nullable=False),
        sa.Column('triggered_by', sa.String(50), nullable=False),
        sa.Column('trigger_user_id', sa.Integer(), nullable=True),
        sa.Column('workflow_template_id', sa.Integer(), nullable=True),
        sa.Column('notifications_sent', sa.JSON(), nullable=True),
        sa.Column('escalated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        # FK to forecast_exception deferred - table doesn't exist yet
        sa.ForeignKeyConstraint(['escalated_from_id'], ['users.id']),
        sa.ForeignKeyConstraint(['escalated_to_id'], ['users.id']),
        sa.ForeignKeyConstraint(['trigger_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['workflow_template_id'], ['exception_workflow_template.id']),
    )
    op.create_index('idx_eel_exception', 'exception_escalation_log', ['exception_id'])
    op.create_index('idx_eel_escalated_to', 'exception_escalation_log', ['escalated_to_id'])
    op.create_index('idx_eel_escalated_at', 'exception_escalation_log', ['escalated_at'])

    # NOTE: forecast_exception table workflow fields will be added when that table is created
    # This migration only creates the workflow infrastructure tables
    pass


def downgrade():
    # Drop indexes
    try:
        op.drop_index('idx_fe_sla', table_name='forecast_exception')
    except Exception:
        pass

    try:
        op.drop_index('idx_fe_workflow', table_name='forecast_exception')
    except Exception:
        pass

    # Drop columns from forecast_exception
    try:
        op.drop_column('forecast_exception', 'deferred_until')
    except Exception:
        pass

    try:
        op.drop_column('forecast_exception', 'sla_deadline')
    except Exception:
        pass

    try:
        op.drop_column('forecast_exception', 'last_escalated_at')
    except Exception:
        pass

    try:
        op.drop_column('forecast_exception', 'current_escalation_level')
    except Exception:
        pass

    try:
        op.drop_column('forecast_exception', 'workflow_template_id')
    except Exception:
        pass

    try:
        op.drop_column('forecast_exception', 'assigned_to_role')
    except Exception:
        pass

    # Drop tables
    op.drop_table('exception_escalation_log')
    op.drop_table('exception_workflow_template')
