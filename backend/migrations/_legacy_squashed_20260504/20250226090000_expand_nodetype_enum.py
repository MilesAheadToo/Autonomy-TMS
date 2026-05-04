"""Expand nodetype enum to cover market nodes

Revision ID: 20250226090000
Revises: 20241101090000
Create Date: 2025-09-26 09:00:00.000000

Originally written against MySQL (`ALTER TABLE … MODIFY COLUMN type ENUM(…)`).
Rewritten 2026-04-23 to be Postgres-native: adds the two new enum labels
`MARKET_SUPPLY` and `MARKET_DEMAND` to the existing `nodetype` enum via
`ALTER TYPE … ADD VALUE`, guarded by `pg_enum` lookups so it is idempotent
and safe against green-field DBs where the enum was created with the full
set of labels already.
"""
from alembic import op
import sqlalchemy as sa


revision = "20250226090000"
down_revision = "20241101090000"
branch_labels = None
depends_on = None


def _enum_has_value(enum_name: str, value: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text(
            "SELECT 1 FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = :t AND e.enumlabel = :v"
        ),
        {"t": enum_name, "v": value},
    ).scalar())


def _enum_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :n"),
        {"n": name},
    ).scalar())


def upgrade() -> None:
    if not _enum_exists("nodetype"):
        return

    for label in ("MARKET_SUPPLY", "MARKET_DEMAND"):
        if not _enum_has_value("nodetype", label):
            op.execute(sa.text(f"ALTER TYPE nodetype ADD VALUE IF NOT EXISTS '{label}'"))


def downgrade() -> None:
    # Postgres does not support removing enum values without full type
    # recreation. This migration is forward-only.
    pass
