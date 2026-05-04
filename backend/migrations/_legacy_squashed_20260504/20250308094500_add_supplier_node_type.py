"""add supplier node type

Revision ID: 20250308094500_add_supplier_node_type
Revises: 20250306090000
Create Date: 2025-03-08 09:45:00.000000

Originally MySQL-native (`ALTER TABLE … MODIFY COLUMN type ENUM(…)` +
`CONCAT` / `SUBSTRING_INDEX`). Rewritten 2026-04-23 for Postgres:
- `ALTER TYPE nodetype ADD VALUE IF NOT EXISTS 'SUPPLIER'` (idempotent).
- Replaced MySQL string functions with Postgres equivalents.
- All writes guarded so the migration is a safe no-op against green-field DBs.
"""
from alembic import op
import sqlalchemy as sa


revision = "20250308094500_add_supplier_node_type"
down_revision = "20250306090000"
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


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = :n AND table_schema = 'public'"
        ),
        {"n": name},
    ).scalar())


def upgrade() -> None:
    # Widen alembic_version.version_num so subsequent long revision IDs fit.
    # Originally handled only for MySQL; Postgres needs it too because later
    # migrations use long underscore-suffixed IDs.
    op.execute(sa.text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)"))

    if _enum_exists("nodetype") and not _enum_has_value("nodetype", "SUPPLIER"):
        op.execute(sa.text("ALTER TYPE nodetype ADD VALUE IF NOT EXISTS 'SUPPLIER'"))

    # Port the MySQL name-rewrite to Postgres. Guarded: Postgres forbids
    # USING a just-added enum value in the same transaction
    # (UnsafeNewEnumValueUsage), so the rewrite only runs when there are
    # legacy rows to migrate. On a green-field DB the table is empty and
    # the UPDATE is skipped entirely.
    conn = op.get_bind()
    if _table_exists("nodes"):
        row_count = conn.execute(sa.text(
            "SELECT COUNT(*) FROM nodes WHERE type::text = 'MARKET_SUPPLY' AND name LIKE 'Supplier %'"
        )).scalar() or 0
        if row_count > 0:
            op.execute(sa.text(
                """
                UPDATE nodes
                SET
                    type = 'SUPPLIER',
                    name = 'Component Supplier ' ||
                           split_part(split_part(name, ' ', array_length(string_to_array(name, ' '), 1)), '-', 1) ||
                           '-' ||
                           lpad(split_part(name, '-', array_length(string_to_array(name, '-'), 1)), 2, '0')
                WHERE type::text = 'MARKET_SUPPLY'
                  AND name LIKE 'Supplier %'
                """
            ))

    if _table_exists("supply_chain_configs"):
        node_defs = (
            '[{"type":"market_demand","label":"Market Demand","order":0,"is_required":true},'
            '{"type":"distributor","label":"Distributor","order":1,"is_required":false},'
            '{"type":"manufacturer","label":"Manufacturer","order":2,"is_required":false},'
            '{"type":"wholesaler","label":"Wholesaler","order":3,"is_required":false},'
            '{"type":"market_demand","label":"Market Demand","order":4,"is_required":true}]'
        )
        op.execute(
            sa.text(
                "UPDATE supply_chain_configs "
                "SET node_type_definitions = :defs "
                "WHERE name = 'Complex_SC'"
            ).bindparams(defs=node_defs)
        )


def downgrade() -> None:
    # Forward-only. Postgres cannot remove enum labels without recreating the
    # type, and the row-level inverse rewrite is lossy.
    pass
