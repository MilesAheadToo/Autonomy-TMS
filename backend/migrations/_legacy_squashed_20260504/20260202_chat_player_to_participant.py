"""Fix player_id -> participant_id in chat-related tables

This migration fixes column naming mismatches between models and database:
- agent_suggestions.player_id -> participant_id
- what_if_analyses.player_id -> participant_id
- what_if_analyses.scenario -> scenario_data

Revision ID: 20260202_chat_participant
Revises: 20260201_group_mode
Create Date: 2026-02-02

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '20260202_chat_participant'
down_revision = '20260201_group_mode'
branch_labels = None
depends_on = None


def upgrade():
    """Rename columns to match model definitions."""

    # Fix agent_suggestions.player_id -> participant_id
    op.alter_column('agent_suggestions', 'player_id', new_column_name='participant_id')
    print("Renamed: agent_suggestions.player_id -> participant_id")

    # Fix what_if_analyses.player_id -> participant_id
    op.alter_column('what_if_analyses', 'player_id', new_column_name='participant_id')
    print("Renamed: what_if_analyses.player_id -> participant_id")

    # Fix what_if_analyses.scenario -> scenario_data
    op.alter_column('what_if_analyses', 'scenario', new_column_name='scenario_data')
    print("Renamed: what_if_analyses.scenario -> scenario_data")

    print("Chat model column fixes applied successfully!")


def downgrade():
    """Revert column renames."""

    # Revert what_if_analyses.scenario_data -> scenario
    op.alter_column('what_if_analyses', 'scenario_data', new_column_name='scenario')
    print("Reverted: what_if_analyses.scenario_data -> scenario")

    # Revert what_if_analyses.participant_id -> player_id
    op.alter_column('what_if_analyses', 'participant_id', new_column_name='player_id')
    print("Reverted: what_if_analyses.participant_id -> player_id")

    # Revert agent_suggestions.participant_id -> player_id
    op.alter_column('agent_suggestions', 'participant_id', new_column_name='player_id')
    print("Reverted: agent_suggestions.participant_id -> player_id")

    print("Chat model column fixes reverted!")
