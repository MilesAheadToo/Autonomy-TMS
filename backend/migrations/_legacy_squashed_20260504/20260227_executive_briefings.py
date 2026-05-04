"""Create executive_briefings, briefing_followups, and briefing_schedules tables

Revision ID: 20260227_exec_brief
Revises: 20260226_decmem
Create Date: 2026-02-27

LLM-synthesized executive strategy briefings with follow-up Q&A and
per-tenant scheduling. Stores raw data packs, LLM narratives,
scored recommendations, and generation metadata.
"""
from alembic import op
import sqlalchemy as sa

revision = '20260227_exec_brief'
down_revision = '20260226_decmem'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Executive briefings — primary table
    op.create_table(
        'executive_briefings',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('requested_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('briefing_type', sa.String(20), nullable=False, server_default='adhoc'),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('title', sa.String(500), nullable=True),
        sa.Column('data_pack', sa.JSON(), nullable=True),
        sa.Column('narrative', sa.Text(), nullable=True),
        sa.Column('recommendations', sa.JSON(), nullable=True),
        sa.Column('executive_summary', sa.Text(), nullable=True),
        sa.Column('model_used', sa.String(100), nullable=True),
        sa.Column('tokens_used', sa.Integer(), nullable=True),
        sa.Column('generation_time_ms', sa.Integer(), nullable=True),
        sa.Column('kb_context_used', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_exec_briefing_tenant_created', 'executive_briefings', ['tenant_id', 'created_at'])
    op.create_index('ix_exec_briefing_status', 'executive_briefings', ['status'])
    op.create_index('ix_exec_briefing_type', 'executive_briefings', ['briefing_type'])

    # 2. Briefing followups — Q&A pairs
    op.create_table(
        'briefing_followups',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('briefing_id', sa.Integer(), sa.ForeignKey('executive_briefings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('asked_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=True),
        sa.Column('model_used', sa.String(100), nullable=True),
        sa.Column('tokens_used', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_briefing_followup_briefing_id', 'briefing_followups', ['briefing_id'])

    # 3. Briefing schedules — per-tenant config
    op.create_table(
        'briefing_schedules',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('briefing_type', sa.String(20), nullable=False, server_default='weekly'),
        sa.Column('cron_day_of_week', sa.String(10), nullable=False, server_default='mon'),
        sa.Column('cron_hour', sa.Integer(), nullable=False, server_default=sa.text('6')),
        sa.Column('cron_minute', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('briefing_schedules')
    op.drop_table('briefing_followups')
    op.drop_table('executive_briefings')
