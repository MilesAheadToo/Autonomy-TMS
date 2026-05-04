"""DAG-ordered execution with TO/PO tracking

Revision ID: dag_order_tracking_001
Revises:
Create Date: 2026-01-29

Phase 1 Implementation: DAG-Ordered Sequential Execution with TO/PO Tracking

This migration adds:
1. agent_mode to players table (MANUAL, COPILOT, AUTONOMOUS)
2. upstream_order_id/type to player_rounds (TO/PO/MO tracking)
3. round_phase to player_rounds (FULFILLMENT, REPLENISHMENT, DECISION, COMPLETED)
4. fulfillment/replenishment tracking fields to player_rounds
5. source_player_round_id to transfer_order (bidirectional link)
6. Relevant indexes for query optimization
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = 'dag_order_tracking_001'
down_revision = '20260129_exception_workflows'
branch_labels = None
depends_on = None


def column_exists(table_name, column_name):
    """Check if a column exists in a table"""
    conn = op.get_bind()
    result = conn.execute(sa.text(
        f"SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name = '{table_name}' AND column_name = '{column_name}')"
    ))
    return result.scalar()


def index_exists(index_name):
    """Check if an index exists"""
    conn = op.get_bind()
    result = conn.execute(sa.text(
        f"SELECT EXISTS(SELECT 1 FROM pg_indexes WHERE indexname = '{index_name}')"
    ))
    return result.scalar()


def upgrade():
    # Create enum types - PostgreSQL will fail if they already exist, so check first
    conn = op.get_bind()

    # Check if agentmode enum exists
    result = conn.execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_type WHERE typname = 'agentmode')"))
    if not result.scalar():
        conn.execute(sa.text("CREATE TYPE agentmode AS ENUM ('MANUAL', 'COPILOT', 'AUTONOMOUS')"))

    # Check if upstreamordertype enum exists
    result = conn.execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_type WHERE typname = 'upstreamordertype')"))
    if not result.scalar():
        conn.execute(sa.text("CREATE TYPE upstreamordertype AS ENUM ('TO', 'PO', 'MO')"))

    # Check if roundphase enum exists
    result = conn.execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_type WHERE typname = 'roundphase')"))
    if not result.scalar():
        conn.execute(sa.text("CREATE TYPE roundphase AS ENUM ('FULFILLMENT', 'REPLENISHMENT', 'DECISION', 'COMPLETED')"))

    # 1. Add agent_mode to players table (if not exists)
    if not column_exists('players', 'agent_mode'):
        op.add_column('players', sa.Column('agent_mode', sa.Enum('MANUAL', 'COPILOT', 'AUTONOMOUS', name='agentmode'), nullable=True))

    # Set default for existing players
    op.execute("UPDATE players SET agent_mode = 'MANUAL' WHERE agent_mode IS NULL")

    # 2. Add upstream order tracking to player_rounds
    if not column_exists('player_rounds', 'upstream_order_id'):
        op.add_column('player_rounds', sa.Column('upstream_order_id', sa.Integer(), nullable=True))
    if not column_exists('player_rounds', 'upstream_order_type'):
        op.add_column('player_rounds', sa.Column('upstream_order_type', sa.Enum('TO', 'PO', 'MO', name='upstreamordertype'), nullable=True))
    if not column_exists('player_rounds', 'round_phase'):
        op.add_column('player_rounds', sa.Column('round_phase', sa.Enum('FULFILLMENT', 'REPLENISHMENT', 'DECISION', 'COMPLETED', name='roundphase'), server_default='DECISION', nullable=False))

    # 3. Add fulfillment phase tracking to player_rounds
    if not column_exists('player_rounds', 'fulfillment_qty'):
        op.add_column('player_rounds', sa.Column('fulfillment_qty', sa.Integer(), nullable=True))
    if not column_exists('player_rounds', 'fulfillment_submitted_at'):
        op.add_column('player_rounds', sa.Column('fulfillment_submitted_at', sa.DateTime(), nullable=True))

    # 4. Add replenishment phase tracking to player_rounds
    if not column_exists('player_rounds', 'replenishment_qty'):
        op.add_column('player_rounds', sa.Column('replenishment_qty', sa.Integer(), nullable=True))
    if not column_exists('player_rounds', 'replenishment_submitted_at'):
        op.add_column('player_rounds', sa.Column('replenishment_submitted_at', sa.DateTime(), nullable=True))

    # 5. Add source_player_round_id to transfer_order (bidirectional link)
    if not column_exists('transfer_order', 'source_player_round_id'):
        op.add_column('transfer_order', sa.Column('source_player_round_id', sa.Integer(), nullable=True))
        # Add FK constraint separately
        op.create_foreign_key('fk_to_player_round', 'transfer_order', 'player_rounds', ['source_player_round_id'], ['id'], ondelete='SET NULL')

    # 6. Create indexes for efficient querying (if not exist)
    if not index_exists('idx_pr_upstream_order'):
        op.create_index('idx_pr_upstream_order', 'player_rounds', ['upstream_order_id', 'upstream_order_type'])
    if not index_exists('idx_pr_round_phase'):
        op.create_index('idx_pr_round_phase', 'player_rounds', ['round_id', 'round_phase'])
    if not index_exists('idx_pr_player_round'):
        op.create_index('idx_pr_player_round', 'player_rounds', ['player_id', 'round_id'])
    if not index_exists('idx_to_player_round'):
        op.create_index('idx_to_player_round', 'transfer_order', ['source_player_round_id'])


def downgrade():
    # Remove indexes
    op.drop_index('idx_to_player_round', table_name='transfer_order')
    op.drop_index('idx_pr_player_round', table_name='player_rounds')
    op.drop_index('idx_pr_round_phase', table_name='player_rounds')
    op.drop_index('idx_pr_upstream_order', table_name='player_rounds')

    # Remove columns from transfer_order
    op.drop_column('transfer_order', 'source_player_round_id')

    # Remove columns from player_rounds
    op.drop_column('player_rounds', 'replenishment_submitted_at')
    op.drop_column('player_rounds', 'replenishment_qty')
    op.drop_column('player_rounds', 'fulfillment_submitted_at')
    op.drop_column('player_rounds', 'fulfillment_qty')
    op.drop_column('player_rounds', 'round_phase')
    op.drop_column('player_rounds', 'upstream_order_type')
    op.drop_column('player_rounds', 'upstream_order_id')

    # Remove column from players
    op.drop_column('players', 'agent_mode')

    # Drop enum types (MySQL doesn't support dropping enum types)
    # op.execute("DROP TYPE IF EXISTS agentmode")
    # op.execute("DROP TYPE IF EXISTS upstreamordertype")
