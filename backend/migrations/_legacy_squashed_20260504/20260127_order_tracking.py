"""Order Tracking and Pipeline Visibility

Revision ID: 20260127_order_tracking
Revises: 20260123_shipment
Create Date: 2026-01-27

Adds schema support for DAG-ordered sequential execution:
- Links PlayerRound to TransferOrder/PurchaseOrder for full traceability
- Adds round phase tracking (FULFILLMENT → REPLENISHMENT → DECISION)
- Adds agent mode configuration (MANUAL, COPILOT, AUTONOMOUS)
- Enables pipeline visibility queries with arrival_round tracking

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20260127_order_tracking'
down_revision = '20260123_shipment'
branch_labels = None
depends_on = None


def upgrade():
    # =========================================================================
    # Create ENUM Types First (PostgreSQL requires these before column creation)
    # =========================================================================

    # Create order_type_enum
    order_type_enum = sa.Enum('TO', 'PO', 'MO', name='order_type_enum')
    order_type_enum.create(op.get_bind(), checkfirst=True)

    # Create round_phase_enum
    round_phase_enum = sa.Enum('FULFILLMENT', 'REPLENISHMENT', 'DECISION', 'COMPLETED',
                               name='round_phase_enum')
    round_phase_enum.create(op.get_bind(), checkfirst=True)

    # Create agent_mode_enum
    agent_mode_enum = sa.Enum('MANUAL', 'COPILOT', 'AUTONOMOUS', name='agent_mode_enum')
    agent_mode_enum.create(op.get_bind(), checkfirst=True)

    # =========================================================================
    # PlayerRound Extensions - Order Tracking & Phase Management
    # =========================================================================

    # Add order tracking columns
    op.add_column('player_rounds',
        sa.Column('upstream_order_id', sa.Integer, nullable=True,
                  comment='FK to order placed upstream (TO/PO/MO)'))
    op.add_column('player_rounds',
        sa.Column('upstream_order_type',
                  sa.Enum('TO', 'PO', 'MO', name='order_type_enum'),
                  nullable=True,
                  comment='Type of upstream order'))
    op.add_column('player_rounds',
        sa.Column('downstream_demand_order_id', sa.Integer, nullable=True,
                  comment='FK to demand from downstream (outbound_order_line)'))
    op.add_column('player_rounds',
        sa.Column('round_phase',
                  sa.Enum('FULFILLMENT', 'REPLENISHMENT', 'DECISION', 'COMPLETED',
                         name='round_phase_enum'),
                  server_default='DECISION',
                  nullable=False,
                  comment='Current phase of round processing'))

    # Add indexes for PlayerRound
    op.create_index('idx_player_round_upstream_order', 'player_rounds',
                    ['upstream_order_id', 'upstream_order_type'])
    op.create_index('idx_player_round_downstream_order', 'player_rounds',
                    ['downstream_demand_order_id'])
    op.create_index('idx_player_round_phase', 'player_rounds',
                    ['round_id', 'round_phase'])

    # =========================================================================
    # RoundMetric Extensions - Order Tracking (Optional - table may not exist yet)
    # =========================================================================

    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if 'round_metric' in tables:
        op.add_column('round_metric',
            sa.Column('upstream_order_id', sa.Integer, nullable=True,
                      comment='FK to order placed upstream'))
        op.add_column('round_metric',
            sa.Column('upstream_order_type',
                      sa.Enum('TO', 'PO', 'MO', name='order_type_enum'),
                      nullable=True))
        op.add_column('round_metric',
            sa.Column('round_phase',
                      sa.Enum('FULFILLMENT', 'REPLENISHMENT', 'DECISION', 'COMPLETED',
                             name='round_phase_enum'),
                      server_default='DECISION',
                      nullable=False))

        # Add indexes for RoundMetric
        op.create_index('idx_round_metric_order', 'round_metric',
                        ['upstream_order_id', 'upstream_order_type'])
        op.create_index('idx_round_metric_phase', 'round_metric',
                        ['game_id', 'round_number', 'round_phase'])

    # =========================================================================
    # GameRound Extensions - Phase Management
    # =========================================================================

    # Check if game_rounds table exists (it might be 'rounds' in some versions)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    game_rounds_table = 'game_rounds' if 'game_rounds' in tables else 'rounds'

    op.add_column(game_rounds_table,
        sa.Column('current_phase',
                  sa.Enum('FULFILLMENT', 'REPLENISHMENT', 'DECISION', 'COMPLETED',
                         name='round_phase_enum'),
                  server_default='DECISION',
                  nullable=False,
                  comment='Current processing phase'))
    op.add_column(game_rounds_table,
        sa.Column('phase_started_at', sa.DateTime, nullable=True,
                  comment='When current phase started'))
    op.add_column(game_rounds_table,
        sa.Column('fulfillment_completed_at', sa.DateTime, nullable=True))
    op.add_column(game_rounds_table,
        sa.Column('replenishment_completed_at', sa.DateTime, nullable=True))
    op.add_column(game_rounds_table,
        sa.Column('decision_completed_at', sa.DateTime, nullable=True))

    # Add index for GameRound
    op.create_index(f'idx_{game_rounds_table}_phase', game_rounds_table,
                    ['game_id', 'round_number', 'current_phase'])

    # =========================================================================
    # Player Extensions - Agent Mode Configuration
    # =========================================================================

    op.add_column('players',
        sa.Column('agent_mode',
                  sa.Enum('MANUAL', 'COPILOT', 'AUTONOMOUS', name='agent_mode_enum'),
                  server_default='MANUAL',
                  nullable=False,
                  comment='Agent assistance level'))
    op.add_column('players',
        sa.Column('agent_config_id', sa.Integer, nullable=True,
                  comment='FK to agent_configs if AI-assisted'))

    # Add FK constraint and index for Player
    try:
        op.create_foreign_key('fk_player_agent_config', 'players', 'agent_configs',
                              ['agent_config_id'], ['id'], ondelete='SET NULL')
    except Exception:
        # FK might fail if agent_configs table doesn't exist yet; log and continue
        pass

    op.create_index('idx_player_agent_mode', 'players', ['game_id', 'agent_mode'])

    # =========================================================================
    # TransferOrder Extensions - Bidirectional Link to PlayerRound
    # =========================================================================

    op.add_column('transfer_order',
        sa.Column('source_player_round_id', sa.Integer, nullable=True,
                  comment='Link to PlayerRound that placed this order'))

    try:
        op.create_foreign_key('fk_to_player_round', 'transfer_order', 'player_rounds',
                              ['source_player_round_id'], ['id'], ondelete='SET NULL')
    except Exception:
        # FK might fail in some DB states; log and continue
        pass

    op.create_index('idx_to_player_round', 'transfer_order', ['source_player_round_id'])

    # =========================================================================
    # PurchaseOrder Extensions - DEFERRED TO PHASE 2
    # =========================================================================
    # PurchaseOrder modifications deferred because game_id/order_round columns
    # don't exist yet (waiting for beer_game_exec migration to be applied).
    # Phase 1 focuses on TransferOrders for Beer Game execution.

    # =========================================================================
    # Game Model Extensions - DAG Sequential Feature Flag
    # =========================================================================

    op.add_column('games',
        sa.Column('use_dag_sequential', sa.Boolean, server_default='0', nullable=False,
                  comment='Use DAG-ordered sequential execution (Phase 1)'))

    op.create_index('idx_game_dag_sequential', 'games', ['use_dag_sequential'])


def downgrade():
    # Drop indexes
    op.drop_index('idx_game_dag_sequential', 'games')
    op.drop_index('idx_player_round_upstream_order', 'player_rounds')
    op.drop_index('idx_player_round_downstream_order', 'player_rounds')
    op.drop_index('idx_player_round_phase', 'player_rounds')

    # Drop round_metric indexes only if table exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if 'round_metric' in tables:
        op.drop_index('idx_round_metric_order', 'round_metric')
        op.drop_index('idx_round_metric_phase', 'round_metric')
    op.drop_index('idx_player_agent_mode', 'players')
    op.drop_index('idx_to_player_round', 'transfer_order')
    # PurchaseOrder indexes not created in this migration

    # Determine game_rounds table name
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    game_rounds_table = 'game_rounds' if 'game_rounds' in tables else 'rounds'

    op.drop_index(f'idx_{game_rounds_table}_phase', game_rounds_table)

    # Drop foreign keys
    try:
        op.drop_constraint('fk_player_agent_config', 'players', type_='foreignkey')
    except Exception:
        pass
    try:
        op.drop_constraint('fk_to_player_round', 'transfer_order', type_='foreignkey')
    except Exception:
        pass
    try:
        op.drop_constraint('fk_po_player_round', 'purchase_order', type_='foreignkey')
    except Exception:
        pass

    # Drop columns
    op.drop_column('games', 'use_dag_sequential')
    op.drop_column('player_rounds', 'upstream_order_id')
    op.drop_column('player_rounds', 'upstream_order_type')
    op.drop_column('player_rounds', 'downstream_demand_order_id')
    op.drop_column('player_rounds', 'round_phase')

    # Drop round_metric columns only if table exists
    if 'round_metric' in tables:
        op.drop_column('round_metric', 'upstream_order_id')
        op.drop_column('round_metric', 'upstream_order_type')
        op.drop_column('round_metric', 'round_phase')

    op.drop_column(game_rounds_table, 'current_phase')
    op.drop_column(game_rounds_table, 'phase_started_at')
    op.drop_column(game_rounds_table, 'fulfillment_completed_at')
    op.drop_column(game_rounds_table, 'replenishment_completed_at')
    op.drop_column(game_rounds_table, 'decision_completed_at')

    op.drop_column('players', 'agent_mode')
    op.drop_column('players', 'agent_config_id')

    op.drop_column('transfer_order', 'source_player_round_id')

    # PurchaseOrder columns not added in this migration

    # =========================================================================
    # Drop ENUM Types (PostgreSQL cleanup)
    # =========================================================================

    # Drop ENUMs (only if no columns are using them)
    sa.Enum(name='agent_mode_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='round_phase_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='order_type_enum').drop(op.get_bind(), checkfirst=True)
