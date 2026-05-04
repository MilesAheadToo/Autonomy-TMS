"""Initial core schema managed by Alembic

Revision ID: 20240900120000
Revises:
Create Date: 2025-09-01 12:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20240900120000"
down_revision = None
branch_labels = None
depends_on = None


def _utc_now() -> sa.text:
    return sa.text("CURRENT_TIMESTAMP")


def _create_enum_idempotent(name: str, values: tuple[str, ...]) -> None:
    """Create a PostgreSQL ENUM type idempotently.

    `sa.Enum(...).create(bind, checkfirst=True)` has a historical bug
    where subsequent `op.create_table` calls that reference the same
    enum name (even with `create_type=False` on the Column) re-emit
    `CREATE TYPE` and fail on the second run. Using a raw DO-block
    bypasses the SQLAlchemy side of that misbehaviour.
    """
    values_sql = ", ".join(f"'{v}'" for v in values)
    op.execute(
        f"""
        DO $$ BEGIN
            CREATE TYPE {name} AS ENUM ({values_sql});
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END $$;
        """
    )


def upgrade() -> None:
    # Create enum types idempotently via raw DO blocks, then reference
    # them from columns with postgresql.ENUM(create_type=False) so
    # op.create_table never tries to CREATE TYPE on its own.
    _create_enum_idempotent(
        "player_role_enum",
        ("RETAILER", "WHOLESALER", "DISTRIBUTOR", "MANUFACTURER"),
    )
    _create_enum_idempotent("player_type_enum", ("HUMAN", "AI"))
    _create_enum_idempotent(
        "player_strategy_enum",
        (
            "MANUAL", "RANDOM", "FIXED", "DEMAND_AVERAGE", "TREND_FOLLOWER",
            "LLM_BASIC", "LLM_ADVANCED", "LLM_REINFORCEMENT",
        ),
    )
    _create_enum_idempotent(
        "game_status_enum",
        ("CREATED", "STARTED", "ROUND_IN_PROGRESS", "ROUND_COMPLETED", "FINISHED"),
    )

    player_role_enum = postgresql.ENUM(
        "RETAILER", "WHOLESALER", "DISTRIBUTOR", "MANUFACTURER",
        name="player_role_enum", create_type=False,
    )
    player_type_enum = postgresql.ENUM(
        "HUMAN", "AI",
        name="player_type_enum", create_type=False,
    )
    player_strategy_enum = postgresql.ENUM(
        "MANUAL", "RANDOM", "FIXED", "DEMAND_AVERAGE", "TREND_FOLLOWER",
        "LLM_BASIC", "LLM_ADVANCED", "LLM_REINFORCEMENT",
        name="player_strategy_enum", create_type=False,
    )
    game_status_enum = postgresql.ENUM(
        "CREATED", "STARTED", "ROUND_IN_PROGRESS", "ROUND_COMPLETED", "FINISHED",
        name="game_status_enum", create_type=False,
    )

    # Users ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=50), nullable=True, unique=True),
        sa.Column("email", sa.String(length=100), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("roles", sa.JSON(), nullable=True),
        sa.Column("last_login", sa.DateTime(), nullable=True),
        sa.Column(
            "last_password_change",
            sa.DateTime(),
            nullable=False,
            server_default=_utc_now(),
        ),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("mfa_secret", sa.String(length=100), nullable=True),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_utc_now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=_utc_now(),
            server_onupdate=_utc_now(),
        ),
    )

    # Games ------------------------------------------------------------------
    op.create_table(
        "games",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("status", game_status_enum, nullable=False, server_default="CREATED"),
        sa.Column("current_round", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_rounds", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("demand_pattern", sa.JSON(), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("role_assignments", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_utc_now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=_utc_now(),
            server_onupdate=_utc_now(),
        ),
    )

    # Rounds -----------------------------------------------------------------
    op.create_table(
        "rounds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=_utc_now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=True),
    )

    # Players ----------------------------------------------------------------
    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("role", player_role_enum, nullable=False),
        sa.Column("type", player_type_enum, nullable=False, server_default="HUMAN"),
        sa.Column("strategy", player_strategy_enum, nullable=False, server_default="MANUAL"),
        sa.Column("is_ai", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("ai_strategy", sa.String(length=50), nullable=True),
        sa.Column("can_see_demand", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("llm_model", sa.String(length=100), nullable=True, server_default="gpt-4o-mini"),
        sa.Column("inventory", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("backlog", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_ready", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("last_order", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_utc_now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=_utc_now(),
            server_onupdate=_utc_now(),
        ),
    )

    # Player inventory -------------------------------------------------------
    op.create_table(
        "player_inventory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("current_stock", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("incoming_shipments", sa.JSON(), nullable=True),
        sa.Column("backorders", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost", sa.Float(), nullable=False, server_default=sa.text("0")),
    )

    # Game rounds ------------------------------------------------------------
    op.create_table(
        "game_rounds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("customer_demand", sa.Integer(), nullable=False),
        sa.Column("is_completed", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("is_processed", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_utc_now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )

    # Player rounds ----------------------------------------------------------
    op.create_table(
        "player_rounds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("round_id", sa.Integer(), sa.ForeignKey("game_rounds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_placed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("order_received", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("inventory_before", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("inventory_after", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("backorders_before", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("backorders_after", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("holding_cost", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("backorder_cost", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_cost", sa.Float(), nullable=False, server_default=sa.text("0")),
    )

    # Player actions ---------------------------------------------------------
    op.create_table(
        "player_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("round_id", sa.Integer(), sa.ForeignKey("rounds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_type", sa.String(length=50), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_utc_now()),
    )

    # Orders -----------------------------------------------------------------
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_utc_now()),
    )

    # Agent configs & supervisor actions ------------------------------------
    op.create_table(
        "agent_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("agent_type", sa.String(length=50), nullable=False),
        sa.Column("config", sa.JSON(), nullable=True),
    )

    op.create_table(
        "supervisor_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("original_order", sa.Integer(), nullable=False),
        sa.Column("adjusted_order", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=100), nullable=False),
        sa.Column("bullwhip_metric", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_utc_now()),
    )

    # Association tables -----------------------------------------------------
    op.create_table(
        "user_games",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), primary_key=True, nullable=False),
    )

    # Auth/supporting tables -------------------------------------------------
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String(length=500), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_utc_now()),
    )

    op.create_table(
        "password_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hashed_password", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_utc_now()),
    )

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String(length=100), nullable=False, unique=True),
        sa.Column("is_used", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_utc_now()),
    )

    op.create_table(
        "token_blacklist",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("jti", sa.String(length=36), nullable=False, unique=True),
        sa.Column("token", sa.String(length=500), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_utc_now()),
    )

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_jti", sa.String(length=36), nullable=False, unique=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_activity", sa.DateTime(), nullable=False, server_default=_utc_now()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_utc_now()),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_table("user_sessions")
    op.drop_table("token_blacklist")
    op.drop_table("password_reset_tokens")
    op.drop_table("password_history")
    op.drop_table("refresh_tokens")
    op.drop_table("user_games")
    op.drop_table("supervisor_actions")
    op.drop_table("agent_configs")
    op.drop_table("orders")
    op.drop_table("player_actions")
    op.drop_table("player_rounds")
    op.drop_table("game_rounds")
    op.drop_table("player_inventory")
    op.drop_table("players")
    op.drop_table("rounds")
    op.drop_table("games")
    op.drop_table("users")

    game_status_enum = sa.Enum(name="game_status_enum")
    game_status_enum.drop(bind, checkfirst=True)

    player_strategy_enum = sa.Enum(name="player_strategy_enum")
    player_strategy_enum.drop(bind, checkfirst=True)

    player_type_enum = sa.Enum(name="player_type_enum")
    player_type_enum.drop(bind, checkfirst=True)

    player_role_enum = sa.Enum(name="player_role_enum")
    player_role_enum.drop(bind, checkfirst=True)
