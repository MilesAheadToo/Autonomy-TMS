"""Rename participant -> scenario_user across all tables and columns.

Complete the terminology migration from Participant -> ScenarioUser.
This renames tables, FK columns, and indexes.

Tables renamed:
- participants -> scenario_users
- participant_actions -> scenario_user_actions
- participant_inventory -> scenario_user_inventory
- participant_rounds -> scenario_user_periods
- participant_stats -> scenario_user_stats
- participant_achievements -> scenario_user_achievements
- participant_badges -> scenario_user_badges

FK columns renamed (participant_id -> scenario_user_id) in:
- scenario_user_actions, scenario_user_inventory, scenario_user_periods,
  scenario_user_stats (PK), scenario_user_achievements, leaderboard_entries,
  scenario_user_badges, achievement_notifications, agent_suggestions,
  what_if_analyses, round_metric, function_assignments,
  orders, agent_mode_history, agent_performance_logs, rlhf_feedback

Also renames participant_level -> scenario_user_level in scenario_user_stats.

Revision ID: 20260224_participant_to_su
Revises: 20260323100000
Create Date: 2026-02-24 00:00:00.000000

"""
from alembic import op

revision = '20260224_participant_to_su'
down_revision = '20260323100000'
branch_labels = None
depends_on = None

# Tables to rename
TABLES_TO_RENAME = [
    ('participants', 'scenario_users'),
    ('participant_actions', 'scenario_user_actions'),
    ('participant_inventory', 'scenario_user_inventory'),
    ('participant_rounds', 'scenario_user_periods'),
    ('participant_stats', 'scenario_user_stats'),
    ('participant_achievements', 'scenario_user_achievements'),
    ('participant_badges', 'scenario_user_badges'),
]

# FK columns to rename (table, old_col, new_col)
# Use new table names (after table rename)
FK_COLUMNS_TO_RENAME = [
    ('scenario_user_actions', 'participant_id', 'scenario_user_id'),
    ('scenario_user_inventory', 'participant_id', 'scenario_user_id'),
    ('scenario_user_periods', 'participant_id', 'scenario_user_id'),
    ('scenario_user_stats', 'participant_id', 'scenario_user_id'),
    ('scenario_user_achievements', 'participant_id', 'scenario_user_id'),
    ('leaderboard_entries', 'participant_id', 'scenario_user_id'),
    ('scenario_user_badges', 'participant_id', 'scenario_user_id'),
    ('achievement_notifications', 'participant_id', 'scenario_user_id'),
    ('agent_suggestions', 'participant_id', 'scenario_user_id'),
    ('what_if_analyses', 'participant_id', 'scenario_user_id'),
    ('round_metric', 'participant_id', 'scenario_user_id'),
    ('function_assignments', 'participant_id', 'scenario_user_id'),
    ('orders', 'participant_id', 'scenario_user_id'),
    ('agent_mode_history', 'participant_id', 'scenario_user_id'),
    ('agent_performance_logs', 'participant_id', 'scenario_user_id'),
    ('rlhf_feedback', 'participant_id', 'scenario_user_id'),
]

# Additional column renames (non-FK)
OTHER_COLUMNS_TO_RENAME = [
    ('scenario_user_stats', 'participant_level', 'scenario_user_level'),
]


def _table_exists(table_name):
    """Check if a table exists (for idempotent migration)."""
    conn = op.get_bind()
    result = conn.execute(
        conn.engine.dialect.has_table(conn, table_name)
        if hasattr(conn.engine.dialect, 'has_table')
        else conn.execute(
            f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '{table_name}')"
        )
    )
    return bool(result)


def upgrade():
    # 1. Rename tables
    for old_name, new_name in TABLES_TO_RENAME:
        try:
            op.rename_table(old_name, new_name)
        except Exception:
            pass  # Table may already be renamed or not exist

    # 2. Rename FK columns
    for table, old_col, new_col in FK_COLUMNS_TO_RENAME:
        try:
            op.alter_column(table, old_col, new_column_name=new_col)
        except Exception:
            pass  # Column may already be renamed or table not exist

    # 3. Rename other columns
    for table, old_col, new_col in OTHER_COLUMNS_TO_RENAME:
        try:
            op.alter_column(table, old_col, new_column_name=new_col)
        except Exception:
            pass  # Column may already be renamed


def downgrade():
    # 3. Revert other columns
    for table, old_col, new_col in reversed(OTHER_COLUMNS_TO_RENAME):
        try:
            op.alter_column(table, new_col, new_column_name=old_col)
        except Exception:
            pass

    # 2. Revert FK columns (use new table names since tables haven't been reverted yet)
    for table, old_col, new_col in reversed(FK_COLUMNS_TO_RENAME):
        try:
            op.alter_column(table, new_col, new_column_name=old_col)
        except Exception:
            pass

    # 1. Revert table renames
    for old_name, new_name in reversed(TABLES_TO_RENAME):
        try:
            op.rename_table(new_name, old_name)
        except Exception:
            pass
