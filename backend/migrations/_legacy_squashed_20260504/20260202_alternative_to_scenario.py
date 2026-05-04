"""Rename 'alternatives' terminology to 'scenarios' for consistency with models.

This migration corrects the Feb 1 terminology refactoring to use 'scenario' instead
of 'alternative' - matching the model definitions:
- alternatives -> scenarios
- alternative_id -> scenario_id
- alternative_rounds -> scenario_rounds

Revision ID: 20260202_alt_to_scen
Revises: 20260201_terminology
Create Date: 2026-02-02

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '20260202_alt_to_scen'
down_revision = '20260202_powell_allocation_tables'
branch_labels = None
depends_on = None


def upgrade():
    """Rename 'alternative' to 'scenario' throughout the database."""

    # ==========================================================================
    # PHASE 1: Rename core tables (alternative -> scenario)
    # ==========================================================================

    # Check if tables exist before renaming (handles fresh installs)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # Rename alternatives -> scenarios (if exists)
    if 'alternatives' in existing_tables:
        op.rename_table('alternatives', 'scenarios')

    # Rename alternative_rounds -> scenario_rounds (if exists)
    if 'alternative_rounds' in existing_tables:
        op.rename_table('alternative_rounds', 'scenario_rounds')

    # ==========================================================================
    # PHASE 2: Rename foreign key columns (alternative_id -> scenario_id)
    # ==========================================================================

    # scenario_rounds table (was alternative_rounds)
    if 'scenario_rounds' in existing_tables or 'alternative_rounds' in existing_tables:
        try:
            op.alter_column('scenario_rounds', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass  # Column may already be named scenario_id

    # participants table
    if 'participants' in existing_tables:
        try:
            op.alter_column('participants', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass

    # participant_rounds table
    if 'participant_rounds' in existing_tables:
        try:
            op.alter_column('participant_rounds', 'alternative_round_id', new_column_name='scenario_round_id')
        except Exception:
            pass

    # participant_actions table
    if 'participant_actions' in existing_tables:
        try:
            op.alter_column('participant_actions', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass

    # rounds table
    if 'rounds' in existing_tables:
        try:
            op.alter_column('rounds', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass

    # orders table
    if 'orders' in existing_tables:
        try:
            op.alter_column('orders', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass

    # round_metric table
    if 'round_metric' in existing_tables:
        try:
            op.alter_column('round_metric', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass

    # chat_messages table
    if 'chat_messages' in existing_tables:
        try:
            op.alter_column('chat_messages', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass

    # agent_suggestions table
    if 'agent_suggestions' in existing_tables:
        try:
            op.alter_column('agent_suggestions', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass

    # what_if_analyses table
    if 'what_if_analyses' in existing_tables:
        try:
            op.alter_column('what_if_analyses', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass

    # supervisor_actions table
    if 'supervisor_actions' in existing_tables:
        try:
            op.alter_column('supervisor_actions', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass

    # decision_proposals table
    if 'decision_proposals' in existing_tables:
        try:
            op.alter_column('decision_proposals', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass

    # transfer_order table
    if 'transfer_order' in existing_tables:
        try:
            op.alter_column('transfer_order', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass

    # purchase_order table
    if 'purchase_order' in existing_tables:
        try:
            op.alter_column('purchase_order', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass

    # supply_chain_configs table
    if 'supply_chain_configs' in existing_tables:
        try:
            op.alter_column('supply_chain_configs', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass

    # user_alternatives -> user_scenarios
    if 'user_alternatives' in existing_tables:
        try:
            op.alter_column('user_alternatives', 'alternative_id', new_column_name='scenario_id')
            op.rename_table('user_alternatives', 'user_scenarios')
        except Exception:
            pass

    # agent_configs table
    if 'agent_configs' in existing_tables:
        try:
            op.alter_column('agent_configs', 'alternative_id', new_column_name='scenario_id')
        except Exception:
            pass

    print("Alternative -> Scenario terminology migration completed!")


def downgrade():
    """Revert scenario terminology back to alternative."""

    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # Revert user_scenarios -> user_alternatives
    if 'user_scenarios' in existing_tables:
        try:
            op.alter_column('user_scenarios', 'scenario_id', new_column_name='alternative_id')
            op.rename_table('user_scenarios', 'user_alternatives')
        except Exception:
            pass

    # Revert columns in dependent tables
    tables_with_scenario_id = [
        'participants', 'participant_actions', 'rounds', 'orders',
        'round_metric', 'chat_messages', 'agent_suggestions',
        'what_if_analyses', 'supervisor_actions', 'decision_proposals',
        'transfer_order', 'purchase_order', 'supply_chain_configs',
        'agent_configs'
    ]

    for table in tables_with_scenario_id:
        if table in existing_tables:
            try:
                op.alter_column(table, 'scenario_id', new_column_name='alternative_id')
            except Exception:
                pass

    # Revert participant_rounds
    if 'participant_rounds' in existing_tables:
        try:
            op.alter_column('participant_rounds', 'scenario_round_id', new_column_name='alternative_round_id')
        except Exception:
            pass

    # Revert scenario_rounds -> alternative_rounds
    if 'scenario_rounds' in existing_tables:
        try:
            op.alter_column('scenario_rounds', 'scenario_id', new_column_name='alternative_id')
            op.rename_table('scenario_rounds', 'alternative_rounds')
        except Exception:
            pass

    # Revert scenarios -> alternatives
    if 'scenarios' in existing_tables:
        op.rename_table('scenarios', 'alternatives')

    print("Scenario -> Alternative terminology rollback completed!")
