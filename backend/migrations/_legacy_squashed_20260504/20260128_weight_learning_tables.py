"""add weight learning, performance tracking, and rlhf tables

Revision ID: 20260128_weight_learning_tables
Revises: 20260128_agent_mode_history
Create Date: 2026-01-28 12:00:00.000000

Phase 4: Multi-Agent Orchestration - Weight Learning & Performance Tracking
Adds tables for:
- learned_weight_configs: Adaptive weight learning persistence
- agent_performance_logs: Per-round agent performance metrics
- rlhf_feedback: Human feedback on AI recommendations for training
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260128_weight_learning_tables'
down_revision = '20260128_agent_mode_history'
branch_labels = None
depends_on = None


def upgrade():
    """
    Add tables for weight learning, performance tracking, and RLHF.
    """

    # 1. learned_weight_configs table
    op.create_table(
        'learned_weight_configs',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('context_id', sa.Integer(), nullable=False),
        sa.Column('context_type', sa.String(20), nullable=False, server_default='game'),
        sa.Column('weights', sa.JSON(), nullable=False),
        sa.Column('learning_method', sa.String(20), nullable=False),
        sa.Column('num_samples', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('performance_metrics', sa.JSON(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # Indexes for learned_weight_configs
    op.create_index(
        'idx_learned_weights_context',
        'learned_weight_configs',
        ['context_id', 'is_active']
    )
    op.create_index(
        'idx_learned_weights_method',
        'learned_weight_configs',
        ['learning_method']
    )
    op.create_index(
        'idx_learned_weights_updated',
        'learned_weight_configs',
        ['updated_at']
    )

    # 2. agent_performance_logs table
    op.create_table(
        'agent_performance_logs',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('game_id', sa.Integer(), nullable=False),
        sa.Column('round_number', sa.Integer(), nullable=False),
        sa.Column('agent_type', sa.String(20), nullable=False),
        sa.Column('agent_mode', sa.String(20), nullable=False),
        sa.Column('total_cost', sa.Float(), nullable=False),
        sa.Column('holding_cost', sa.Float(), nullable=False),
        sa.Column('shortage_cost', sa.Float(), nullable=False),
        sa.Column('service_level', sa.Float(), nullable=False),
        sa.Column('stockout_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('backlog', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('avg_inventory', sa.Float(), nullable=False),
        sa.Column('inventory_variance', sa.Float(), nullable=True),
        sa.Column('demand_amplification', sa.Float(), nullable=True),
        sa.Column('order_variance', sa.Float(), nullable=True),
        sa.Column('order_quantity', sa.Integer(), nullable=True),
        sa.Column('optimal_order', sa.Integer(), nullable=True),
        sa.Column('decision_error', sa.Float(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ondelete='CASCADE')
    )

    # Indexes for agent_performance_logs
    op.create_index(
        'idx_perf_logs_player',
        'agent_performance_logs',
        ['player_id']
    )
    op.create_index(
        'idx_perf_logs_game',
        'agent_performance_logs',
        ['game_id']
    )
    op.create_index(
        'idx_perf_logs_round',
        'agent_performance_logs',
        ['round_number']
    )
    op.create_index(
        'idx_perf_logs_agent_type',
        'agent_performance_logs',
        ['agent_type']
    )
    op.create_index(
        'idx_perf_logs_timestamp',
        'agent_performance_logs',
        ['timestamp']
    )
    op.create_index(
        'idx_perf_logs_game_round',
        'agent_performance_logs',
        ['game_id', 'round_number']
    )

    # 3. rlhf_feedback table
    op.create_table(
        'rlhf_feedback',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('game_id', sa.Integer(), nullable=False),
        sa.Column('round_number', sa.Integer(), nullable=False),
        sa.Column('agent_type', sa.String(20), nullable=False),
        sa.Column('game_state', sa.JSON(), nullable=False),
        sa.Column('ai_suggestion', sa.Integer(), nullable=False),
        sa.Column('ai_reasoning', sa.Text(), nullable=True),
        sa.Column('ai_confidence', sa.Float(), nullable=True),
        sa.Column('human_decision', sa.Integer(), nullable=False),
        sa.Column('feedback_action', sa.String(20), nullable=False),
        sa.Column('modification_delta', sa.Integer(), nullable=True),
        sa.Column('ai_outcome', sa.JSON(), nullable=True),
        sa.Column('human_outcome', sa.JSON(), nullable=True),
        sa.Column('preference_label', sa.String(20), nullable=False, server_default='unknown'),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ondelete='CASCADE')
    )

    # Indexes for rlhf_feedback
    op.create_index(
        'idx_rlhf_player',
        'rlhf_feedback',
        ['player_id']
    )
    op.create_index(
        'idx_rlhf_game',
        'rlhf_feedback',
        ['game_id']
    )
    op.create_index(
        'idx_rlhf_agent_type',
        'rlhf_feedback',
        ['agent_type']
    )
    op.create_index(
        'idx_rlhf_feedback_action',
        'rlhf_feedback',
        ['feedback_action']
    )
    op.create_index(
        'idx_rlhf_preference_label',
        'rlhf_feedback',
        ['preference_label']
    )
    op.create_index(
        'idx_rlhf_timestamp',
        'rlhf_feedback',
        ['timestamp']
    )
    op.create_index(
        'idx_rlhf_game_round',
        'rlhf_feedback',
        ['game_id', 'round_number']
    )


def downgrade():
    """Remove weight learning, performance tracking, and RLHF tables."""

    # Drop rlhf_feedback indexes and table
    op.drop_index('idx_rlhf_game_round', table_name='rlhf_feedback')
    op.drop_index('idx_rlhf_timestamp', table_name='rlhf_feedback')
    op.drop_index('idx_rlhf_preference_label', table_name='rlhf_feedback')
    op.drop_index('idx_rlhf_feedback_action', table_name='rlhf_feedback')
    op.drop_index('idx_rlhf_agent_type', table_name='rlhf_feedback')
    op.drop_index('idx_rlhf_game', table_name='rlhf_feedback')
    op.drop_index('idx_rlhf_player', table_name='rlhf_feedback')
    op.drop_table('rlhf_feedback')

    # Drop agent_performance_logs indexes and table
    op.drop_index('idx_perf_logs_game_round', table_name='agent_performance_logs')
    op.drop_index('idx_perf_logs_timestamp', table_name='agent_performance_logs')
    op.drop_index('idx_perf_logs_agent_type', table_name='agent_performance_logs')
    op.drop_index('idx_perf_logs_round', table_name='agent_performance_logs')
    op.drop_index('idx_perf_logs_game', table_name='agent_performance_logs')
    op.drop_index('idx_perf_logs_player', table_name='agent_performance_logs')
    op.drop_table('agent_performance_logs')

    # Drop learned_weight_configs indexes and table
    op.drop_index('idx_learned_weights_updated', table_name='learned_weight_configs')
    op.drop_index('idx_learned_weights_method', table_name='learned_weight_configs')
    op.drop_index('idx_learned_weights_context', table_name='learned_weight_configs')
    op.drop_table('learned_weight_configs')
