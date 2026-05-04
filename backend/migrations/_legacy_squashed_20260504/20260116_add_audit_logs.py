"""Add audit logging tables

Revision ID: 20260116_audit
Revises: 20260116_rbac
Create Date: 2026-01-16 08:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20260116_audit'
down_revision = '20260116_rbac'
branch_labels = None
depends_on = None


def upgrade():
    # Create audit_logs table
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('username', sa.String(100), nullable=True),
        sa.Column('user_email', sa.String(255), nullable=True),
        sa.Column('action', sa.Enum(
            'LOGIN', 'LOGOUT', 'LOGIN_FAILED', 'PASSWORD_CHANGE', 'PASSWORD_RESET',
            'MFA_ENABLE', 'MFA_DISABLE', 'CREATE', 'READ', 'UPDATE', 'DELETE',
            'ROLE_ASSIGN', 'ROLE_REVOKE', 'PERMISSION_GRANT', 'PERMISSION_REVOKE',
            'USER_CREATE', 'USER_UPDATE', 'USER_DELETE', 'USER_ACTIVATE', 'USER_DEACTIVATE',
            'TENANT_CREATE', 'TENANT_UPDATE', 'TENANT_DELETE',
            'SSO_LOGIN', 'SSO_PROVIDER_CREATE', 'SSO_PROVIDER_UPDATE', 'SSO_PROVIDER_DELETE',
            'GAME_CREATE', 'GAME_START', 'GAME_COMPLETE', 'GAME_DELETE',
            'CONFIG_CREATE', 'CONFIG_UPDATE', 'CONFIG_DELETE',
            'DATA_EXPORT', 'DATA_IMPORT',
            name='auditaction'
        ), nullable=False),
        sa.Column('resource_type', sa.String(50), nullable=True),
        sa.Column('resource_id', sa.Integer(), nullable=True),
        sa.Column('resource_name', sa.String(255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('old_value', sa.JSON(), nullable=True),
        sa.Column('new_value', sa.JSON(), nullable=True),
        sa.Column('changes', sa.JSON(), nullable=True),
        sa.Column('status', sa.Enum('SUCCESS', 'FAILURE', 'PARTIAL', 'ERROR', name='auditstatus'),
                  nullable=False, server_default='SUCCESS'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('session_id', sa.String(255), nullable=True),
        sa.Column('correlation_id', sa.String(255), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('extra_data', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE')
    )

    # Create indexes
    op.create_index('ix_audit_logs_id', 'audit_logs', ['id'])
    op.create_index('ix_audit_logs_user_id', 'audit_logs', ['user_id'])
    op.create_index('ix_audit_logs_action', 'audit_logs', ['action'])
    op.create_index('ix_audit_logs_resource_type', 'audit_logs', ['resource_type'])
    op.create_index('ix_audit_logs_status', 'audit_logs', ['status'])
    op.create_index('ix_audit_logs_tenant_id', 'audit_logs', ['tenant_id'])
    op.create_index('ix_audit_logs_session_id', 'audit_logs', ['session_id'])
    op.create_index('ix_audit_logs_correlation_id', 'audit_logs', ['correlation_id'])
    op.create_index('ix_audit_logs_created_at', 'audit_logs', ['created_at'])

    # Composite indexes for common queries
    op.create_index('idx_audit_tenant_created', 'audit_logs', ['tenant_id', 'created_at'])
    op.create_index('idx_audit_user_created', 'audit_logs', ['user_id', 'created_at'])
    op.create_index('idx_audit_resource', 'audit_logs', ['resource_type', 'resource_id'])
    op.create_index('idx_audit_action_created', 'audit_logs', ['action', 'created_at'])
    op.create_index('idx_audit_status_created', 'audit_logs', ['status', 'created_at'])

    # Create audit_log_summaries table
    op.create_table(
        'audit_log_summaries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('period_start', sa.DateTime(), nullable=False),
        sa.Column('period_end', sa.DateTime(), nullable=False),
        sa.Column('total_actions', sa.Integer(), server_default=sa.text("0")),
        sa.Column('success_count', sa.Integer(), server_default=sa.text("0")),
        sa.Column('failure_count', sa.Integer(), server_default=sa.text("0")),
        sa.Column('error_count', sa.Integer(), server_default=sa.text("0")),
        sa.Column('action_counts', sa.JSON(), nullable=True),
        sa.Column('resource_counts', sa.JSON(), nullable=True),
        sa.Column('most_active_users', sa.JSON(), nullable=True),
        sa.Column('most_accessed_resources', sa.JSON(), nullable=True),
        sa.Column('peak_hour', sa.Integer(), nullable=True),
        sa.Column('peak_day', sa.Integer(), nullable=True),
        sa.Column('generated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE')
    )

    op.create_index('ix_audit_log_summaries_id', 'audit_log_summaries', ['id'])
    op.create_index('ix_audit_log_summaries_tenant_id', 'audit_log_summaries', ['tenant_id'])
    op.create_index('ix_audit_log_summaries_user_id', 'audit_log_summaries', ['user_id'])
    op.create_index('ix_audit_log_summaries_period_start', 'audit_log_summaries', ['period_start'])
    op.create_index('ix_audit_log_summaries_period_end', 'audit_log_summaries', ['period_end'])


def downgrade():
    op.drop_table('audit_log_summaries')
    op.drop_table('audit_logs')
