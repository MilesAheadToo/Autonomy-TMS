"""Email signals and email connections tables

Revision ID: 20260311_email_signals
Revises: (latest existing migration)
Create Date: 2026-03-11

Creates:
  - email_connections: IMAP/Gmail inbox configurations per tenant
  - email_signals: GDPR-safe supply chain signals extracted from emails
"""

from alembic import op
import sqlalchemy as sa


revision = '20260311_email_signals'
down_revision = None  # Will be resolved by Alembic
branch_labels = None
depends_on = None


def upgrade():
    # ── email_connections ─────────────────────────────────────────────────────
    op.create_table(
        'email_connections',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('connection_type', sa.String(20), nullable=False),
        # IMAP config
        sa.Column('imap_host', sa.String(255), nullable=True),
        sa.Column('imap_port', sa.Integer(), nullable=True),
        sa.Column('imap_username', sa.String(255), nullable=True),
        sa.Column('imap_password_encrypted', sa.Text(), nullable=True),
        sa.Column('imap_folder', sa.String(100), server_default='INBOX'),
        sa.Column('imap_use_ssl', sa.Boolean(), server_default='true'),
        # Gmail config
        sa.Column('gmail_label_filter', sa.String(255), nullable=True),
        # Domain filters
        sa.Column('domain_allowlist', sa.JSON(), nullable=True),
        sa.Column('domain_blocklist', sa.JSON(), nullable=True),
        # Polling state
        sa.Column('poll_interval_minutes', sa.Integer(), server_default='5'),
        sa.Column('last_poll_at', sa.DateTime(), nullable=True),
        sa.Column('last_poll_uid', sa.String(255), nullable=True),
        # Auto-routing
        sa.Column('auto_route_enabled', sa.Boolean(), server_default='true'),
        sa.Column('min_confidence_to_route', sa.Float(), server_default='0.6'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('idx_email_conn_tenant', 'email_connections', ['tenant_id'])

    # ── email_signals ─────────────────────────────────────────────────────────
    op.create_table(
        'email_signals',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('connection_id', sa.Integer(), sa.ForeignKey('email_connections.id'), nullable=True),
        # Email metadata (GDPR-safe)
        sa.Column('email_uid', sa.String(255), nullable=False),
        sa.Column('received_at', sa.DateTime(), nullable=False),
        sa.Column('ingested_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('subject_scrubbed', sa.Text(), nullable=True),
        sa.Column('body_scrubbed', sa.Text(), nullable=False),
        # Sender resolution (company only, GDPR)
        sa.Column('sender_domain', sa.String(255), nullable=False),
        sa.Column('resolved_partner_id', sa.String(100), nullable=True),
        sa.Column('partner_type', sa.String(50), nullable=True),
        sa.Column('partner_name', sa.String(255), nullable=True),
        # Signal classification
        sa.Column('signal_type', sa.String(50), nullable=False),
        sa.Column('signal_direction', sa.String(20), nullable=True),
        sa.Column('signal_magnitude_pct', sa.Float(), nullable=True),
        sa.Column('signal_confidence', sa.Float(), nullable=False),
        sa.Column('signal_urgency', sa.Float(), nullable=False, server_default='0.5'),
        sa.Column('signal_summary', sa.Text(), nullable=False),
        # Scope
        sa.Column('resolved_product_ids', sa.JSON(), nullable=True),
        sa.Column('resolved_site_ids', sa.JSON(), nullable=True),
        sa.Column('time_horizon_weeks', sa.Integer(), nullable=True),
        # Routing
        sa.Column('target_trm_types', sa.JSON(), nullable=True),
        sa.Column('routed_decision_ids', sa.JSON(), nullable=True),
        # Lifecycle
        sa.Column('status', sa.String(20), nullable=False, server_default='INGESTED'),
        sa.Column('classified_at', sa.DateTime(), nullable=True),
        sa.Column('routed_at', sa.DateTime(), nullable=True),
        sa.Column('acted_at', sa.DateTime(), nullable=True),
        sa.Column('dismissed_by', sa.Integer(), nullable=True),
        sa.Column('dismiss_reason', sa.String(255), nullable=True),
    )

    op.create_index('idx_email_signal_tenant_status', 'email_signals', ['tenant_id', 'status'])
    op.create_index('idx_email_signal_config', 'email_signals', ['config_id'])
    op.create_index('idx_email_signal_partner', 'email_signals', ['resolved_partner_id'])
    op.create_index('idx_email_signal_type', 'email_signals', ['signal_type'])
    op.create_index('idx_email_signal_received', 'email_signals', ['received_at'])
    op.create_unique_constraint('uq_email_signal_uid', 'email_signals', ['tenant_id', 'email_uid'])


def downgrade():
    op.drop_table('email_signals')
    op.drop_index('idx_email_conn_tenant', 'email_connections')
    op.drop_table('email_connections')
