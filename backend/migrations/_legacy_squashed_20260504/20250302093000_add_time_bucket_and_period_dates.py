"""add time bucket support and period dates"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20250302093000"
down_revision = "20250301093000"
branch_labels = None
depends_on = None

TIME_BUCKET_ENUM_NAME = "timebucketenum"


def upgrade() -> None:
    bind = op.get_bind()

    time_bucket_enum = sa.Enum("DAY", "WEEK", "MONTH", name=TIME_BUCKET_ENUM_NAME)
    time_bucket_enum.create(bind, checkfirst=True)
    time_bucket_col = sa.Column("time_bucket", time_bucket_enum, nullable=False, server_default="WEEK")

    op.add_column(
        "supply_chain_configs",
        time_bucket_col,
    )

    op.add_column(
        "games",
        sa.Column("time_bucket", sa.String(length=16), nullable=False, server_default="week"),
    )
    op.add_column(
        "games",
        sa.Column("start_date", sa.Date(), nullable=False, server_default=sa.text("'2025-01-06'")),
    )
    op.add_column(
        "games",
        sa.Column("current_period_start", sa.Date(), nullable=True),
    )

    op.add_column(
        "game_rounds",
        sa.Column("period_start", sa.Date(), nullable=True),
    )
    op.add_column(
        "game_rounds",
        sa.Column("period_end", sa.Date(), nullable=True),
    )

    # Initialize the new columns for existing rows
    op.execute("UPDATE supply_chain_configs SET time_bucket = 'WEEK' WHERE time_bucket IS NULL")
    op.execute("UPDATE games SET time_bucket = 'week' WHERE time_bucket IS NULL")
    op.execute("UPDATE games SET current_period_start = start_date WHERE current_round > 0")

    # Remove server defaults now that existing rows are populated
    op.alter_column("supply_chain_configs", "time_bucket", server_default=None)
    op.alter_column("games", "time_bucket", server_default=None)
    op.alter_column("games", "start_date", server_default=None)


def downgrade() -> None:
    op.drop_column("game_rounds", "period_end")
    op.drop_column("game_rounds", "period_start")

    op.drop_column("games", "current_period_start")
    op.drop_column("games", "start_date")
    op.drop_column("games", "time_bucket")

    op.drop_column("supply_chain_configs", "time_bucket")

    bind = op.get_bind()
    time_bucket_enum = sa.Enum(name=TIME_BUCKET_ENUM_NAME)
    time_bucket_enum.drop(bind, checkfirst=True)
