"""§3.80 Category 2 follow-up: make inv_projection.config_id NOT NULL.

Revision ID: 20260518_inv_projection_config_id_not_null
Revises: 20260518_soc2_cat2_drop_inv_projection_scope_strings
Create Date: 2026-05-18

The preceding migration dropped the redundant ``scenario_id`` /
``scenario_name`` columns on ``inv_projection`` (closing the
``hazard_nullable_fk_plus_redundant_name`` finding) but ``config_id``
remained nullable — which immediately reclassified the table to the
``hazard_nullable_fk_unmitigated`` category instead of clearing it.
Audit count unchanged (254 → 254).

This migration completes the close-out by making ``config_id`` NOT
NULL. After both migrations apply, ``inv_projection`` carries:

  - NOT NULL ``config_id`` with FK to ``supply_chain_configs(id)``
    (FK was already declared at the ORM level and present on the table)
  - No redundant scope-string columns

That's the canonical "tenant via config_id" shape — same as DP's
``forecast_versions`` and the six tables fixed in the Cat 1 migration.

Backfill safety
---------------
``ALTER TABLE ... SET NOT NULL`` fails if any existing row has
``config_id IS NULL``. In the audit's fresh DB the table is empty, so
this is trivially safe. In any environment with existing rows, the
migration will fail loudly — surfacing the bad data rather than
silently masking it. The operator can then decide whether to backfill
to a sentinel config or drop the offending rows. This matches the
pre-launch directive: prefer fail-fast over "for now" backfills.

Idempotent: skips the ALTER if ``config_id`` is already NOT NULL.
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260518_inv_projection_config_id_not_null"
down_revision = "20260518_soc2_cat2_drop_inv_projection_scope_strings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'inv_projection'
                  AND column_name = 'config_id'
                  AND is_nullable = 'YES'
            ) THEN
                -- Fail loudly if any row would violate the constraint.
                -- The directive: surface bad data rather than mask it.
                IF EXISTS (
                    SELECT 1 FROM public.inv_projection WHERE config_id IS NULL LIMIT 1
                ) THEN
                    RAISE EXCEPTION
                        'inv_projection has rows with NULL config_id — '
                        'backfill or delete before applying this migration. '
                        'See §3.80 Category 2 in MIGRATION_REGISTER.md.';
                END IF;
                ALTER TABLE public.inv_projection
                  ALTER COLUMN config_id SET NOT NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.inv_projection ALTER COLUMN config_id DROP NOT NULL;"
    )
