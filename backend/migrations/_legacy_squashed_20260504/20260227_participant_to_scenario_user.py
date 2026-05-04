"""Rename participant_id → scenario_user_id and update user_type_enum.

1. Rename participant_id column to scenario_user_id across 22 tables
2. Replace GROUP_ADMIN with TENANT_ADMIN in user_type_enum

Fully idempotent — safe to run even if parts were already applied.

Revision ID: 20260227_participant_rename
Revises: 20260227_drop_groups
Create Date: 2026-02-27 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260227_participant_rename'
down_revision = '20260227_drop_groups'
branch_labels = None
depends_on = None

# Tables that had participant_id renamed to scenario_user_id
PARTICIPANT_TABLES = [
    'achievement_notifications',
    'agent_mode_history',
    'agent_performance_logs',
    'agent_suggestions',
    'function_assignments',
    'leaderboard_entries',
    'orders',
    'participant_achievements',
    'participant_actions',
    'participant_badges',
    'participant_inventory',
    'participant_rounds',
    'participant_stats',
    'rlhf_feedback',
    'round_metric',
    'scenario_user_achievements',
    'scenario_user_actions',
    'scenario_user_badges',
    'scenario_user_inventory',
    'scenario_user_periods',
    'scenario_user_stats',
    'what_if_analyses',
]


def _column_exists(table_name, column_name):
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :tbl AND column_name = :col)"
    ), {"tbl": table_name, "col": column_name})
    return result.scalar()


def _table_exists(table_name):
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = :tbl)"
    ), {"tbl": table_name})
    return result.scalar()


def upgrade():
    conn = op.get_bind()

    # ================================================================== #
    # STEP 1: Rename participant_id → scenario_user_id
    # ================================================================== #
    for table_name in PARTICIPANT_TABLES:
        if _table_exists(table_name) and _column_exists(table_name, 'participant_id'):
            if _column_exists(table_name, 'scenario_user_id'):
                # Both exist — drop the old one
                try:
                    op.drop_column(table_name, 'participant_id')
                except Exception:
                    pass
            else:
                try:
                    op.alter_column(table_name, 'participant_id',
                                    new_column_name='scenario_user_id')
                except Exception:
                    pass

    # ================================================================== #
    # STEP 2: Update user_type_enum — add TENANT_ADMIN, migrate data,
    #         remove GROUP_ADMIN
    # ================================================================== #
    # Check if TENANT_ADMIN already exists in enum
    has_tenant_admin = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM pg_enum "
        "WHERE enumlabel = 'TENANT_ADMIN' AND enumtypid = "
        "(SELECT oid FROM pg_type WHERE typname = 'user_type_enum'))"
    )).scalar()

    if not has_tenant_admin:
        conn.execute(sa.text(
            "ALTER TYPE user_type_enum ADD VALUE IF NOT EXISTS 'TENANT_ADMIN'"
        ))

    # Migrate any GROUP_ADMIN users to TENANT_ADMIN
    has_group_admin = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM pg_enum "
        "WHERE enumlabel = 'GROUP_ADMIN' AND enumtypid = "
        "(SELECT oid FROM pg_type WHERE typname = 'user_type_enum'))"
    )).scalar()

    if has_group_admin:
        # Update users with GROUP_ADMIN to TENANT_ADMIN
        conn.execute(sa.text(
            "UPDATE users SET user_type = 'TENANT_ADMIN' WHERE user_type = 'GROUP_ADMIN'"
        ))

        # Cannot remove enum values in PostgreSQL directly.
        # Recreate the enum without GROUP_ADMIN.
        # Must drop column default first (it references the old enum type),
        # then recreate enum, alter column, and restore default.
        conn.execute(sa.text(
            "ALTER TABLE users ALTER COLUMN user_type DROP DEFAULT"
        ))
        conn.execute(sa.text(
            "ALTER TYPE user_type_enum RENAME TO user_type_enum_old"
        ))
        conn.execute(sa.text(
            "CREATE TYPE user_type_enum AS ENUM "
            "('SYSTEM_ADMIN', 'TENANT_ADMIN', 'USER', 'PLAYER')"
        ))
        conn.execute(sa.text(
            "ALTER TABLE users ALTER COLUMN user_type TYPE user_type_enum "
            "USING user_type::text::user_type_enum"
        ))
        conn.execute(sa.text(
            "ALTER TABLE users ALTER COLUMN user_type SET DEFAULT 'USER'::user_type_enum"
        ))
        conn.execute(sa.text("DROP TYPE user_type_enum_old"))


def downgrade():
    raise NotImplementedError(
        "Cannot downgrade: GROUP_ADMIN enum value was removed. "
        "Restore from database backup if needed."
    )
