"""add agent_mode_history table

Revision ID: 20260128_agent_mode_history
Revises: 20260127_decision_simulation
Create Date: 2026-01-28 10:00:00.000000

Phase 4: Multi-Agent Orchestration
Adds agent_mode_history table for tracking dynamic mode switches during gameplay.
Used for RLHF training data collection.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260128_agent_mode_history'
down_revision = '20260127_decision_simulation'
branch_labels = None
depends_on = None


def upgrade():
    """
    Add agent_mode_history table for Phase 4 dynamic mode switching.

    Table tracks:
    - When users switch between manual/copilot/autonomous modes
    - Why they switched (user_request, performance_threshold, etc.)
    - Performance context at time of switch
    - Used for RLHF training to teach agents when to suggest mode changes
    """
    # Create agent_mode_history table
    op.create_table(
        'agent_mode_history',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('game_id', sa.Integer(), nullable=False),
        sa.Column('round_number', sa.Integer(), nullable=False),
        sa.Column('previous_mode', sa.String(20), nullable=False),
        sa.Column('new_mode', sa.String(20), nullable=False),
        sa.Column('reason', sa.String(50), nullable=False),
        sa.Column('triggered_by', sa.String(20), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ondelete='CASCADE')
    )

    # Indexes for efficient querying
    op.create_index(
        'idx_agent_mode_history_player',
        'agent_mode_history',
        ['player_id']
    )

    op.create_index(
        'idx_agent_mode_history_game',
        'agent_mode_history',
        ['game_id']
    )

    op.create_index(
        'idx_agent_mode_history_timestamp',
        'agent_mode_history',
        ['timestamp']
    )

    op.create_index(
        'idx_agent_mode_history_game_round',
        'agent_mode_history',
        ['game_id', 'round_number']
    )

    # Add agent_mode column to players table if not exists
    # Check if column already exists before adding
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('players')]

    if 'agent_mode' not in columns:
        op.add_column(
            'players',
            sa.Column(
                'agent_mode',
                sa.String(20),
                nullable=True,
                server_default='manual',
                comment='Current agent mode: manual, copilot, or autonomous'
            )
        )


def downgrade():
    """Remove agent_mode_history table and related changes."""
    # Drop indexes
    op.drop_index('idx_agent_mode_history_game_round', table_name='agent_mode_history')
    op.drop_index('idx_agent_mode_history_timestamp', table_name='agent_mode_history')
    op.drop_index('idx_agent_mode_history_game', table_name='agent_mode_history')
    op.drop_index('idx_agent_mode_history_player', table_name='agent_mode_history')

    # Drop table
    op.drop_table('agent_mode_history')

    # Note: Not dropping agent_mode column from players table
    # as it may be in use by other features
