"""Expand player role enum for supply chain node types

Revision ID: 20251113094500
Revises: 20251111090000_add_node_key_to_players
Create Date: 2025-11-13 10:15:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20251113094500"
down_revision = "20251111090000_add_node_key_to_players"
branch_labels = None
depends_on = None

NEW_VALUES = ("SUPPLIER", "MARKET_SUPPLY", "MARKET_DEMAND")
ALL_VALUES = (
    "RETAILER",
    "WHOLESALER",
    "DISTRIBUTOR",
    "MANUFACTURER",
    "SUPPLIER",
    "MARKET_SUPPLY",
    "MARKET_DEMAND",
)

def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Guard: `playerrole` enum may not exist (e.g. green-field where
        # the enum was named differently or is created later by a
        # subsequent migration). Skip silently if absent.
        enum_present = bind.execute(
            sa.text("SELECT 1 FROM pg_type WHERE typname = 'playerrole'")
        ).scalar()
        if not enum_present:
            return
        for value in NEW_VALUES:
            op.execute(
                sa.text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM pg_type t
                            JOIN pg_enum e ON t.oid = e.enumtypid
                            WHERE t.typname = 'playerrole' AND e.enumlabel = :label
                        ) THEN
                            ALTER TYPE playerrole ADD VALUE :label;
                        END IF;
                    END$$;
                    """
                ).bindparams(label=value)
            )
    elif dialect == "mysql":
        enum_values = ",".join(f"'{val}'" for val in ALL_VALUES)
        op.execute(
            sa.text(
                f"ALTER TABLE players MODIFY COLUMN role ENUM({enum_values}) NOT NULL"
            )
        )
    else:
        # Other dialects store enums as text; no action required
        pass


def downgrade() -> None:
    # Downgrades are intentionally left as no-ops because removing enum values
    # risks data loss once rows use the expanded set.
    pass
