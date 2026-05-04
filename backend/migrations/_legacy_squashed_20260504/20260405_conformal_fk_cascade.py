"""Add FK cascade on conformal.* tables to prevent orphaned rows.

Previously, conformal.active_predictors and sibling tables stored
config_id as a plain integer with no FK constraint. When a supply_chain_config
was deleted, the conformal rows became orphans and could be hydrated into
live provisioning runs — a SOC II cross-tenant data leak.

This migration adds FK constraints with ON DELETE CASCADE so that
conformal data is automatically removed when its parent config is deleted.

Revision ID: 20260405_conformal_fk
Revises: 20260330_soc2_schemas
Create Date: 2026-04-05
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260405_conformal_fk"
down_revision = "20260330_soc2_schemas"
branch_labels = None
depends_on = None


CONFORMAL_TABLES = [
    "active_predictors",
    "calibration_snapshots",
    "observation_log",
    "drift_events",
    "coverage_audit",
]


def upgrade() -> None:
    # First, clean any pre-existing orphans (idempotent — re-run safe)
    for tbl in CONFORMAL_TABLES:
        op.execute(
            f"""
            DELETE FROM conformal.{tbl}
            WHERE config_id IS NOT NULL
              AND config_id NOT IN (SELECT id FROM public.supply_chain_configs)
            """
        )

    # Add FK with ON DELETE CASCADE.
    # Use named constraint so we can drop cleanly in downgrade.
    for tbl in CONFORMAL_TABLES:
        constraint_name = f"fk_conformal_{tbl}_config_id"
        op.execute(
            f"""
            ALTER TABLE conformal.{tbl}
            ADD CONSTRAINT {constraint_name}
            FOREIGN KEY (config_id)
            REFERENCES public.supply_chain_configs(id)
            ON DELETE CASCADE
            """
        )


def downgrade() -> None:
    for tbl in CONFORMAL_TABLES:
        constraint_name = f"fk_conformal_{tbl}_config_id"
        op.execute(
            f"ALTER TABLE conformal.{tbl} DROP CONSTRAINT IF EXISTS {constraint_name}"
        )
