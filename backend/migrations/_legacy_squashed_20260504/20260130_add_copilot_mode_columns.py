"""Add Phase 2 Copilot Mode columns to decision_proposals

Revision ID: 20260130_copilot
Revises: dag_default_true_001
Create Date: 2026-01-30 12:00:00.000000

Adds columns needed for copilot mode override proposals:
- game_id: Links proposal to a game (for Beer Game overrides)
- created_by: User ID who created the proposal
- decision_type: Type of decision (override_fulfillment, override_replenishment)
- proposal_metadata: Flexible JSON for player context, quantities, etc.

Also makes scenario_id nullable to support game-based proposals that don't use scenarios.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '20260130_copilot'
down_revision = 'dag_default_true_001'
branch_labels = None
depends_on = None


def upgrade():
    # Add game_id column for copilot mode
    op.add_column(
        'decision_proposals',
        sa.Column('game_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_decision_proposals_game_id',
        'decision_proposals',
        'games',
        ['game_id'],
        ['id'],
        ondelete='CASCADE'
    )
    op.create_index('idx_decision_proposals_game_id', 'decision_proposals', ['game_id'])

    # Add created_by column (user who created)
    op.add_column(
        'decision_proposals',
        sa.Column('created_by', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_decision_proposals_created_by',
        'decision_proposals',
        'users',
        ['created_by'],
        ['id'],
        ondelete='SET NULL'
    )

    # Add decision_type column
    op.add_column(
        'decision_proposals',
        sa.Column('decision_type', sa.String(50), nullable=True)
    )

    # Add proposal_metadata column
    op.add_column(
        'decision_proposals',
        sa.Column('proposal_metadata', sa.JSON(), nullable=True)
    )

    # Make scenario_id nullable for game-based proposals
    op.alter_column(
        'decision_proposals',
        'scenario_id',
        existing_type=sa.Integer(),
        nullable=True
    )

    # Make some columns nullable that shouldn't be required
    op.alter_column(
        'decision_proposals',
        'proposed_by',
        existing_type=sa.String(100),
        nullable=True
    )
    op.alter_column(
        'decision_proposals',
        'proposed_by_type',
        existing_type=sa.String(20),
        nullable=True
    )
    op.alter_column(
        'decision_proposals',
        'action_type',
        existing_type=sa.String(50),
        nullable=True
    )
    op.alter_column(
        'decision_proposals',
        'action_params',
        existing_type=sa.JSON(),
        nullable=True
    )


def downgrade():
    # Remove added columns
    op.drop_index('idx_decision_proposals_game_id', 'decision_proposals')
    op.drop_constraint('fk_decision_proposals_game_id', 'decision_proposals', type_='foreignkey')
    op.drop_column('decision_proposals', 'game_id')

    op.drop_constraint('fk_decision_proposals_created_by', 'decision_proposals', type_='foreignkey')
    op.drop_column('decision_proposals', 'created_by')

    op.drop_column('decision_proposals', 'decision_type')
    op.drop_column('decision_proposals', 'proposal_metadata')

    # Restore NOT NULL constraints
    op.alter_column(
        'decision_proposals',
        'scenario_id',
        existing_type=sa.Integer(),
        nullable=False
    )
    op.alter_column(
        'decision_proposals',
        'proposed_by',
        existing_type=sa.String(100),
        nullable=False
    )
    op.alter_column(
        'decision_proposals',
        'proposed_by_type',
        existing_type=sa.String(20),
        nullable=False
    )
    op.alter_column(
        'decision_proposals',
        'action_type',
        existing_type=sa.String(50),
        nullable=False
    )
    op.alter_column(
        'decision_proposals',
        'action_params',
        existing_type=sa.JSON(),
        nullable=False
    )
