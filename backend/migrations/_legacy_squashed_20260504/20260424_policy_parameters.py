"""L4 Strategic policy_parameters table.

Creates `policy_parameters` — the canonical θ store consumed by L1 TRMs,
the (future) L2 Terminal Coordinator, and the (future) L3 Integrated
Balancer. Schema details + invariants in
`docs/L4_POLICY_PARAMETERS_DESIGN.md`.

Idempotent: information_schema-guarded so the migration replays cleanly.

Default JSONB server-defaults match `app.models.policy_parameters._DEFAULT_*`
exactly — keep them in lockstep when you change one. The Python defaults
are what pytest fixtures and explicit-construction call sites get; the
DB defaults are what `INSERT INTO policy_parameters (tenant_id) VALUES (?)`
gets. Drift between them silently bakes inconsistent θ into existing
tenants on backfill.

Revision ID: 20260424_policy_parameters
Revises: (no dependency — idempotent)
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260424_policy_parameters"
down_revision = None
branch_labels = None
depends_on = None


# Default JSONB server-default literals. Keep in lockstep with
# app.models.policy_parameters._DEFAULT_* dicts.
_DEFAULT_SERVICE_LEVEL_TIERS = """[
  {"tier": "PLATINUM", "otd_target_pct": 99, "tender_accept_pct": 99, "priority": 1},
  {"tier": "GOLD",     "otd_target_pct": 95, "tender_accept_pct": 97, "priority": 2},
  {"tier": "SILVER",   "otd_target_pct": 90, "tender_accept_pct": 92, "priority": 3},
  {"tier": "BRONZE",   "otd_target_pct": 85, "tender_accept_pct": 88, "priority": 4},
  {"tier": "ECONOMY",  "otd_target_pct": 80, "tender_accept_pct": 80, "priority": 5}
]"""

_DEFAULT_MODE_MIX_TARGETS = """{
  "FTL":          {"target_pct": 55, "floor_pct": 45, "ceiling_pct": 65},
  "LTL":          {"target_pct": 25, "floor_pct": 15, "ceiling_pct": 30},
  "INTERMODAL":   {"target_pct": 12, "floor_pct": 8,  "ceiling_pct": 20},
  "PARCEL":       {"target_pct": 5,  "floor_pct": 2,  "ceiling_pct": 10},
  "RAIL_CARLOAD": {"target_pct": 2,  "floor_pct": 0,  "ceiling_pct": 5},
  "AIR_STD":      {"target_pct": 1,  "floor_pct": 0,  "ceiling_pct": 3}
}"""

_DEFAULT_FLEET_COMPOSITION = """{
  "DRY_VAN":         {"asset_target": 50, "3pl_target": 200, "spot_ratio": 0.15},
  "REEFER":          {"asset_target": 10, "3pl_target": 40,  "spot_ratio": 0.25},
  "FLATBED":         {"asset_target": 5,  "3pl_target": 20,  "spot_ratio": 0.30},
  "CONTAINER_40FT":  {"asset_target": 0,  "3pl_target": 100, "spot_ratio": 0.10},
  "CONTAINER_20FT":  {"asset_target": 0,  "3pl_target": 50,  "spot_ratio": 0.10}
}"""

_DEFAULT_CARRIER_PORTFOLIO_TARGETS = """{
  "asset_ratio":              0.25,
  "contracted_3pl_ratio":     0.60,
  "spot_market_ratio":        0.15,
  "max_single_carrier_pct":   0.30,
  "min_carrier_count_per_lane": 2,
  "brokerage_allowance_pct":  0.10
}"""

_DEFAULT_NETWORK_TOPOLOGY = """{
  "pattern":             "HUB_AND_SPOKE",
  "hubs":                [],
  "max_stops_per_route": 4,
  "cross_dock_strategy": "REGIONAL",
  "intermodal_enabled":  true
}"""

_DEFAULT_ESCALATION_THRESHOLDS = """{
  "tender_reject_rate_1h":         0.20,
  "exception_backlog_count":       20,
  "terminal_health_threshold":     0.5,
  "terminal_health_duration_hours": 2,
  "sla_miss_rate_4h":              0.10,
  "cascade_affected_shipments":    10
}"""


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = :n AND table_schema = 'public'"
        ),
        {"n": name},
    ).scalar())


def _index_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes WHERE indexname = :n"
        ),
        {"n": name},
    ).scalar())


def upgrade() -> None:
    if _table_exists("policy_parameters"):
        return

    op.create_table(
        "policy_parameters",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id", sa.Integer,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "config_id", sa.Integer,
            sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # Effective window
        sa.Column("effective_from", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("effective_to", sa.DateTime, nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        # Authoring / audit
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("source", sa.String(30), nullable=False, server_default="STRATEGIC_AGENT"),
        sa.Column("source_proposal_id", sa.Integer, nullable=True),
        # Section 1: BSC weights
        sa.Column("bsc_weight_financial", sa.Float, nullable=False, server_default="0.35"),
        sa.Column("bsc_weight_customer", sa.Float, nullable=False, server_default="0.30"),
        sa.Column("bsc_weight_internal", sa.Float, nullable=False, server_default="0.20"),
        sa.Column("bsc_weight_learning", sa.Float, nullable=False, server_default="0.15"),
        # Section 2: service tiers
        sa.Column(
            "service_level_tiers", postgresql.JSONB, nullable=False,
            server_default=sa.text(f"'{_DEFAULT_SERVICE_LEVEL_TIERS}'::jsonb"),
        ),
        # Section 3: mode mix
        sa.Column(
            "mode_mix_targets", postgresql.JSONB, nullable=False,
            server_default=sa.text(f"'{_DEFAULT_MODE_MIX_TARGETS}'::jsonb"),
        ),
        sa.Column("mode_mix_period_days", sa.Integer, nullable=False, server_default="91"),
        # Section 4: fleet composition
        sa.Column(
            "fleet_composition", postgresql.JSONB, nullable=False,
            server_default=sa.text(f"'{_DEFAULT_FLEET_COMPOSITION}'::jsonb"),
        ),
        # Section 5: carrier portfolio
        sa.Column(
            "carrier_portfolio_targets", postgresql.JSONB, nullable=False,
            server_default=sa.text(f"'{_DEFAULT_CARRIER_PORTFOLIO_TARGETS}'::jsonb"),
        ),
        # Section 6: sustainability
        sa.Column("co2_per_load_mile_ceiling_g", sa.Float, nullable=True),
        sa.Column("co2_measurement_method", sa.String(30), server_default="EPA_SMARTWAY"),
        sa.Column("sustainability_penalty_weight", sa.Float, server_default="0.0"),
        # Section 7: cost guardrails
        sa.Column("max_cost_delta_pct", sa.Float, nullable=False, server_default="0.10"),
        sa.Column("max_expedite_premium_pct", sa.Float, nullable=False, server_default="0.50"),
        sa.Column("detention_cost_cap_usd", sa.Float, nullable=True),
        sa.Column("accessorial_cap_usd", sa.Float, nullable=True),
        # Section 8: network topology
        sa.Column(
            "network_topology", postgresql.JSONB, nullable=False,
            server_default=sa.text(f"'{_DEFAULT_NETWORK_TOPOLOGY}'::jsonb"),
        ),
        # Section 9: L3 cadence overrides
        sa.Column(
            "l3_cadence_overrides", postgresql.JSONB,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # Section 10: escalation thresholds
        sa.Column(
            "escalation_thresholds", postgresql.JSONB, nullable=False,
            server_default=sa.text(f"'{_DEFAULT_ESCALATION_THRESHOLDS}'::jsonb"),
        ),
        # Section 11: extension blob
        sa.Column(
            "extra_policy", postgresql.JSONB,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # Audit
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    # Tenant-scoped lookup index
    if not _index_exists("ix_policy_tenant_effective"):
        op.create_index(
            "ix_policy_tenant_effective", "policy_parameters",
            ["tenant_id", "effective_from", "effective_to"],
        )

    # Source-proposal lookup (nullable FK soft-link to agent_decisions)
    if not _index_exists("ix_policy_source_proposal"):
        op.create_index(
            "ix_policy_source_proposal", "policy_parameters",
            ["source_proposal_id"],
            postgresql_where=sa.text("source_proposal_id IS NOT NULL"),
        )

    # The "single active policy per scope" invariant. Partial unique
    # index over (tenant_id, COALESCE(config_id, 0)) WHERE
    # effective_to IS NULL. Raw DDL because SQLAlchemy can't express
    # the COALESCE-as-index-key portably.
    if not _index_exists("uq_policy_active"):
        op.execute(
            "CREATE UNIQUE INDEX uq_policy_active "
            "ON policy_parameters (tenant_id, COALESCE(config_id, 0)) "
            "WHERE effective_to IS NULL"
        )

    # Backfill: one default tenant-wide policy per existing tenant.
    # All structured columns get their server_default; only tenant_id
    # / source / version are populated explicitly. Idempotent — uses
    # ON CONFLICT DO NOTHING against the partial unique index.
    op.execute(
        """
        INSERT INTO policy_parameters (tenant_id, source, version)
        SELECT id, 'MIGRATION', 1
        FROM tenants
        ON CONFLICT (tenant_id, COALESCE(config_id, 0))
            WHERE effective_to IS NULL
            DO NOTHING
        """
    )


def downgrade() -> None:
    if _table_exists("policy_parameters"):
        for ix in (
            "uq_policy_active",
            "ix_policy_source_proposal",
            "ix_policy_tenant_effective",
        ):
            if _index_exists(ix):
                op.execute(f"DROP INDEX IF EXISTS {ix}")
        op.drop_table("policy_parameters")
