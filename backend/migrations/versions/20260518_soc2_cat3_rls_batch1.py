"""§3.80 Category 3 batch 1: enable RLS on 5 clearly-tenant-scoped tables.

Revision ID: 20260518_soc2_cat3_rls_batch1
Revises: 20260518_inv_projection_config_id_not_null
Create Date: 2026-05-18

§3.80 Category 3 — first bounded batch
======================================

The first TMS SOC II audit found ~253 tables flagged
``hazard_nullable_fk_unmitigated`` — tenant-scoped tables with no RLS
+ no policy. This batch closes 5 of them, all with proven DP analogs
(same column shape, same policy pattern in DP's
``0005_enable_rls_tenant_scoped``).

Tables in this batch:

  - ``public.forecast`` — DP's `0006_rls_forecast_master` is the analog
    (note: DP uses tenant-via-config_id; TMS's table has ``tenant_id``
    NOT NULL directly, so we use the direct pattern).
  - ``public.forecast_versions`` — DP analog: ``forecast_versions`` in
    `0005` (tenant-via-config_id). TMS variant has tenant_id direct.
  - ``public.external_signal_sources`` — DP analog: same name in `0005`,
    tenant_id direct.
  - ``public.external_signals`` — DP analog: same name in `0005`,
    tenant_id direct.
  - ``public.risk_alerts`` — multi-plane Alert ORM (Core
    `azirella_data_model.risk_engine.Alert`). DP enables RLS via `0005`
    (tenant_id direct); TMS gets symmetric treatment here.

All five have ``tenant_id INTEGER NOT NULL`` per the post-squash
baseline (verified via baseline_schema.sql grep). Standard
tenant_isolation policy: ``USING (tenant_id =
current_setting('app.tenant_id', true)::int)``.

Runtime contract
----------------
RLS enforcement requires the application's connection pool to set
``app.tenant_id`` on each tenant-scoped session. TMS's runtime in
``autonomy-app`` honours this contract (same plumbing SCP and DP have
been using). Connections without ``app.tenant_id`` set will see zero
rows — that's the correct failure mode under the platform's
single-home rule.

Idempotent via ``pg_policies`` catalog lookup.

Forward-classification approach: this batch ships 5 tables with the
highest confidence. The remaining ~250 will be batched per a per-
table classification (tenant_id-direct vs config_id-via vs
legitimately-global) tracked under §3.80 in MIGRATION_REGISTER.
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260518_soc2_cat3_rls_batch1"
down_revision = "20260518_inv_projection_config_id_not_null"
branch_labels = None
depends_on = None


_TENANT_DIRECT_TABLES = (
    "forecast",
    "forecast_versions",
    "external_signal_sources",
    "external_signals",
    "risk_alerts",
)


def upgrade() -> None:
    for table in _TENANT_DIRECT_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                -- Enable RLS if not already enabled.
                IF NOT (
                    SELECT relrowsecurity FROM pg_class
                    WHERE oid = 'public.{table}'::regclass
                ) THEN
                    ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
                END IF;

                -- Attach tenant_isolation policy if not already present.
                IF NOT EXISTS (
                    SELECT 1 FROM pg_policies
                    WHERE schemaname = 'public'
                      AND tablename = '{table}'
                      AND policyname = 'tenant_isolation'
                ) THEN
                    CREATE POLICY tenant_isolation ON public.{table}
                      USING (tenant_id = current_setting('app.tenant_id', true)::int);
                END IF;
            END $$;
            """
        )


def downgrade() -> None:
    for table in _TENANT_DIRECT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON public.{table};")
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY;")
