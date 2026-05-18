"""Mirror of Core 0052: RLS + tenant_isolation on TMS-standalone risk_alerts.

Revision ID: 20260518_soc2_risk_alerts_rls
Revises: 20260518_soc2_cat3_final
Create Date: 2026-05-18

Companion to Autonomy-Core@7c14854 (data-model migration
``0052_risk_alerts_rls``). Core's migration covers the canonical
``risk_alerts`` table in ``autonomy-db`` (the AD-13 single-DB
production target); this one covers the same table in TMS's
standalone DB (legacy AD-12 topology, still alive in dev / on
msi-stealth).

Closes the last 2 of 4 deliberate exclusions from §3.80 (the
``risk_alerts`` ×2 audit-category lines). After this lands the
TMS audit reports only ``supply_chain_configs`` + ``users`` (both
separately tracked with structural-reason defers).

Same DDL as Core 0052 — kept in lockstep. Idempotent.
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260518_soc2_risk_alerts_rls"
down_revision = "20260518_soc2_cat3_final"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT (
                SELECT relrowsecurity FROM pg_class
                WHERE oid = 'public.risk_alerts'::regclass
            ) THEN
                ALTER TABLE public.risk_alerts ENABLE ROW LEVEL SECURITY;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE schemaname = 'public'
                  AND tablename = 'risk_alerts'
                  AND policyname = 'tenant_isolation'
            ) THEN
                CREATE POLICY tenant_isolation ON public.risk_alerts
                  USING (
                    config_id IN (
                      SELECT id FROM public.supply_chain_configs
                      WHERE tenant_id = current_setting('app.tenant_id', true)::int
                    )
                  );
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.risk_alerts;")
    op.execute("ALTER TABLE public.risk_alerts DISABLE ROW LEVEL SECURITY;")
