"""§3.80 Category 1: add FK constraints for the six hazard_config_id_no_fk tables.

Revision ID: 20260518_soc2_cat1_config_id_fks
Revises: 20260517_rename_load_volume_sensing
Create Date: 2026-05-18

§3.80 Category 1 — quick-wins
=============================

The first TMS SOC II audit (Autonomy-TMS@8fbffd2, 2026-05-18) found
260 STRICT-mode violations. Category 1 is the smallest, cleanest
slice: six tables carry a non-null ``config_id`` column with no
foreign-key constraint to ``public.supply_chain_configs(id)``.
Adding the FK closes the audit finding and adds DB-level integrity
without changing any application logic.

Tables fixed:
  - conformal.active_predictors
  - conformal.calibration_snapshots
  - conformal.coverage_audit
  - conformal.drift_events
  - conformal.observation_log
  - public.training_corpus_checkpoint

``NOT VALID`` + ``VALIDATE CONSTRAINT`` pattern
-----------------------------------------------
``ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY (...) NOT VALID``
adds the constraint without scanning existing rows. ``VALIDATE
CONSTRAINT`` then scans (taking only a SHARE UPDATE EXCLUSIVE lock,
not ACCESS EXCLUSIVE — concurrent reads/writes proceed). If existing
data contains orphan ``config_id`` values, the VALIDATE step surfaces
the issue without blocking the structural constraint addition.

Idempotent via ``information_schema.table_constraints`` lookup.
``ON DELETE CASCADE`` matches the policy used in DP's
``0001b_core_substrate`` for the same FK target.
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260518_soc2_cat1_config_id_fks"
down_revision = "20260517_rename_load_volume_sensing"
branch_labels = None
depends_on = None


_FK_TARGETS = (
    # (schema, table, constraint_name)
    ("conformal", "active_predictors", "fk_active_predictors_config"),
    ("conformal", "calibration_snapshots", "fk_calibration_snapshots_config"),
    ("conformal", "coverage_audit", "fk_coverage_audit_config"),
    ("conformal", "drift_events", "fk_drift_events_config"),
    ("conformal", "observation_log", "fk_observation_log_config"),
    ("public", "training_corpus_checkpoint", "fk_training_corpus_checkpoint_config"),
)


def upgrade() -> None:
    for schema, table, constraint in _FK_TARGETS:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE constraint_schema = '{schema}'
                      AND table_name = '{table}'
                      AND constraint_name = '{constraint}'
                ) THEN
                    ALTER TABLE {schema}.{table}
                      ADD CONSTRAINT {constraint}
                      FOREIGN KEY (config_id)
                      REFERENCES public.supply_chain_configs(id)
                      ON DELETE CASCADE
                      NOT VALID;
                    ALTER TABLE {schema}.{table}
                      VALIDATE CONSTRAINT {constraint};
                END IF;
            END $$;
            """
        )


def downgrade() -> None:
    for schema, table, constraint in _FK_TARGETS:
        op.execute(
            f"ALTER TABLE {schema}.{table} DROP CONSTRAINT IF EXISTS {constraint}"
        )
