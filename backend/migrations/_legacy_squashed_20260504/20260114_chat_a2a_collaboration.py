"""Create chat and A2A collaboration tables

Revision ID: 20260114_chat
Revises: 20260113_performance_indexes
Create Date: 2026-01-14

Phase 7 Sprint 2 - Real-time A2A Collaboration
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20260114_chat'
down_revision = '20260113_performance_indexes'
branch_labels = None
depends_on = None


def upgrade():
    """Create chat and A2A collaboration tables."""

    # Create chat_messages table
    op.create_table(
        'chat_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('game_id', sa.Integer(), nullable=False),
        sa.Column('sender_id', sa.String(100), nullable=False),
        sa.Column('sender_name', sa.String(100), nullable=False),
        sa.Column('sender_type', sa.Enum('PLAYER', 'AGENT', name='sendertype'), nullable=False),
        sa.Column('recipient_id', sa.String(100), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('type', sa.Enum('TEXT', 'SUGGESTION', 'QUESTION', 'ANALYSIS', name='messagetype'), nullable=False),
        sa.Column('message_metadata', mysql.JSON(), nullable=True),
        sa.Column('read', sa.Boolean(), nullable=False, default=False),
        sa.Column('delivered', sa.Boolean(), nullable=False, default=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Create indexes for chat_messages
    op.create_index('ix_chat_messages_game_id', 'chat_messages', ['game_id'])
    op.create_index('ix_chat_messages_created_at', 'chat_messages', ['created_at'])
    op.create_index('ix_chat_messages_recipient_read', 'chat_messages', ['recipient_id', 'read'])

    # Create agent_suggestions table
    op.create_table(
        'agent_suggestions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('game_id', sa.Integer(), nullable=False),
        sa.Column('round', sa.Integer(), nullable=False),
        sa.Column('agent_name', sa.String(100), nullable=False),
        sa.Column('order_quantity', sa.Integer(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('rationale', sa.Text(), nullable=False),
        sa.Column('context', mysql.JSON(), nullable=False),
        sa.Column('accepted', sa.Boolean(), nullable=True),
        sa.Column('player_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Create indexes for agent_suggestions
    op.create_index('ix_agent_suggestions_game_id', 'agent_suggestions', ['game_id'])
    op.create_index('ix_agent_suggestions_agent_name', 'agent_suggestions', ['agent_name'])
    op.create_index('ix_agent_suggestions_accepted', 'agent_suggestions', ['accepted'])

    # Create what_if_analyses table
    op.create_table(
        'what_if_analyses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('game_id', sa.Integer(), nullable=False),
        sa.Column('round', sa.Integer(), nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('scenario', mysql.JSON(), nullable=False),
        sa.Column('result', mysql.JSON(), nullable=True),
        sa.Column('agent_analysis', sa.Text(), nullable=True),
        sa.Column('completed', sa.Boolean(), nullable=False, default=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Create indexes for what_if_analyses
    op.create_index('ix_what_if_analyses_game_id', 'what_if_analyses', ['game_id'])
    op.create_index('ix_what_if_analyses_player_id', 'what_if_analyses', ['player_id'])
    op.create_index('ix_what_if_analyses_completed', 'what_if_analyses', ['completed'])


def downgrade():
    """Drop chat and A2A collaboration tables."""

    # Drop what_if_analyses table
    op.drop_index('ix_what_if_analyses_completed', 'what_if_analyses')
    op.drop_index('ix_what_if_analyses_player_id', 'what_if_analyses')
    op.drop_index('ix_what_if_analyses_game_id', 'what_if_analyses')
    op.drop_table('what_if_analyses')

    # Drop agent_suggestions table
    op.drop_index('ix_agent_suggestions_accepted', 'agent_suggestions')
    op.drop_index('ix_agent_suggestions_agent_name', 'agent_suggestions')
    op.drop_index('ix_agent_suggestions_game_id', 'agent_suggestions')
    op.drop_table('agent_suggestions')

    # Drop chat_messages table
    op.drop_index('ix_chat_messages_recipient_read', 'chat_messages')
    op.drop_index('ix_chat_messages_created_at', 'chat_messages')
    op.drop_index('ix_chat_messages_game_id', 'chat_messages')
    op.drop_table('chat_messages')

    # Drop enums
    op.execute('DROP TYPE IF EXISTS messagetype')
    op.execute('DROP TYPE IF EXISTS sendertype')
