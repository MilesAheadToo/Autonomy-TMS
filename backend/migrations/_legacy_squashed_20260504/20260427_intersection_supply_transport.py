"""TMS-side adoption of Core MIGRATION_REGISTER 1.8 — full intersection package.

Mirrors Core migrations 0006_intersection_contracts + 0007_intersection_feedback
(shipped in azirella-data-model v0.7.0 + v0.8.0). TMS's alembic chain is
separate from Core's, so we ship a parallel migration here rather than
chaining across packages — same pattern as `20260422_plane_registration.py`
for MR 1.9.

Creates 6 tables + 5 enum types:

  * deployment_requirement       — SCP → TMS contract
  * dispatch_commitment          — TMS → SCP contract
  * service_window_promise       — joint contract
  * lane_performance_actuals     — closed-loop event log
  * carrier_capacity_state       — periodic carrier-utilisation snapshot
  * service_commitment_outcomes  — terminal-state per joint promise

Enums:
  * mode_flexibility_enum        — ROAD_ONLY / ANY_GROUND / ANY_MODE
  * dispatch_state_enum          — COMMITTED / DISPATCHED / IN_TRANSIT /
                                   DELIVERED / EXCEPTION
  * promise_state_enum           — PROPOSED / JOINT_COMMITTED / MISSED /
                                   FULFILLED
  * commitment_outcome_enum      — MET / MISSED / MISSED_RECOVERED /
                                   CANCELLED
  * capacity_state_class_enum    — ABUNDANT / NORMAL / TIGHT / EXHAUSTED

Reuses ``plane_enum`` shipped in `20260422_plane_registration.py`.

Once SCP also adopts and Core can claim authoritative ownership of
this schema, the parallel TMS migration becomes redundant and gets
folded into the consumer-adoption-from-Core path. Until then, each
product creates its own copy via its own alembic chain.

Revision ID: 20260427_intersection_supply_transport
Revises: (no dependency — idempotent, information_schema guarded)
Create Date: 2026-04-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260427_intersect_st"
down_revision = None
branch_labels = None
depends_on = None


_TABLES = (
    "deployment_requirement",
    "dispatch_commitment",
    "service_window_promise",
    "lane_performance_actuals",
    "carrier_capacity_state",
    "service_commitment_outcomes",
)

_NEW_ENUMS: dict[str, tuple[str, ...]] = {
    "mode_flexibility_enum": ("ROAD_ONLY", "ANY_GROUND", "ANY_MODE"),
    "dispatch_state_enum": (
        "COMMITTED", "DISPATCHED", "IN_TRANSIT", "DELIVERED", "EXCEPTION",
    ),
    "promise_state_enum": (
        "PROPOSED", "JOINT_COMMITTED", "MISSED", "FULFILLED",
    ),
    "commitment_outcome_enum": (
        "MET", "MISSED", "MISSED_RECOVERED", "CANCELLED",
    ),
    "capacity_state_class_enum": (
        "ABUNDANT", "NORMAL", "TIGHT", "EXHAUSTED",
    ),
}


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = :n AND table_schema = 'public'"
        ),
        {"n": name},
    ).scalar())


def _enum_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :n"),
        {"n": name},
    ).scalar())


def upgrade() -> None:
    # ── New enum types ──────────────────────────────────────────────
    for enum_name, values in _NEW_ENUMS.items():
        if not _enum_exists(enum_name):
            postgresql.ENUM(*values, name=enum_name).create(
                op.get_bind(), checkfirst=False,
            )

    plane_enum_col = postgresql.ENUM(
        "SUPPLY", "TRANSPORT", "PORTFOLIO", "DEMAND_SHAPING",
        "PRODUCTION", "WAREHOUSE",
        name="plane_enum", create_type=False,
    )

    # ── deployment_requirement ──────────────────────────────────────
    if not _table_exists("deployment_requirement"):
        op.create_table(
            "deployment_requirement",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id", sa.Integer,
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("source_site_id", sa.String(100), nullable=False),
            sa.Column("dest_site_id", sa.String(100), nullable=False),
            sa.Column("product_id", sa.String(100), nullable=False),
            sa.Column("required_qty", sa.Double, nullable=False),
            sa.Column("required_by", sa.DateTime, nullable=False),
            sa.Column("priority", sa.Integer, nullable=False, server_default="5"),
            sa.Column("shadow_price_miss", sa.Double, nullable=False),
            sa.Column(
                "shadow_price_earliness", sa.Double,
                nullable=False, server_default="0",
            ),
            sa.Column(
                "mode_flexibility",
                postgresql.ENUM(
                    *_NEW_ENUMS["mode_flexibility_enum"],
                    name="mode_flexibility_enum", create_type=False,
                ),
                nullable=False, server_default="ANY_GROUND",
            ),
            sa.Column(
                "superseded_by", sa.Integer,
                sa.ForeignKey("deployment_requirement.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "source_plane", plane_enum_col,
                nullable=False, server_default="SUPPLY",
            ),
            sa.Column("correlation_id", sa.String(100), nullable=False),
            sa.Column(
                "created_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "tenant_id", "correlation_id", "source_plane",
                name="uq_deployment_req_correlation",
            ),
        )
        op.create_index(
            "idx_deployment_req_lookup", "deployment_requirement",
            ["tenant_id", "required_by"],
        )
        op.create_index(
            "idx_deployment_req_source_dest", "deployment_requirement",
            ["tenant_id", "source_site_id", "dest_site_id", "required_by"],
        )
        op.create_index(
            "idx_deployment_req_chain", "deployment_requirement",
            ["superseded_by"],
        )
        op.create_index(
            "ix_deployment_requirement_tenant_id", "deployment_requirement",
            ["tenant_id"],
        )

    # ── dispatch_commitment ─────────────────────────────────────────
    if not _table_exists("dispatch_commitment"):
        op.create_table(
            "dispatch_commitment",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id", sa.Integer,
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("transfer_order_id", sa.String(100), nullable=False),
            sa.Column("carrier_id", sa.String(100), nullable=False),
            sa.Column("load_id", sa.String(100), nullable=True),
            sa.Column("pickup_window_start", sa.DateTime, nullable=False),
            sa.Column("pickup_window_end", sa.DateTime, nullable=False),
            sa.Column("dispatched_at", sa.DateTime, nullable=True),
            sa.Column("eta_p10", sa.DateTime, nullable=False),
            sa.Column("eta_p50", sa.DateTime, nullable=False),
            sa.Column("eta_p90", sa.DateTime, nullable=False),
            sa.Column(
                "state",
                postgresql.ENUM(
                    *_NEW_ENUMS["dispatch_state_enum"],
                    name="dispatch_state_enum", create_type=False,
                ),
                nullable=False, server_default="COMMITTED",
            ),
            sa.Column(
                "source_plane", plane_enum_col,
                nullable=False, server_default="TRANSPORT",
            ),
            sa.Column("correlation_id", sa.String(100), nullable=False),
            sa.Column("state_reason", sa.Text, nullable=True),
            sa.Column(
                "created_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "tenant_id", "correlation_id", "source_plane",
                name="uq_dispatch_commit_correlation",
            ),
        )
        op.create_index(
            "idx_dispatch_commit_to", "dispatch_commitment",
            ["tenant_id", "transfer_order_id"],
        )
        op.create_index(
            "idx_dispatch_commit_state", "dispatch_commitment",
            ["tenant_id", "state"],
        )
        op.create_index(
            "idx_dispatch_commit_pickup", "dispatch_commitment",
            ["tenant_id", "pickup_window_start"],
        )
        op.create_index(
            "ix_dispatch_commitment_tenant_id", "dispatch_commitment",
            ["tenant_id"],
        )

    # ── service_window_promise ──────────────────────────────────────
    if not _table_exists("service_window_promise"):
        op.create_table(
            "service_window_promise",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id", sa.Integer,
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("order_id", sa.String(100), nullable=False),
            sa.Column("product_id", sa.String(100), nullable=False),
            sa.Column("origin_site_id", sa.String(100), nullable=False),
            sa.Column("dest_site_id", sa.String(100), nullable=False),
            sa.Column("lane_id", sa.String(100), nullable=True),
            sa.Column("atp_commit_qty", sa.Double, nullable=True),
            sa.Column("atp_available_at", sa.DateTime, nullable=True),
            sa.Column("capacity_commit_qty", sa.Double, nullable=True),
            sa.Column("capacity_window_start", sa.DateTime, nullable=True),
            sa.Column("capacity_window_end", sa.DateTime, nullable=True),
            sa.Column(
                "promise_state",
                postgresql.ENUM(
                    *_NEW_ENUMS["promise_state_enum"],
                    name="promise_state_enum", create_type=False,
                ),
                nullable=False, server_default="PROPOSED",
            ),
            sa.Column(
                "source_plane_last_updated", plane_enum_col,
                nullable=False,
            ),
            sa.Column("correlation_id", sa.String(100), nullable=False),
            sa.Column("conformal_p10", sa.DateTime, nullable=True),
            sa.Column("conformal_p50", sa.DateTime, nullable=True),
            sa.Column("conformal_p90", sa.DateTime, nullable=True),
            sa.Column("fulfilled_at", sa.DateTime, nullable=True),
            sa.Column(
                "created_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "tenant_id", "correlation_id", "source_plane_last_updated",
                name="uq_service_window_correlation",
            ),
        )
        op.create_index(
            "idx_swp_order", "service_window_promise",
            ["tenant_id", "order_id"],
        )
        op.create_index(
            "idx_swp_lane_window", "service_window_promise",
            ["tenant_id", "lane_id", "capacity_window_start"],
        )
        op.create_index(
            "idx_swp_state", "service_window_promise",
            ["tenant_id", "promise_state"],
        )
        op.create_index(
            "ix_service_window_promise_tenant_id", "service_window_promise",
            ["tenant_id"],
        )

    # ── lane_performance_actuals ────────────────────────────────────
    if not _table_exists("lane_performance_actuals"):
        op.create_table(
            "lane_performance_actuals",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id", sa.Integer,
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("lane_id", sa.String(100), nullable=False),
            sa.Column("carrier_id", sa.String(100), nullable=False),
            sa.Column("mode", sa.String(30), nullable=False),
            sa.Column("transfer_order_id", sa.String(100), nullable=True),
            sa.Column("load_id", sa.String(100), nullable=True),
            sa.Column("promised_pickup", sa.DateTime, nullable=False),
            sa.Column("promised_eta_p10", sa.DateTime, nullable=False),
            sa.Column("promised_eta_p50", sa.DateTime, nullable=False),
            sa.Column("promised_eta_p90", sa.DateTime, nullable=False),
            sa.Column("actual_pickup", sa.DateTime, nullable=True),
            sa.Column("actual_delivery", sa.DateTime, nullable=True),
            sa.Column("transit_minutes_actual", sa.Double, nullable=True),
            sa.Column("transit_minutes_promised_p50", sa.Double, nullable=True),
            sa.Column("on_time", sa.Integer, nullable=True),
            sa.Column("quantity", sa.Double, nullable=True),
            sa.Column("weight_lb", sa.Double, nullable=True),
            sa.Column("correlation_id", sa.String(100), nullable=False),
            sa.Column(
                "observation_index", sa.Integer,
                nullable=False, server_default="0",
            ),
            sa.Column(
                "source_plane", plane_enum_col,
                nullable=False, server_default="TRANSPORT",
            ),
            sa.Column(
                "observed_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "created_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "tenant_id", "correlation_id", "observation_index",
                name="uq_lane_perf_correlation",
            ),
        )
        op.create_index(
            "idx_lane_perf_lookup", "lane_performance_actuals",
            ["tenant_id", "lane_id", "mode", "observed_at"],
        )
        op.create_index(
            "idx_lane_perf_carrier", "lane_performance_actuals",
            ["tenant_id", "carrier_id"],
        )
        op.create_index(
            "ix_lane_performance_actuals_tenant_id",
            "lane_performance_actuals", ["tenant_id"],
        )

    # ── carrier_capacity_state ──────────────────────────────────────
    if not _table_exists("carrier_capacity_state"):
        op.create_table(
            "carrier_capacity_state",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id", sa.Integer,
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("carrier_id", sa.String(100), nullable=False),
            sa.Column("lane_id", sa.String(100), nullable=True),
            sa.Column("committed_loads_period", sa.Integer, nullable=False),
            sa.Column("actual_loads_period", sa.Integer, nullable=False),
            sa.Column("utilisation_pct", sa.Double, nullable=False),
            sa.Column(
                "state_class",
                postgresql.ENUM(
                    *_NEW_ENUMS["capacity_state_class_enum"],
                    name="capacity_state_class_enum", create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("period_start", sa.DateTime, nullable=False),
            sa.Column("period_end", sa.DateTime, nullable=False),
            sa.Column("observed_at", sa.DateTime, nullable=False),
            sa.Column("tender_accept_rate", sa.Double, nullable=True),
            sa.Column("tender_reject_rate", sa.Double, nullable=True),
            sa.Column(
                "source_plane", plane_enum_col,
                nullable=False, server_default="TRANSPORT",
            ),
            sa.Column(
                "created_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "tenant_id", "carrier_id", "observed_at",
                name="uq_carrier_capacity_obs",
            ),
        )
        op.create_index(
            "idx_carrier_cap_lookup", "carrier_capacity_state",
            ["tenant_id", "carrier_id", "observed_at"],
        )
        op.create_index(
            "idx_carrier_cap_state", "carrier_capacity_state",
            ["tenant_id", "state_class", "observed_at"],
        )
        op.create_index(
            "ix_carrier_capacity_state_tenant_id",
            "carrier_capacity_state", ["tenant_id"],
        )

    # ── service_commitment_outcomes ─────────────────────────────────
    if not _table_exists("service_commitment_outcomes"):
        op.create_table(
            "service_commitment_outcomes",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id", sa.Integer,
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("correlation_id", sa.String(100), nullable=False),
            sa.Column("order_id", sa.String(100), nullable=False),
            sa.Column("product_id", sa.String(100), nullable=False),
            sa.Column("origin_site_id", sa.String(100), nullable=False),
            sa.Column("dest_site_id", sa.String(100), nullable=False),
            sa.Column("lane_id", sa.String(100), nullable=True),
            sa.Column(
                "outcome",
                postgresql.ENUM(
                    *_NEW_ENUMS["commitment_outcome_enum"],
                    name="commitment_outcome_enum", create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("promised_window_start", sa.DateTime, nullable=False),
            sa.Column("promised_window_end", sa.DateTime, nullable=False),
            sa.Column("promised_qty", sa.Double, nullable=False),
            sa.Column("actual_delivered_at", sa.DateTime, nullable=True),
            sa.Column("actual_qty", sa.Double, nullable=True),
            sa.Column("minutes_late", sa.Double, nullable=True),
            sa.Column("qty_short", sa.Double, nullable=True),
            sa.Column("root_cause", sa.String(50), nullable=True),
            sa.Column("notes", sa.Text, nullable=True),
            sa.Column(
                "source_plane", plane_enum_col,
                nullable=False, server_default="TRANSPORT",
            ),
            sa.Column(
                "observed_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "created_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "tenant_id", "correlation_id",
                name="uq_service_outcome_correlation",
            ),
        )
        op.create_index(
            "idx_swp_outcome", "service_commitment_outcomes",
            ["tenant_id", "outcome", "observed_at"],
        )
        op.create_index(
            "idx_swp_outcome_order", "service_commitment_outcomes",
            ["tenant_id", "order_id", "observed_at"],
        )
        op.create_index(
            "ix_service_commitment_outcomes_tenant_id",
            "service_commitment_outcomes", ["tenant_id"],
        )


def downgrade() -> None:
    for tbl in reversed(_TABLES):
        if _table_exists(tbl):
            op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE;")

    for enum_name in (
        "capacity_state_class_enum",
        "commitment_outcome_enum",
        "promise_state_enum",
        "dispatch_state_enum",
        "mode_flexibility_enum",
    ):
        if _enum_exists(enum_name):
            op.execute(f"DROP TYPE IF EXISTS {enum_name};")
