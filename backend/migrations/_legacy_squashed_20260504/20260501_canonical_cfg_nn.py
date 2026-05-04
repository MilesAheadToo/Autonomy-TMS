"""Mirror Core 0010: canonical entities config_id NOT NULL.

Revision ID: 20260501_canonical_cfg_nn
Revises: 20260501_plane_reg_wildcard
Create Date: 2026-05-01

Mirrors Core ``0010_canonical_entities_config_id_not_null``. Tightens
``config_id`` from nullable to NOT NULL on the 16 canonical AWS SC
entities. Audit 2026-05-01: every TMS row already has ``config_id``
populated (zero NULL across all 16 tables in this DB), so the
``ALTER COLUMN`` is the only operation; no backfill is needed.

Idempotent — guarded by ``information_schema.is_nullable`` checks.
Re-running is a no-op.

If a future fresh DB has NULL rows when this runs, the ALTER fails
loud — that's the desired SOC II posture (orphan rows are a data bug,
not a constraint to relax).
"""
from alembic import op
import sqlalchemy as sa


revision = "20260501_canonical_cfg_nn"
down_revision = "20260501_plane_reg_wildcard"
branch_labels = None
depends_on = None


# Same 16 tables as Core 0010.
CANONICAL_TABLES = (
    "product",
    "sourcing_rules",
    "inv_policy",
    "inv_level",
    "product_bom",
    "production_process",
    "supply_plan",
    "forecast",
    "reservation",
    "outbound_order",
    "outbound_order_line",
    "shipment",
    "inbound_order",
    "inbound_order_line",
    "backorder",
    "final_assembly_schedule",
)


def _table_exists(conn, table: str, schema: str = "public") -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :s AND table_name = :t"
            ),
            {"s": schema, "t": table},
        ).scalar()
    )


def _is_nullable(conn, table: str, column: str = "config_id", schema: str = "public") -> bool:
    return (
        conn.execute(
            sa.text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
            ),
            {"s": schema, "t": table, "c": column},
        ).scalar()
        == "YES"
    )


def upgrade() -> None:
    conn = op.get_bind()
    for tbl in CANONICAL_TABLES:
        if not _table_exists(conn, tbl):
            continue
        if not _is_nullable(conn, tbl):
            continue
        op.alter_column(tbl, "config_id", schema="public", nullable=False)


def downgrade() -> None:
    conn = op.get_bind()
    for tbl in CANONICAL_TABLES:
        if not _table_exists(conn, tbl):
            continue
        if _is_nullable(conn, tbl):
            continue
        op.alter_column(tbl, "config_id", schema="public", nullable=True)
