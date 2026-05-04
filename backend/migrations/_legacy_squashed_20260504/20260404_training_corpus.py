"""Unified training corpus table

Revision ID: 20260404_training_corpus
Revises: 20260404_correlation_id
Create Date: 2026-04-04

Creates the training_corpus table that holds training data for ALL 4 planning
layers (Strategic, Tactical, Operational, Execution). Replaces the previous
architecture of independent synthetic data pipelines per layer.

See docs/internal/architecture/UNIFIED_TRAINING_CORPUS.md
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '20260404_training_corpus'
down_revision: Union[str, None] = '20260404_correlation_id'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'training_corpus',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), nullable=False, index=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('layer', sa.Numeric(3, 1), nullable=False),
        sa.Column('scenario_id', sa.String(64), nullable=True),
        sa.Column('origin', sa.String(20), nullable=False, server_default='perturbation'),
        sa.Column('trm_type', sa.String(50), nullable=True),
        sa.Column('product_id', sa.String(100), nullable=True),
        sa.Column('site_id', sa.String(100), nullable=True),
        sa.Column('period', sa.String(20), nullable=True),
        sa.Column('window', sa.String(20), nullable=True),
        sa.Column('sample_data', JSONB, nullable=False),
        sa.Column('reward', sa.Float(), nullable=True),
        sa.Column('weight', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('decision_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
    )

    op.create_index('idx_corpus_tenant_config', 'training_corpus', ['tenant_id', 'config_id'])
    op.create_index('idx_corpus_config_layer', 'training_corpus', ['config_id', 'layer'])
    op.create_index('idx_corpus_config_scenario', 'training_corpus', ['config_id', 'scenario_id'])
    op.create_index('idx_corpus_config_origin', 'training_corpus', ['config_id', 'origin', 'created_at'])
    op.create_index('idx_corpus_trm_type', 'training_corpus', ['config_id', 'trm_type'])
    op.create_index('idx_corpus_site', 'training_corpus', ['config_id', 'site_id'])
    op.create_index('idx_corpus_created_at', 'training_corpus', ['created_at'])

    # RLS policy for tenant isolation
    op.execute("""
        ALTER TABLE training_corpus ENABLE ROW LEVEL SECURITY;
        CREATE POLICY training_corpus_tenant_isolation ON training_corpus
            USING (tenant_id = current_setting('app.current_tenant_id', true)::int);
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS training_corpus_tenant_isolation ON training_corpus")
    op.drop_index('idx_corpus_created_at', 'training_corpus')
    op.drop_index('idx_corpus_site', 'training_corpus')
    op.drop_index('idx_corpus_trm_type', 'training_corpus')
    op.drop_index('idx_corpus_config_origin', 'training_corpus')
    op.drop_index('idx_corpus_config_scenario', 'training_corpus')
    op.drop_index('idx_corpus_config_layer', 'training_corpus')
    op.drop_index('idx_corpus_tenant_config', 'training_corpus')
    op.drop_table('training_corpus')
