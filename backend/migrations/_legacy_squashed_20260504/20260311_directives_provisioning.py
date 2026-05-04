"""User Directives and Config Provisioning Status tables

Revision ID: 20260311_directives_provisioning
Revises: (latest existing migration)
Create Date: 2026-03-11

Creates:
  - user_directives: Natural language directive capture, LLM parsing, Powell routing
  - config_provisioning_status: 10-step provisioning pipeline with dependency tracking
"""

from alembic import op
import sqlalchemy as sa


revision = '20260311_directives_provisioning'
down_revision = None  # Will be resolved by Alembic
branch_labels = None
depends_on = None


def upgrade():
    # ── user_directives ──────────────────────────────────────────────────────
    op.create_table(
        'user_directives',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('raw_text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('directive_type', sa.String(50), nullable=False),
        sa.Column('reason_code', sa.String(100), nullable=False),
        sa.Column('parsed_intent', sa.String(30), nullable=False),
        sa.Column('parsed_scope', sa.JSON(), nullable=False),
        sa.Column('parsed_direction', sa.String(20), nullable=True),
        sa.Column('parsed_metric', sa.String(50), nullable=True),
        sa.Column('parsed_magnitude_pct', sa.Float(), nullable=True),
        sa.Column('parser_confidence', sa.Float(), nullable=False),
        sa.Column('target_layer', sa.String(20), nullable=False),
        sa.Column('target_trm_types', sa.JSON(), nullable=True),
        sa.Column('target_site_keys', sa.JSON(), nullable=True),
        sa.Column('routed_actions', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='PARSED'),
        sa.Column('applied_at', sa.DateTime(), nullable=True),
        sa.Column('measured_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('effectiveness_delta', sa.Float(), nullable=True),
        sa.Column('effectiveness_scope', sa.String(20), nullable=True),
    )

    op.create_index('idx_directive_user', 'user_directives', ['user_id'])
    op.create_index('idx_directive_config', 'user_directives', ['config_id'])
    op.create_index('idx_directive_tenant_status', 'user_directives', ['tenant_id', 'status'])
    op.create_index('idx_directive_created', 'user_directives', ['created_at'])

    # ── config_provisioning_status ───────────────────────────────────────────
    op.create_table(
        'config_provisioning_status',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=False, unique=True),
        # Step 1: Warm start
        sa.Column('warm_start_status', sa.String(20), server_default='pending'),
        sa.Column('warm_start_at', sa.DateTime(), nullable=True),
        sa.Column('warm_start_error', sa.Text(), nullable=True),
        # Step 2: S&OP GraphSAGE
        sa.Column('sop_graphsage_status', sa.String(20), server_default='pending'),
        sa.Column('sop_graphsage_at', sa.DateTime(), nullable=True),
        sa.Column('sop_graphsage_error', sa.Text(), nullable=True),
        # Step 3: CFA optimization
        sa.Column('cfa_optimization_status', sa.String(20), server_default='pending'),
        sa.Column('cfa_optimization_at', sa.DateTime(), nullable=True),
        sa.Column('cfa_optimization_error', sa.Text(), nullable=True),
        # Step 4: Execution tGNN
        sa.Column('execution_tgnn_status', sa.String(20), server_default='pending'),
        sa.Column('execution_tgnn_at', sa.DateTime(), nullable=True),
        sa.Column('execution_tgnn_error', sa.Text(), nullable=True),
        # Step 5: TRM training
        sa.Column('trm_training_status', sa.String(20), server_default='pending'),
        sa.Column('trm_training_at', sa.DateTime(), nullable=True),
        sa.Column('trm_training_error', sa.Text(), nullable=True),
        # Step 6: Supply plan
        sa.Column('supply_plan_status', sa.String(20), server_default='pending'),
        sa.Column('supply_plan_at', sa.DateTime(), nullable=True),
        sa.Column('supply_plan_error', sa.Text(), nullable=True),
        # Step 7: Decision seed
        sa.Column('decision_seed_status', sa.String(20), server_default='pending'),
        sa.Column('decision_seed_at', sa.DateTime(), nullable=True),
        sa.Column('decision_seed_error', sa.Text(), nullable=True),
        # Step 8: Site tGNN
        sa.Column('site_tgnn_status', sa.String(20), server_default='pending'),
        sa.Column('site_tgnn_at', sa.DateTime(), nullable=True),
        sa.Column('site_tgnn_error', sa.Text(), nullable=True),
        # Step 9: Conformal
        sa.Column('conformal_status', sa.String(20), server_default='pending'),
        sa.Column('conformal_at', sa.DateTime(), nullable=True),
        sa.Column('conformal_error', sa.Text(), nullable=True),
        # Step 10: Briefing
        sa.Column('briefing_status', sa.String(20), server_default='pending'),
        sa.Column('briefing_at', sa.DateTime(), nullable=True),
        sa.Column('briefing_error', sa.Text(), nullable=True),
        # Overall
        sa.Column('overall_status', sa.String(20), server_default='not_started'),
        sa.Column('last_updated', sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table('config_provisioning_status')
    op.drop_index('idx_directive_created', 'user_directives')
    op.drop_index('idx_directive_tenant_status', 'user_directives')
    op.drop_index('idx_directive_config', 'user_directives')
    op.drop_index('idx_directive_user', 'user_directives')
    op.drop_table('user_directives')
