"""Add ``tenant_id`` to TMS Powell decision tables and tighten to NOT NULL.

Revision ID: 20260501_powell_tid_nn
Revises: 20260501_ext_signal_creds
Create Date: 2026-05-01

Mirrors Core ``0013_powell_tenant_id_not_null`` but with a TMS-specific
shape: TMS Powell decision tables live in the ``public`` schema (Core's
are in ``agents``), and TMS DB does **not** yet have a ``tenant_id``
column on these tables at all (Core's existed-but-nullable; TMS's are
missing entirely). So this is ADD COLUMN + backfill + NOT NULL, not
just NOT NULL.

Affected tables (12 with config_id FK + 1 special case):

  ┌──────────────────────────────────────┬───────────┬──────────────────┐
  │ Table                                │ has FK?   │ Backfill source  │
  ├──────────────────────────────────────┼───────────┼──────────────────┤
  │ powell_atp_decisions                 │ config_id │ supply_chain_configs │
  │ powell_buffer_decisions              │ config_id │ supply_chain_configs │
  │ powell_forecast_adjustment_decisions │ config_id │ supply_chain_configs │
  │ powell_forecast_baseline_decisions   │ config_id │ supply_chain_configs │
  │ powell_maintenance_decisions         │ config_id │ supply_chain_configs │
  │ powell_mo_decisions                  │ config_id │ supply_chain_configs │
  │ powell_po_decisions                  │ config_id │ supply_chain_configs │
  │ powell_policy_parameters             │ config_id │ supply_chain_configs │
  │ powell_quality_decisions             │ config_id │ supply_chain_configs │
  │ powell_rebalance_decisions           │ config_id │ supply_chain_configs │
  │ powell_subcontracting_decisions      │ config_id │ supply_chain_configs │
  │ powell_to_decisions                  │ config_id │ supply_chain_configs │
  │ powell_site_agent_decisions          │ none      │ (currently empty —   │
  │                                      │           │  nullable for now;   │
  │                                      │           │  tighten via TMS-#3) │
  └──────────────────────────────────────┴───────────┴──────────────────┘

The 12 config_id-bearing tables get the same three-step pattern as Core
0013: ADD COLUMN nullable → backfill from supply_chain_configs → SET
NOT NULL. The 13th (``powell_site_agent_decisions``) has no config_id
FK; it has a ``site_key`` string instead. Since the table is empty in
this DB and the site_key→tenant_id resolution has no canonical mapping
yet, the column is added nullable. A follow-up migration will tighten
once the resolution lives somewhere (TMS-#3 candidate).

Idempotent: ADD COLUMN guarded by ``information_schema.columns``;
backfill matches only NULL rows; NOT NULL skipped if already enforced.
Re-running is a no-op.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260501_powell_tid_nn"
down_revision = "20260501_ext_signal_creds"
branch_labels = None
depends_on = None


# Tables that get the full ADD + backfill + NOT NULL treatment.
POWELL_TABLES_WITH_CONFIG = (
    "powell_atp_decisions",
    "powell_buffer_decisions",
    "powell_forecast_adjustment_decisions",
    "powell_forecast_baseline_decisions",
    "powell_maintenance_decisions",
    "powell_mo_decisions",
    "powell_po_decisions",
    "powell_policy_parameters",
    "powell_quality_decisions",
    "powell_rebalance_decisions",
    "powell_subcontracting_decisions",
    "powell_to_decisions",
)

# TMS-specific table without a config_id FK. ADD COLUMN nullable;
# defer NOT NULL until site_key→tenant_id resolution is wired.
POWELL_TABLES_NO_CONFIG = (
    "powell_site_agent_decisions",
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


def _column_exists(conn, table: str, column: str, schema: str = "public") -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
            ),
            {"s": schema, "t": table, "c": column},
        ).scalar()
    )


def _is_nullable(conn, table: str, column: str = "tenant_id", schema: str = "public") -> bool:
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

    # ── Tables with config_id ─────────────────────────────────────
    for tbl in POWELL_TABLES_WITH_CONFIG:
        if not _table_exists(conn, tbl):
            continue

        # 1. ADD COLUMN nullable (skipped if already present).
        if not _column_exists(conn, tbl, "tenant_id"):
            op.add_column(
                tbl,
                sa.Column(
                    "tenant_id",
                    sa.Integer(),
                    sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                    nullable=True,
                    index=True,
                ),
            )

        # 2. Idempotent backfill from supply_chain_configs.
        conn.execute(
            sa.text(
                f"""
                UPDATE {tbl} t
                   SET tenant_id = scc.tenant_id
                  FROM supply_chain_configs scc
                 WHERE t.config_id = scc.id
                   AND t.tenant_id IS NULL
                """
            )
        )

        # 3. Tighten to NOT NULL. Fails loud if any row is still NULL
        # (orphan, no matching supply_chain_configs row).
        if _is_nullable(conn, tbl):
            op.alter_column(tbl, "tenant_id", schema="public", nullable=False)

    # ── Tables without config_id (powell_site_agent_decisions) ────
    # ADD COLUMN nullable only. NOT NULL deferred — the resolution
    # path (site_key → site_id → config_id → tenant_id) needs to be
    # wired in TMS code first; tracked as a TMS-#3 follow-up.
    for tbl in POWELL_TABLES_NO_CONFIG:
        if not _table_exists(conn, tbl):
            continue
        if not _column_exists(conn, tbl, "tenant_id"):
            op.add_column(
                tbl,
                sa.Column(
                    "tenant_id",
                    sa.Integer(),
                    sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                    nullable=True,
                    index=True,
                ),
            )


def downgrade() -> None:
    """Drop the tenant_id column. Does not preserve any data."""
    conn = op.get_bind()
    for tbl in POWELL_TABLES_WITH_CONFIG + POWELL_TABLES_NO_CONFIG:
        if not _table_exists(conn, tbl):
            continue
        if _column_exists(conn, tbl, "tenant_id"):
            op.drop_column(tbl, "tenant_id")
