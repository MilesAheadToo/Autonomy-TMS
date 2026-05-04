"""Create decision_embeddings table for RAG decision memory

Revision ID: 20260226_decmem
Revises: 20260213_per_site_trm
Create Date: 2026-02-26

Stores embedded past decisions (state + outcome) for RAG retrieval.
Uses pgvector 768-dim embeddings (same as kb_chunks) with HNSW index
for fast approximate nearest neighbor search.
"""
from alembic import op
import sqlalchemy as sa

revision = '20260226_decmem'
down_revision = '20260213_per_site_trm'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure pgvector extension exists (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        'decision_embeddings',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),

        # Decision identity
        sa.Column('trm_type', sa.String(50), nullable=False),
        sa.Column('site_key', sa.String(100), nullable=True),
        sa.Column('tenant_id', sa.Integer(), nullable=True),

        # State at decision time
        sa.Column('state_features', sa.JSON(), nullable=False),
        sa.Column('state_summary', sa.Text(), nullable=False),

        # Decision
        sa.Column('decision', sa.JSON(), nullable=False),
        sa.Column('decision_source', sa.String(50), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),

        # Outcome (filled after feedback horizon)
        sa.Column('outcome', sa.JSON(), nullable=True),
        sa.Column('outcome_summary', sa.Text(), nullable=True),
        sa.Column('reward', sa.Float(), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('outcome_recorded_at', sa.DateTime(), nullable=True),
    )

    # Add pgvector column using raw SQL (alembic doesn't know pgvector types)
    op.execute("""
        ALTER TABLE decision_embeddings
        ADD COLUMN embedding vector(768)
    """)

    # Create HNSW index for fast vector similarity search
    op.execute("""
        CREATE INDEX idx_decision_embeddings_hnsw
        ON decision_embeddings
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # Additional indexes
    op.create_index('idx_de_trm_type', 'decision_embeddings', ['trm_type'])
    op.create_index('idx_de_site', 'decision_embeddings', ['site_key'])
    op.create_index('idx_de_tenant', 'decision_embeddings', ['tenant_id'])
    op.create_index('idx_de_reward', 'decision_embeddings', ['reward'])
    op.create_index('idx_de_created', 'decision_embeddings', ['created_at'])


def downgrade() -> None:
    op.drop_table('decision_embeddings')
