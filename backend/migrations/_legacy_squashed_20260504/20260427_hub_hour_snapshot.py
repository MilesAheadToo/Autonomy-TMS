"""L2 Phase-2 hub_hour_snapshot table — GATv2-ready training substrate.

Per docs/L2_TERMINAL_COORDINATOR_DESIGN.md §6 Phase 2: build the data
substrate the future GATv2+GRU agent (Phase 3) will train on. One row
per (tenant, config, hub, hour) — append-only time-series.

Three JSONB feature blobs:
  * node_features  — per-resource features (dock, lane, equipment,
                     carrier, shipment_queue, TRM_agent)
  * edge_features  — per-edge features keyed by edge_type (sparse)
  * hub_summary    — quick-access scalar KPIs mirroring
                     terminal_health_signal columns

Plus:
  * policy_snapshot — JSONB snapshot of active PolicyParameters at
                      extraction time (so the trained agent learns
                      policy-conditioned actions even when policy
                      changes mid-history).
  * source — provenance: "live" / "twin" / "twin_<scenario>" / "manual".

Revision ID: 20260427_hub_hour_snap
Revises: (no dependency — idempotent guards)
Create Date: 2026-04-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260427_hub_hour_snap"
down_revision = None
branch_labels = None
depends_on = None


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
    if _table_exists("hub_hour_snapshot"):
        return

    op.create_table(
        "hub_hour_snapshot",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id", sa.Integer,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "config_id", sa.Integer,
            sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "hub_site_id", sa.Integer,
            sa.ForeignKey("site.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime, nullable=False),
        sa.Column(
            "node_features", postgresql.JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "edge_features", postgresql.JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "hub_summary", postgresql.JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "policy_snapshot", postgresql.JSONB,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "source", sa.String(30), nullable=False,
            server_default=sa.text("'live'"),
        ),
        sa.Column(
            "created_at", sa.DateTime, nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "config_id", "hub_site_id", "observed_at",
            name="uq_hub_hour_snapshot",
        ),
    )
    op.create_index(
        "idx_hub_hour_snapshot_lookup", "hub_hour_snapshot",
        ["tenant_id", "hub_site_id", "observed_at"],
    )
    op.create_index(
        "idx_hub_hour_snapshot_recent", "hub_hour_snapshot",
        ["tenant_id", "observed_at"],
    )


def downgrade() -> None:
    if _table_exists("hub_hour_snapshot"):
        op.execute("DROP TABLE IF EXISTS hub_hour_snapshot CASCADE;")
