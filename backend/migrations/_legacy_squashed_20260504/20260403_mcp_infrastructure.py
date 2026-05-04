"""MCP infrastructure tables

Revision ID: 20260403_mcp_infra
Revises: 20260402_schema_profile
Create Date: 2026-04-03

Adds:
- mcp_server_config: Per-tenant MCP server connection configuration
- audit.mcp_call_log: SOC II audit trail for all MCP tool calls
- mcp_delta_state: Hash-based CDC state tracking for MCP polls
- mcp_pending_writeback: Pending write-backs for INSPECT mode approval
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


revision: str = '20260403_mcp_infra'
down_revision: Union[str, None] = '20260402_schema_profile'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # MCP Server Configuration (per tenant + ERP)
    op.create_table(
        'mcp_server_config',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('erp_type', sa.String(20), nullable=False),
        sa.Column('transport', sa.String(30), nullable=False, server_default='sse'),
        sa.Column('server_command', JSON, nullable=True),
        sa.Column('server_url', sa.String(500), nullable=True),
        sa.Column('auth_config_encrypted', sa.Text(), nullable=True),
        sa.Column('server_env', JSON, nullable=True),
        sa.Column('tool_mappings', JSON, nullable=True),
        sa.Column('poll_interval_seconds', sa.Integer(), nullable=False, server_default='300'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_validated', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('last_validated_at', sa.DateTime(), nullable=True),
        sa.Column('last_poll_at', sa.DateTime(), nullable=True),
        sa.Column('validation_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.UniqueConstraint('tenant_id', 'erp_type', name='uq_mcp_config_tenant_erp'),
    )

    # Audit log for MCP calls (SOC II)
    op.execute("CREATE SCHEMA IF NOT EXISTS audit")
    op.create_table(
        'mcp_call_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), nullable=False, index=True),
        sa.Column('config_id', sa.Integer(), nullable=True),
        sa.Column('erp_type', sa.String(20), nullable=False),
        sa.Column('direction', sa.String(10), nullable=False),
        sa.Column('tool_name', sa.String(200), nullable=False),
        sa.Column('arguments_hash', sa.String(64), nullable=True),
        sa.Column('arguments_summary', sa.Text(), nullable=True),
        sa.Column('result_summary', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('duration_ms', sa.Float(), nullable=True),
        sa.Column('correlation_id', sa.String(36), nullable=True, index=True),
        sa.Column('decision_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        schema='audit',
    )

    # Delta state for MCP CDC polling
    op.create_table(
        'mcp_delta_state',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('entity_type', sa.String(50), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('record_key', sa.String(200), nullable=False),
        sa.Column('record_hash', sa.String(16), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.UniqueConstraint('entity_type', 'config_id', 'record_key', name='uq_mcp_delta_state'),
    )
    op.create_index('ix_mcp_delta_entity_config', 'mcp_delta_state', ['entity_type', 'config_id'])

    # Pending write-backs for INSPECT mode
    op.create_table(
        'mcp_pending_writeback',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('decision_id', sa.Integer(), nullable=False),
        sa.Column('decision_type', sa.String(50), nullable=False),
        sa.Column('tool_name', sa.String(200), nullable=False),
        sa.Column('erp_payload', sa.Text(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False, index=True),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('correlation_id', sa.String(36), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('approved_by', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('executed_at', sa.DateTime(), nullable=True),
        sa.Column('execution_result', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
    )

    # RLS policy for mcp_server_config
    op.execute("""
        ALTER TABLE mcp_server_config ENABLE ROW LEVEL SECURITY;
        CREATE POLICY mcp_config_tenant_isolation ON mcp_server_config
            USING (tenant_id = current_setting('app.current_tenant_id', true)::int);
    """)

    # RLS policy for mcp_pending_writeback
    op.execute("""
        ALTER TABLE mcp_pending_writeback ENABLE ROW LEVEL SECURITY;
        CREATE POLICY mcp_writeback_tenant_isolation ON mcp_pending_writeback
            USING (tenant_id = current_setting('app.current_tenant_id', true)::int);
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS mcp_writeback_tenant_isolation ON mcp_pending_writeback")
    op.execute("DROP POLICY IF EXISTS mcp_config_tenant_isolation ON mcp_server_config")
    op.drop_table('mcp_pending_writeback')
    op.drop_index('ix_mcp_delta_entity_config', 'mcp_delta_state')
    op.drop_table('mcp_delta_state')
    op.drop_table('mcp_call_log', schema='audit')
    op.drop_table('mcp_server_config')
