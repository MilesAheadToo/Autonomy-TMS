"""Intermodal network + spot-rate substrate for TMS (Item 5).

Creates four transport-plane tables consumed by IntermodalTransferTRM:

  * intermodal_ramp           — rail / ocean / air ramp catalog
  * intermodal_rate           — (origin_ramp, destination_ramp, mode) rates
  * ramp_congestion_snapshot  — append-only ramp congestion time-series
  * spot_rate_snapshot        — append-only truck spot-rate time-series

All four are tenant-scoped with RLS enabled. Uses idempotent
information_schema guards so the migration replays cleanly against a
partially-populated DB.

Revision ID: 20260424_intermodal_network
Revises: (no dependency — idempotent guards; chains visible from
          alembic heads at apply time)
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260424_intermodal_network"
down_revision = None
branch_labels = None
depends_on = None


_TABLES = (
    "intermodal_ramp",
    "intermodal_rate",
    "ramp_congestion_snapshot",
    "spot_rate_snapshot",
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


def _enable_rls(table_name: str) -> None:
    """Enable row-level security and add tenant isolation policy.

    Pattern matches the rest of the TMS migrations: tenant_id matches a
    `current_setting('app.current_tenant_id', true)::int` session var
    or admin role bypass. The actual policy expression matches existing
    RLS-enabled tables in the schema.
    """
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE tablename = '{table_name}'
              AND policyname = 'tenant_isolation_{table_name}'
          ) THEN
            CREATE POLICY tenant_isolation_{table_name}
              ON {table_name}
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
    # ── intermodal_ramp ────────────────────────────────────────────────
    if not _table_exists("intermodal_ramp"):
        op.create_table(
            "intermodal_ramp",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("config_id", sa.Integer, sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("code", sa.String(50), nullable=False),
            sa.Column("ramp_type", sa.String(30), nullable=False),
            sa.Column("operator", sa.String(100), nullable=True),
            sa.Column("site_id", sa.Integer, sa.ForeignKey("site.id", ondelete="SET NULL"), nullable=True),
            sa.Column("latitude", sa.Float, nullable=True),
            sa.Column("longitude", sa.Float, nullable=True),
            sa.Column("address", sa.String(500), nullable=True),
            sa.Column("capacity_loads_daily", sa.Integer, nullable=True),
            sa.Column("congestion_threshold_pct", sa.Float, server_default="0.7"),
            sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "code", name="uq_intermodal_ramp_tenant_code"),
        )
        op.create_index("idx_intermodal_ramp_tenant", "intermodal_ramp", ["tenant_id"])
        op.create_index("idx_intermodal_ramp_type", "intermodal_ramp", ["ramp_type"])
        _enable_rls("intermodal_ramp")

    # ── intermodal_rate ────────────────────────────────────────────────
    if not _table_exists("intermodal_rate"):
        op.create_table(
            "intermodal_rate",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("origin_ramp_id", sa.Integer, sa.ForeignKey("intermodal_ramp.id", ondelete="CASCADE"), nullable=False),
            sa.Column("destination_ramp_id", sa.Integer, sa.ForeignKey("intermodal_ramp.id", ondelete="CASCADE"), nullable=False),
            # transport_mode_enum is created elsewhere; do NOT recreate it here.
            sa.Column(
                "mode",
                postgresql.ENUM(
                    name="transport_mode_enum", create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("rate_per_load", sa.Double, nullable=False),
            sa.Column("rate_per_container", sa.Double, nullable=True),
            sa.Column("fuel_surcharge_pct", sa.Float, nullable=True),
            sa.Column("transit_days_p50", sa.Float, nullable=False),
            sa.Column("transit_days_p90", sa.Float, nullable=True),
            sa.Column("reliability_pct", sa.Float, nullable=True),
            sa.Column("valid_from", sa.Date, nullable=False),
            sa.Column("valid_to", sa.Date, nullable=False),
            sa.Column("source", sa.String(30), nullable=False),
            sa.Column("contract_number", sa.String(100), nullable=True),
            sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        )
        op.create_index(
            "idx_intermodal_rate_lookup",
            "intermodal_rate",
            ["tenant_id", "origin_ramp_id", "destination_ramp_id", "mode"],
        )
        op.create_index(
            "idx_intermodal_rate_validity",
            "intermodal_rate",
            ["valid_from", "valid_to", "is_active"],
        )
        _enable_rls("intermodal_rate")

    # ── ramp_congestion_snapshot ───────────────────────────────────────
    if not _table_exists("ramp_congestion_snapshot"):
        op.create_table(
            "ramp_congestion_snapshot",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ramp_id", sa.Integer, sa.ForeignKey("intermodal_ramp.id", ondelete="CASCADE"), nullable=False),
            sa.Column("congestion_level", sa.Float, nullable=False),
            sa.Column("queued_loads", sa.Integer, nullable=True),
            sa.Column("expected_clear_hours", sa.Float, nullable=True),
            sa.Column("source", sa.String(50), nullable=True),
            sa.Column("snapshot_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        )
        op.create_index(
            "idx_ramp_congestion_lookup",
            "ramp_congestion_snapshot",
            ["tenant_id", "ramp_id", "snapshot_at"],
        )
        _enable_rls("ramp_congestion_snapshot")

    # ── spot_rate_snapshot ─────────────────────────────────────────────
    if not _table_exists("spot_rate_snapshot"):
        op.create_table(
            "spot_rate_snapshot",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("lane_id", sa.Integer, sa.ForeignKey("transportation_lane.id", ondelete="SET NULL"), nullable=True),
            sa.Column("origin_site_id", sa.Integer, sa.ForeignKey("site.id", ondelete="SET NULL"), nullable=True),
            sa.Column("destination_site_id", sa.Integer, sa.ForeignKey("site.id", ondelete="SET NULL"), nullable=True),
            sa.Column(
                "mode",
                postgresql.ENUM(
                    name="transport_mode_enum", create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("equipment_type", sa.String(30), nullable=True),
            sa.Column("rate_per_load", sa.Double, nullable=False),
            sa.Column("rate_per_mile", sa.Double, nullable=True),
            sa.Column("distance_miles", sa.Float, nullable=True),
            sa.Column("fuel_surcharge_pct", sa.Float, nullable=True),
            sa.Column("market_tightness", sa.Float, nullable=True),
            sa.Column("sample_size", sa.Integer, nullable=True),
            sa.Column("source", sa.String(50), nullable=False),
            sa.Column("valid_at", sa.DateTime, nullable=False),
            sa.Column("captured_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
            sa.Column("extra", sa.JSON, nullable=True),
        )
        op.create_index(
            "idx_spot_rate_lane",
            "spot_rate_snapshot",
            ["tenant_id", "lane_id", "mode", "valid_at"],
        )
        op.create_index(
            "idx_spot_rate_origin_dest",
            "spot_rate_snapshot",
            ["tenant_id", "origin_site_id", "destination_site_id", "mode"],
        )
        op.create_index(
            "idx_spot_rate_validity",
            "spot_rate_snapshot",
            ["valid_at"],
        )
        _enable_rls("spot_rate_snapshot")


def downgrade() -> None:
    # Drop in reverse FK order
    for tbl in reversed(_TABLES):
        if _table_exists(tbl):
            op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE;")
