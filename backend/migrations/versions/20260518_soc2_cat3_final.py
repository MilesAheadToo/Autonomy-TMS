"""§3.80 Cat 3 finale: plane_registration RLS + supply_plan_requests redundant column drop.

Revision ID: 20260518_soc2_cat3_final
Revises: 20260518_soc2_cat3_rls_bulk3
Create Date: 2026-05-18

After passes 1+2+3 the audit dropped to 7 violations. Of those:

- 4 are intentional defers (risk_alerts, supply_chain_configs, users —
  Core §3.62 / cross-tenant identity / RLS-policy parent).
- 2 belong to ``plane_registration`` — initially excluded out of caution
  (it's the licensing registry the router reads to dispatch).
  On second look the table has ``tenant_id INTEGER NOT NULL`` and the
  rows are inherently tenant-private (which tier the tenant licensed
  per plane). RLS is exactly right; the contract — router sets
  ``app.tenant_id`` before reading — already holds for all the other
  tables passes 1-3 covered.
- 1 belongs to ``supply_plan_requests`` — Cat 2
  (``hazard_redundant_scope_name``). Has ``config_id INTEGER NOT NULL``
  + ``config_name VARCHAR(200)`` (redundant). Same pattern as
  ``inv_projection`` Cat 2: drop the redundant scope-string column.

This migration covers both: plane_registration gets the tenant_id-
direct RLS treatment; supply_plan_requests loses its config_name
column.

Expected audit count after this: 7 → 3 (only the three deliberate
exclusions remain — risk_alerts, supply_chain_configs, users).
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260518_soc2_cat3_final"
down_revision = "20260518_soc2_cat3_rls_bulk3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. plane_registration — RLS + tenant_isolation (tenant_id direct).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT (
                SELECT relrowsecurity FROM pg_class
                WHERE oid = 'public.plane_registration'::regclass
            ) THEN
                ALTER TABLE public.plane_registration ENABLE ROW LEVEL SECURITY;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE schemaname = 'public'
                  AND tablename = 'plane_registration'
                  AND policyname = 'tenant_isolation'
            ) THEN
                CREATE POLICY tenant_isolation ON public.plane_registration
                  USING (tenant_id = current_setting('app.tenant_id', true)::int);
            END IF;
        END $$;
        """
    )

    # 2. supply_plan_requests — drop redundant config_name (Cat 2).
    # Same pattern as inv_projection Cat 2 cleanup. config_id is
    # already NOT NULL on this table per the baseline_schema dump.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'supply_plan_requests'
                  AND column_name = 'config_name'
            ) THEN
                ALTER TABLE public.supply_plan_requests DROP COLUMN config_name;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.supply_plan_requests "
        "ADD COLUMN IF NOT EXISTS config_name VARCHAR(200);"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.plane_registration;")
    op.execute("ALTER TABLE public.plane_registration DISABLE ROW LEVEL SECURITY;")
