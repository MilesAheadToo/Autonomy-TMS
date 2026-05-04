"""Materialise Core substrate tables that TMS services depend on.

Revision ID: 20260504_tms_materialize_core_substrate
Revises: 20260504_tms_baseline_squash
Create Date: 2026-05-04

Background
----------
Mirrors the SCP equivalent (Autonomy-SCP PR #335). Per AD-5 in
``Autonomy-Core/docs/architecture/ARCHITECTURE_DECISIONS.md``, each
plane has its own DB; Core's data-model package ships migration
*templates*, but those don't auto-apply to product DBs. Each
product's chain materialises the subset its services consume.

Four Core templates that TMS services consume but were never
materialised in TMS's chain (the squash baseline captured the
prod-like TMS DB at the time, which was missing all four):

- ``0034_carrier_capacity_commitment`` — capacity commitments under
  Contract. Used by ``IntegratedBalancerService.balance_plan(
  resolve_capacity_from_db=True)``.
- ``0037_cascade_run`` — Powell cascade substrate. Used by
  ``L3CascadeRunner._record_cascade_run`` (writes one row per L3
  transport cascade execution).
- ``0038_transportation_plan_cascade_run_fk`` — retargets the
  legacy ``transportation_plan.cascade_run_id VARCHAR(100)`` column
  to an Integer FK on ``cascade_run.id``. The squash baseline
  preserved the legacy String column; this migration drops it and
  re-creates as Integer FK.
- ``0040_model_artifact`` — per-tenant ML model registry. Used by
  the L3 GraphSAGE Movement Planner's checkpoint loader (§3.41
  Phase 3.5).

Without these tables, the L3 cascade runner fails on its first
``_record_cascade_run`` call (``relation "cascade_run" does not
exist``) and the GraphSAGE inference path falls back to heuristic
silently (still works but loses the ML benefit).

What this migration does
------------------------
Creates the four substrate elements in TMS's DB. Mirrors the Core
templates' DDL verbatim. Idempotent —
``information_schema``/``pg_type``-guarded so re-running on a DB
that already has the tables is a no-op.

Order matters: ``cascade_run`` must exist before
``transportation_plan.cascade_run_id`` can FK to it.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PGEnum


# revision identifiers, used by Alembic.
revision = "20260504_tms_materialize_core_substrate"
down_revision = "20260504_tms_baseline_squash"
branch_labels = None
depends_on = None


def _table_exists(conn, schema: str, table: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :s AND table_name = :t"
            ),
            {"s": schema, "t": table},
        ).scalar()
    )


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def _column_data_type(conn, table: str, column: str) -> str | None:
    return conn.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).scalar()


def _enum_exists(conn, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM pg_type WHERE typname = :n"),
            {"n": name},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()

    tenants_exists = _table_exists(conn, "public", "tenants")
    contract_exists = _table_exists(conn, "public", "contract")
    transportation_plan_exists = _table_exists(
        conn, "public", "transportation_plan",
    )

    # ──────────────────────────────────────────────────────────────
    # 1. carrier_capacity_commitment (Core 0034 template)
    # ──────────────────────────────────────────────────────────────
    if not _table_exists(conn, "public", "carrier_capacity_commitment"):
        op.create_table(
            "carrier_capacity_commitment",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer, nullable=False),
            sa.Column(
                "contract_id", sa.Integer,
                sa.ForeignKey("contract.id") if contract_exists else None,
                nullable=False,
            ),
            sa.Column("lane_filter", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("equipment_type", sa.String(32), nullable=True),
            sa.Column("mode", sa.String(20), nullable=True),
            sa.Column("period_start", sa.Date, nullable=False),
            sa.Column("period_end", sa.Date, nullable=False),
            sa.Column(
                "period_granularity", sa.String(20),
                nullable=False, server_default="WEEKLY",
            ),
            sa.Column("commit_volume", sa.Numeric(12, 2), nullable=False),
            sa.Column("min_volume", sa.Numeric(12, 2), nullable=True),
            sa.Column(
                "currency", sa.String(8), nullable=False,
                server_default="USD",
            ),
            sa.Column("effective_from", sa.DateTime, nullable=False),
            sa.Column("effective_to", sa.DateTime, nullable=True),
            sa.Column("notes", sa.Text, nullable=True),
            sa.Column(
                "created_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.CheckConstraint(
                "commit_volume >= 0",
                name="ck_capacity_commit_volume_nonneg",
            ),
            sa.CheckConstraint(
                "(min_volume IS NULL) OR (min_volume >= 0)",
                name="ck_capacity_commit_min_nonneg",
            ),
            sa.CheckConstraint(
                "(min_volume IS NULL) OR (min_volume <= commit_volume)",
                name="ck_capacity_commit_min_le_commit",
            ),
            sa.CheckConstraint(
                "period_end >= period_start",
                name="ck_capacity_commit_period_well_formed",
            ),
            sa.CheckConstraint(
                "(effective_to IS NULL) OR (effective_to >= effective_from)",
                name="ck_capacity_commit_effective_well_formed",
            ),
        )
        op.create_index(
            "ix_capacity_commit_contract_equip_period",
            "carrier_capacity_commitment",
            ["contract_id", "equipment_type", "period_start"],
        )
        op.create_index(
            "ix_capacity_commit_tenant_period",
            "carrier_capacity_commitment",
            ["tenant_id", "period_start", "period_end"],
        )

    # ──────────────────────────────────────────────────────────────
    # 2. cascade_run (Core 0037 template)
    # ──────────────────────────────────────────────────────────────
    if not _enum_exists(conn, "cascade_plane_type_enum"):
        op.execute(
            "CREATE TYPE cascade_plane_type_enum AS ENUM "
            "('L3_TRANSPORT', 'SUPPLY', 'SOP')"
        )
    if not _enum_exists(conn, "cascade_run_status_enum"):
        op.execute(
            "CREATE TYPE cascade_run_status_enum AS ENUM "
            "('RUNNING', 'OK', 'FAILED', 'SKIPPED')"
        )

    if not _table_exists(conn, "public", "cascade_run"):
        op.create_table(
            "cascade_run",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "cascade_run_id", sa.String(100),
                nullable=False, unique=True,
            ),
            sa.Column(
                "tenant_id", sa.Integer,
                sa.ForeignKey("tenants.id", ondelete="CASCADE")
                if tenants_exists else None,
                nullable=False,
            ),
            sa.Column(
                "plane_type",
                PGEnum(
                    "L3_TRANSPORT", "SUPPLY", "SOP",
                    name="cascade_plane_type_enum",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("period_start", sa.DateTime, nullable=True),
            sa.Column(
                "started_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("completed_at", sa.DateTime, nullable=True),
            sa.Column(
                "status",
                PGEnum(
                    "RUNNING", "OK", "FAILED", "SKIPPED",
                    name="cascade_run_status_enum",
                    create_type=False,
                ),
                nullable=False, server_default="RUNNING",
            ),
            sa.Column("n_stages_total", sa.Integer, nullable=True),
            sa.Column("n_stages_ok", sa.Integer, nullable=True),
            sa.Column("n_stages_failed", sa.Integer, nullable=True),
            sa.Column("error_summary", sa.String(2000), nullable=True),
            sa.Column("run_metadata", sa.JSON, nullable=True),
            sa.Column(
                "created_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.CheckConstraint(
                "(status = 'RUNNING') OR (completed_at IS NOT NULL)",
                name="ck_cascade_run_completed_at_set_when_terminal",
            ),
        )
        op.create_index(
            "ix_cascade_run_tenant_id", "cascade_run", ["tenant_id"],
        )
        op.create_index(
            "ix_cascade_run_tenant_plane_started", "cascade_run",
            ["tenant_id", "plane_type", "started_at"],
        )
        op.create_index(
            "ix_cascade_run_tenant_period", "cascade_run",
            ["tenant_id", "plane_type", "period_start"],
        )

    # ──────────────────────────────────────────────────────────────
    # 3. transportation_plan.cascade_run_id retarget (Core 0038)
    # ──────────────────────────────────────────────────────────────
    # Squash baseline preserved the legacy ``cascade_run_id
    # VARCHAR(100)`` column. Drop and re-create as Integer FK on
    # cascade_run.id with ON DELETE SET NULL. Existing data in the
    # legacy column is dropped — TMS prod-like DB had no rows
    # populated with cascade_run_label values matching cascade_run
    # (the cascade_run table didn't exist), so backfill is a no-op.
    if transportation_plan_exists:
        current_type = _column_data_type(
            conn, "transportation_plan", "cascade_run_id",
        )
        if current_type == "character varying":
            op.drop_column("transportation_plan", "cascade_run_id")
        if not _column_exists(conn, "transportation_plan", "cascade_run_id"):
            op.add_column(
                "transportation_plan",
                sa.Column(
                    "cascade_run_id", sa.Integer,
                    sa.ForeignKey("cascade_run.id", ondelete="SET NULL"),
                    nullable=True,
                ),
            )
            op.create_index(
                "ix_transportation_plan_cascade_run_id",
                "transportation_plan", ["cascade_run_id"],
            )

    # ──────────────────────────────────────────────────────────────
    # 4. model_artifact (Core 0040 template)
    # ──────────────────────────────────────────────────────────────
    if not _enum_exists(conn, "model_artifact_status_enum"):
        op.execute(
            "CREATE TYPE model_artifact_status_enum AS ENUM "
            "('TRAINED', 'CALIBRATED', 'STAGED', 'ACTIVE', 'ARCHIVED')"
        )
    if not _enum_exists(conn, "model_artifact_framework_enum"):
        op.execute(
            "CREATE TYPE model_artifact_framework_enum AS ENUM "
            "('PYTORCH', 'SCIKIT', 'LIGHTGBM', 'XGBOOST', "
            "'TENSORFLOW', 'OTHER')"
        )

    if not _table_exists(conn, "public", "model_artifact"):
        op.create_table(
            "model_artifact",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id", sa.Integer,
                sa.ForeignKey("tenants.id", ondelete="CASCADE")
                if tenants_exists else None,
                nullable=True,
            ),
            sa.Column("model_kind", sa.String(64), nullable=False),
            sa.Column("version", sa.String(64), nullable=False),
            sa.Column(
                "status",
                PGEnum(
                    "TRAINED", "CALIBRATED", "STAGED", "ACTIVE", "ARCHIVED",
                    name="model_artifact_status_enum",
                    create_type=False,
                ),
                nullable=False, server_default="TRAINED",
            ),
            sa.Column("checkpoint_uri", sa.String(512), nullable=False),
            sa.Column(
                "framework",
                PGEnum(
                    "PYTORCH", "SCIKIT", "LIGHTGBM", "XGBOOST",
                    "TENSORFLOW", "OTHER",
                    name="model_artifact_framework_enum",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("framework_version", sa.String(32), nullable=True),
            sa.Column("training_started_at", sa.DateTime, nullable=True),
            sa.Column("training_completed_at", sa.DateTime, nullable=True),
            sa.Column("training_data_range_start", sa.DateTime, nullable=True),
            sa.Column("training_data_range_end", sa.DateTime, nullable=True),
            sa.Column("n_training_examples", sa.Integer, nullable=True),
            sa.Column("validation_metrics", sa.JSON, nullable=True),
            sa.Column("hyperparameters", sa.JSON, nullable=True),
            sa.Column("model_metadata", sa.JSON, nullable=True),
            sa.Column("produced_by", sa.String(128), nullable=True),
            sa.Column("notes", sa.Text, nullable=True),
            sa.Column("activated_at", sa.DateTime, nullable=True),
            sa.Column("archived_at", sa.DateTime, nullable=True),
            sa.Column(
                "created_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "tenant_id", "model_kind", "version",
                name="uq_model_artifact_tenant_kind_version",
            ),
            sa.CheckConstraint(
                "(status != 'ACTIVE') OR (activated_at IS NOT NULL)",
                name="ck_model_artifact_active_has_activation_time",
            ),
            sa.CheckConstraint(
                "(status != 'ARCHIVED') OR "
                "(activated_at IS NOT NULL AND archived_at IS NOT NULL)",
                name="ck_model_artifact_archived_has_both_timestamps",
            ),
            sa.CheckConstraint(
                "(training_started_at IS NULL) OR "
                "(training_completed_at IS NULL) OR "
                "(training_completed_at >= training_started_at)",
                name="ck_model_artifact_training_window_well_formed",
            ),
            sa.CheckConstraint(
                "(training_data_range_start IS NULL) OR "
                "(training_data_range_end IS NULL) OR "
                "(training_data_range_end >= training_data_range_start)",
                name="ck_model_artifact_data_range_well_formed",
            ),
            sa.CheckConstraint(
                "(n_training_examples IS NULL) OR (n_training_examples >= 0)",
                name="ck_model_artifact_n_examples_nonneg",
            ),
        )
        op.create_index(
            "ix_model_artifact_tenant_id", "model_artifact", ["tenant_id"],
        )
        op.create_index(
            "ix_model_artifact_model_kind", "model_artifact", ["model_kind"],
        )
        op.create_index(
            "ix_model_artifact_status", "model_artifact", ["status"],
        )
        op.create_index(
            "ix_model_artifact_active_lookup", "model_artifact",
            ["tenant_id", "model_kind", "status"],
        )
        op.create_index(
            "ix_model_artifact_tenant_kind_created", "model_artifact",
            ["tenant_id", "model_kind", "created_at"],
        )
        op.execute(
            "CREATE UNIQUE INDEX uq_model_artifact_one_active_per_kind "
            "ON model_artifact (tenant_id, model_kind) "
            "WHERE status = 'ACTIVE'"
        )


def downgrade() -> None:
    """Drop in reverse order. Defensive ``IF EXISTS`` clauses cover
    partial-state downgrades."""
    conn = op.get_bind()

    # 4. model_artifact
    if _table_exists(conn, "public", "model_artifact"):
        op.execute("DROP TABLE model_artifact CASCADE")
    if _enum_exists(conn, "model_artifact_framework_enum"):
        op.execute("DROP TYPE model_artifact_framework_enum")
    if _enum_exists(conn, "model_artifact_status_enum"):
        op.execute("DROP TYPE model_artifact_status_enum")

    # 3. transportation_plan.cascade_run_id — drop Integer column +
    #    re-add legacy String column for forward-compat with prior
    #    state.
    if (
        _table_exists(conn, "public", "transportation_plan")
        and _column_exists(conn, "transportation_plan", "cascade_run_id")
        and _column_data_type(conn, "transportation_plan", "cascade_run_id") == "integer"
    ):
        op.drop_index(
            "ix_transportation_plan_cascade_run_id",
            table_name="transportation_plan",
        )
        op.drop_column("transportation_plan", "cascade_run_id")
        op.add_column(
            "transportation_plan",
            sa.Column("cascade_run_id", sa.String(100), nullable=True),
        )

    # 2. cascade_run
    if _table_exists(conn, "public", "cascade_run"):
        op.execute("DROP TABLE cascade_run CASCADE")
    if _enum_exists(conn, "cascade_run_status_enum"):
        op.execute("DROP TYPE cascade_run_status_enum")
    if _enum_exists(conn, "cascade_plane_type_enum"):
        op.execute("DROP TYPE cascade_plane_type_enum")

    # 1. carrier_capacity_commitment
    if _table_exists(conn, "public", "carrier_capacity_commitment"):
        op.execute("DROP TABLE carrier_capacity_commitment CASCADE")
