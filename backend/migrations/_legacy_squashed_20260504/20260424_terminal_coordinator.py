"""L2 Terminal Coordinator data plane (3 tables).

Creates:
  * terminal_urgency_override   — L2 → L1 urgency modulation
  * lane_waterfall_override     — L2 → L1 tender-depth cap
  * terminal_health_signal      — L2 → L3 hub-KPI append-only feed

All three tenant-scoped with RLS. See
docs/L2_TERMINAL_COORDINATOR_DESIGN.md §5 for the consumer contract.

Revision ID: 20260424_terminal_coordinator
Revises: (no dependency — idempotent)
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa


revision = "20260424_terminal_coordinator"
down_revision = None
branch_labels = None
depends_on = None


_TABLES = (
    "terminal_urgency_override",
    "lane_waterfall_override",
    "terminal_health_signal",
)


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = :n AND table_schema = 'public'"
        ),
        {"n": name},
    ).scalar())


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE tablename = '{table}'
              AND policyname = 'tenant_isolation_{table}'
          ) THEN
            CREATE POLICY tenant_isolation_{table}
              ON {table}
              USING (
                tenant_id = COALESCE(
                  NULLIF(current_setting('app.current_tenant_id', true), '')::int,
                  tenant_id
                )
                OR current_user = 'autonomy_admin'
              );
          END IF;
        END$$;
        """
    )


def upgrade() -> None:
    # ── terminal_urgency_override ───────────────────────────────────
    if not _table_exists("terminal_urgency_override"):
        op.create_table(
            "terminal_urgency_override",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("config_id", sa.Integer, sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("hub_site_id", sa.Integer, sa.ForeignKey("site.id", ondelete="CASCADE"), nullable=False),
            sa.Column("trm_type", sa.String(30), nullable=False),
            sa.Column("urgency_multiplier", sa.Float, nullable=False, server_default="1.0"),
            sa.Column("expires_at", sa.DateTime, nullable=False),
            sa.Column("reason", sa.Text, nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "ix_terminal_urgency_active",
            "terminal_urgency_override",
            ["tenant_id", "hub_site_id", "trm_type", "expires_at"],
        )
        _enable_rls("terminal_urgency_override")

    # ── lane_waterfall_override ─────────────────────────────────────
    if not _table_exists("lane_waterfall_override"):
        op.create_table(
            "lane_waterfall_override",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("config_id", sa.Integer, sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("hub_site_id", sa.Integer, sa.ForeignKey("site.id", ondelete="CASCADE"), nullable=False),
            sa.Column("lane_id", sa.Integer, sa.ForeignKey("transportation_lane.id", ondelete="CASCADE"), nullable=False),
            sa.Column("mode", sa.String(20), nullable=False),
            sa.Column("waterfall_depth", sa.SmallInteger, nullable=False),
            sa.Column("expires_at", sa.DateTime, nullable=False),
            sa.Column("reason", sa.Text, nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "ix_lane_waterfall_active",
            "lane_waterfall_override",
            ["tenant_id", "hub_site_id", "lane_id", "mode", "expires_at"],
        )
        _enable_rls("lane_waterfall_override")

    # ── terminal_health_signal ──────────────────────────────────────
    if not _table_exists("terminal_health_signal"):
        op.create_table(
            "terminal_health_signal",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("config_id", sa.Integer, sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("hub_site_id", sa.Integer, sa.ForeignKey("site.id", ondelete="CASCADE"), nullable=False),
            sa.Column("timestamp", sa.DateTime, nullable=False, server_default=sa.func.now()),
            sa.Column("composite_health", sa.Float, nullable=False),
            sa.Column("dock_utilization_pct", sa.Float, nullable=True),
            sa.Column("tender_reject_rate_1h", sa.Float, nullable=True),
            sa.Column("exception_backlog_count", sa.Integer, nullable=True),
            sa.Column("equipment_imbalance", sa.Float, nullable=True),
            sa.Column("sla_miss_rate_1h", sa.Float, nullable=True),
            sa.Column("trend_7d", sa.String(20), nullable=True),
            sa.Column("active_overrides_count", sa.SmallInteger, server_default="0"),
        )
        op.create_index(
            "ix_terminal_health_lookup",
            "terminal_health_signal",
            ["tenant_id", "hub_site_id", "timestamp"],
        )
        _enable_rls("terminal_health_signal")


def downgrade() -> None:
    for tbl in reversed(_TABLES):
        if _table_exists(tbl):
            op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE;")
