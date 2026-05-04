"""Align core schema with current models

Revision ID: 20241020120000
Revises: 20241001120000
Create Date: 2025-10-20 12:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.exc import NoSuchTableError

# revision identifiers, used by Alembic.
revision = "20241020120000"
down_revision = "20241010120000"
branch_labels = None
depends_on = None


def _inspector() -> sa.engine.reflection.Inspector:
    return inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    insp = _inspector()
    if table_name not in insp.get_table_names():
        return False
    return column_name in {col["name"] for col in insp.get_columns(table_name)}


def _fk_exists(table_name: str, fk_name: str) -> bool:
    try:
        fks = _inspector().get_foreign_keys(table_name)
    except NoSuchTableError:
        return False
    return any(fk.get("name") == fk_name for fk in fks)


def _index_exists(table_name: str, index_name: str) -> bool:
    try:
        indexes = _inspector().get_indexes(table_name)
    except NoSuchTableError:
        return False
    return any(idx.get("name") == index_name for idx in indexes)


def _enum_user_type() -> sa.Enum:
    return sa.Enum(
        "SYSTEM_ADMIN", "GROUP_ADMIN", "PLAYER",
        name="user_type_enum",
    )


def _enum_player_type() -> sa.Enum:
    return sa.Enum("human", "ai", name="player_type_enum")


def _enum_player_strategy() -> sa.Enum:
    return sa.Enum(
        "manual",
        "random",
        "fixed",
        "demand_average",
        "trend_follower",
        "llm_basic",
        "llm_advanced",
        "llm_reinforcement",
        name="player_strategy_enum",
    )


def ensure_users_table() -> None:
    if _table_exists("users"):
        return

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=50), nullable=True, unique=True),
        sa.Column("email", sa.String(length=100), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("user_type", _enum_user_type(), nullable=False, server_default="PLAYER"),
        sa.Column("last_login", sa.DateTime(), nullable=True),
        sa.Column("last_password_change", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("mfa_secret", sa.String(length=100), nullable=True),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

def ensure_groups_table() -> None:
    ensure_users_table()

    if not _table_exists("groups"):
        op.create_table(
            "groups",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("logo", sa.String(length=255), nullable=True),
            sa.Column("admin_id", sa.Integer(), nullable=False, unique=True),
            sa.ForeignKeyConstraint(["admin_id"], ["users.id"], ondelete="CASCADE"),
        )

    if not _column_exists("users", "customer_id"):
        op.add_column("users", sa.Column("customer_id", sa.Integer(), nullable=True))
    if not _fk_exists("users", "fk_users_group"):
        op.create_foreign_key(
            "fk_users_group", "users", "groups", ["customer_id"], ["id"], ondelete="CASCADE"
        )


def ensure_refresh_tokens_table() -> None:
    ensure_users_table()

    if _table_exists("refresh_tokens"):
        if not _fk_exists("refresh_tokens", "fk_refresh_tokens_user"):
            op.create_foreign_key(
                "fk_refresh_tokens_user",
                "refresh_tokens",
                "users",
                ["user_id"],
                ["id"],
                ondelete="CASCADE",
            )
        return

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=500), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )


def ensure_user_games_table() -> None:
    ensure_users_table()
    ensure_games_table()

    if not _table_exists("user_games"):
        op.create_table(
            "user_games",
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("game_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id", "game_id"),
        )


def ensure_games_table() -> None:
    ensure_users_table()
    ensure_groups_table()

    if _table_exists("games"):
        if not _column_exists("games", "created_by"):
            op.add_column("games", sa.Column("created_by", sa.Integer(), nullable=True))
        if not _column_exists("games", "customer_id"):
            op.add_column("games", sa.Column("customer_id", sa.Integer(), nullable=True))
        if not _column_exists("games", "round_time_limit"):
            op.add_column("games", sa.Column("round_time_limit", sa.Integer(), nullable=False, server_default="60"))
        if not _column_exists("games", "current_round_ends_at"):
            op.add_column("games", sa.Column("current_round_ends_at", sa.DateTime(), nullable=True))
        return

    op.create_table(
        "games",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="created"),
        sa.Column("current_round", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_rounds", sa.Integer(), nullable=False, server_default="52"),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("demand_pattern", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("role_assignments", sa.JSON(), nullable=True),
        sa.Column("round_time_limit", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("current_round_ends_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["customer_id"], ["groups.id"], ondelete="CASCADE"),
    )
def ensure_players_table() -> None:
    ensure_users_table()
    ensure_games_table()

    if _table_exists("players"):
        return

    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("role", _enum_player_role(), nullable=False),
        sa.Column("type", _enum_player_type(), nullable=False, server_default="human"),
        sa.Column("strategy", _enum_player_strategy(), nullable=False, server_default="manual"),
        sa.Column("inventory", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("backlog", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_ready", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("can_see_demand", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("llm_model", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
    )
def ensure_rounds_table() -> None:
    ensure_games_table()

    if not _table_exists("rounds"):
        op.create_table(
            "rounds",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("game_id", sa.Integer(), nullable=False),
            sa.Column("round_number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("config", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        )


def ensure_player_actions_table() -> None:
    ensure_rounds_table()
    ensure_players_table()

    if not _table_exists("player_actions"):
        op.create_table(
            "player_actions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("game_id", sa.Integer(), nullable=False),
            sa.Column("round_id", sa.Integer(), nullable=False),
            sa.Column("player_id", sa.Integer(), nullable=False),
            sa.Column("action_type", sa.String(length=50), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["round_id"], ["rounds.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        )


def ensure_password_tables() -> None:
    ensure_users_table()

    if not _table_exists("password_history"):
        op.create_table(
            "password_history",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("hashed_password", sa.String(length=100), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )

    if not _table_exists("password_reset_tokens"):
        op.create_table(
            "password_reset_tokens",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("token", sa.String(length=100), nullable=False, unique=True),
            sa.Column("is_used", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )


def ensure_token_tables() -> None:
    if not _table_exists("token_blacklist"):
        op.create_table(
            "token_blacklist",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("jti", sa.String(length=36), nullable=False, unique=True),
            sa.Column("token", sa.String(length=500), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )

    ensure_users_table()
    if not _table_exists("user_sessions"):
        op.create_table(
            "user_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("token_jti", sa.String(length=36), nullable=False, unique=True),
            sa.Column("user_agent", sa.String(length=500), nullable=True),
            sa.Column("ip_address", sa.String(length=45), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("last_activity", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )


def ensure_agent_tables() -> None:
    ensure_games_table()

    if not _table_exists("agent_configs"):
        op.create_table(
            "agent_configs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("game_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=50), nullable=False),
            sa.Column("agent_type", sa.String(length=50), nullable=False),
            sa.Column("config", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        )

    if not _table_exists("supervisor_actions"):
        op.create_table(
            "supervisor_actions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("game_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("original_order", sa.Integer(), nullable=False),
            sa.Column("adjusted_order", sa.Integer(), nullable=False),
            sa.Column("reason", sa.String(length=100), nullable=False),
            sa.Column("bullwhip_metric", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        )


def ensure_supply_chain_tables() -> None:
    """Create legacy supply-chain gameplay tables required by services."""
    ensure_games_table()
    ensure_players_table()

    if not _table_exists("player_inventory"):
        op.create_table(
            "player_inventory",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("player_id", sa.Integer(), nullable=False, unique=True),
            sa.Column("current_stock", sa.Integer(), nullable=False, server_default="12"),
            sa.Column("incoming_shipments", sa.JSON(), nullable=True),
            sa.Column("backorders", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("cost", sa.Float(), nullable=False, server_default=sa.text("0")),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        )

    if not _table_exists("orders"):
        op.create_table(
            "orders",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("game_id", sa.Integer(), nullable=False),
            sa.Column("player_id", sa.Integer(), nullable=False),
            sa.Column("round_number", sa.Integer(), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        )

    if not _table_exists("game_rounds"):
        op.create_table(
            "game_rounds",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("game_id", sa.Integer(), nullable=False),
            sa.Column("round_number", sa.Integer(), nullable=False),
            sa.Column("customer_demand", sa.Integer(), nullable=False),
            sa.Column("is_completed", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        )

    if not _table_exists("player_rounds"):
        op.create_table(
            "player_rounds",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("player_id", sa.Integer(), nullable=False),
            sa.Column("round_id", sa.Integer(), nullable=False),
            sa.Column("order_placed", sa.Integer(), nullable=False),
            sa.Column("order_received", sa.Integer(), nullable=False),
            sa.Column("inventory_before", sa.Integer(), nullable=False),
            sa.Column("inventory_after", sa.Integer(), nullable=False),
            sa.Column("backorders_before", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("backorders_after", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("holding_cost", sa.Float(), nullable=False, server_default=sa.text("0")),
            sa.Column("backorder_cost", sa.Float(), nullable=False, server_default=sa.text("0")),
            sa.Column("total_cost", sa.Float(), nullable=False, server_default=sa.text("0")),
            sa.Column("comment", sa.String(length=255), nullable=True),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["round_id"], ["game_rounds.id"], ondelete="CASCADE"),
        )

    if not _table_exists("products"):
        op.create_table(
            "products",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("unit_cost", sa.Float(), nullable=False, server_default=sa.text("0")),
        )

    if not _table_exists("simulation_runs"):
        op.create_table(
            "simulation_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("start_time", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("end_time", sa.DateTime(), nullable=True),
            sa.Column("parameters", sa.JSON(), nullable=True),
        )

    if not _table_exists("simulation_steps"):
        op.create_table(
            "simulation_steps",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("simulation_run_id", sa.Integer(), nullable=False),
            sa.Column("step_number", sa.Integer(), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("state", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(["simulation_run_id"], ["simulation_runs.id"], ondelete="CASCADE"),
        )


def backfill_defaults() -> None:
    bind = op.get_bind()

    # Fresh-install guard: if the tables are empty (green-field migration run),
    # backfill UPDATEs serve no purpose and trigger PG enum-cast quirks for
    # values that don't yet exist. Skip entirely in that case.
    if _table_exists("users"):
        row_count = bind.execute(text("SELECT COUNT(*) FROM users")).scalar() or 0
        if row_count == 0:
            return

    if _column_exists("users", "user_type"):
        bind.execute(
            text(
                "UPDATE users SET user_type = (CASE WHEN is_superuser THEN 'SYSTEM_ADMIN' ELSE 'PLAYER' END)::user_type_enum "
                "WHERE user_type IS NULL OR user_type::text = ''"
            )
        )

    if _column_exists("users", "last_password_change"):
        bind.execute(
            text(
                "UPDATE users SET last_password_change = CURRENT_TIMESTAMP "
                "WHERE last_password_change IS NULL"
            )
        )

    if _column_exists("users", "failed_login_attempts"):
        bind.execute(
            text(
                "UPDATE users SET failed_login_attempts = 0 WHERE failed_login_attempts IS NULL"
            )
        )

    if _column_exists("users", "mfa_enabled"):
        bind.execute(text("UPDATE users SET mfa_enabled = false WHERE mfa_enabled IS NULL"))

    if _column_exists("players", "type"):
        bind.execute(
            text(
                "UPDATE players SET type = (CASE WHEN is_ai THEN 'ai' ELSE 'human' END)::player_type_enum "
                "WHERE type IS NULL"
            )
        )

    if _column_exists("players", "strategy"):
        bind.execute(
            text("UPDATE players SET strategy = 'manual'::player_strategy_enum WHERE strategy IS NULL OR strategy::text = ''")
        )

    for numeric_column in ("inventory", "backlog", "cost"):
        if _column_exists("players", numeric_column):
            bind.execute(
                text(
                    f"UPDATE players SET {numeric_column} = 0 WHERE {numeric_column} IS NULL"
                )
            )

    if _column_exists("players", "is_ready"):
        bind.execute(text("UPDATE players SET is_ready = false WHERE is_ready IS NULL"))

    if _column_exists("players", "can_see_demand"):
        bind.execute(text("UPDATE players SET can_see_demand = false WHERE can_see_demand IS NULL"))

    if _column_exists("players", "llm_model"):
        bind.execute(
            text(
                "UPDATE players SET llm_model = 'gpt-4o-mini' "
                "WHERE llm_model IS NULL OR llm_model = ''"
            )
        )

    if _column_exists("games", "config"):
        bind.execute(text("UPDATE games SET config = '{}' WHERE config IS NULL"))

    if _column_exists("games", "role_assignments"):
        bind.execute(text("UPDATE games SET role_assignments = '{}' WHERE role_assignments IS NULL"))

    if _column_exists("games", "is_public"):
        bind.execute(text("UPDATE games SET is_public = false WHERE is_public IS NULL"))

    if _column_exists("games", "round_time_limit"):
        bind.execute(
            text(
                "UPDATE games SET round_time_limit = 60 "
                "WHERE round_time_limit IS NULL OR round_time_limit = 0"
            )
        )


def upgrade() -> None:
    ensure_users_table()
    ensure_groups_table()
    ensure_refresh_tokens_table()
    ensure_games_table()
    ensure_players_table()
    ensure_rounds_table()
    ensure_player_actions_table()
    ensure_user_games_table()
    ensure_password_tables()
    ensure_token_tables()
    ensure_agent_tables()
    ensure_supply_chain_tables()
    backfill_defaults()


def downgrade() -> None:
    # Drop tables added in upgrade; skip core tables to avoid data loss.
    for table in (
        "player_rounds",
        "player_actions",
        "orders",
        "player_inventory",
        "rounds",
        "game_rounds",
        "supervisor_actions",
        "agent_configs",
        "simulation_steps",
        "simulation_runs",
        "products",
        "user_sessions",
        "token_blacklist",
        "password_reset_tokens",
        "password_history",
    ):
        if _table_exists(table):
            op.drop_table(table)

    if _table_exists("user_games"):
        op.drop_table("user_games")

    if _table_exists("refresh_tokens"):
        op.drop_table("refresh_tokens")

    if _table_exists("players"):
        for index_name in ("idx_players_game_role", "idx_players_game_user"):
            if _index_exists("players", index_name):
                op.drop_index(index_name, table_name="players")

    if _table_exists("games"):
        if _fk_exists("games", "fk_games_group"):
            op.drop_constraint("fk_games_group", "games", type_="foreignkey")
        if _fk_exists("games", "fk_games_created_by_users"):
            op.drop_constraint("fk_games_created_by_users", "games", type_="foreignkey")

    if _column_exists("users", "customer_id") and _fk_exists("users", "fk_users_group"):
        op.drop_constraint("fk_users_group", "users", type_="foreignkey")

    if _table_exists("groups"):
        op.drop_table("groups")
