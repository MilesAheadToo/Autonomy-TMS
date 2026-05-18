"""§3.80 Category 3 batch 1: enable RLS on 4 clearly-tenant-scoped tables.

Revision ID: 20260518_soc2_cat3_rls_batch1
Revises: 20260518_inv_projection_config_id_not_null
Create Date: 2026-05-18

§3.80 Category 3 — first bounded batch
======================================

The first TMS SOC II audit (2026-05-18) found ~253 tables flagged
``hazard_nullable_fk_unmitigated`` — tenant-scoped tables with no RLS
+ no policy. This batch closes 4 of them. All four have proven
analogs in DP's ``0005_enable_rls_tenant_scoped`` /
``0006_rls_forecast_master``.

Tables in this batch (column shape per the post-squash baseline):

  Tenant_id direct (USING (tenant_id = current_setting('app.tenant_id')::int)):
  - ``public.external_signal_sources`` — ``tenant_id INTEGER NOT NULL``
  - ``public.external_signals``         — ``tenant_id INTEGER NOT NULL``

  Tenant via config_id (USING (config_id IN (SELECT id FROM supply_chain_configs WHERE tenant_id = …))):
  - ``public.forecast``           — only ``config_id`` (nullable on TMS; required at the policy-eval layer)
  - ``public.forecast_versions``  — only ``config_id``

Originally drafted with five tables; ``public.risk_alerts`` removed
2026-05-18 after Trevor flagged that it's the multi-plane Alert
substrate (Core §3.62) — SCP / DP / TMS all write to it, the
canonical home is autonomy-db / Core's data-model migrations, and a
TMS-only chain is the wrong place to attach the policy. Separate
workstream tracked in §3.80 / §3.62 follow-up.

The original ``forecast`` and ``forecast_versions`` entries used the
tenant_id-direct pattern by mistake — the post-squash baseline shows
both tables only carry ``config_id``, not ``tenant_id``. Fixed to use
the via-config policy.

Runtime contract
----------------
RLS enforcement requires the application's connection pool to set
``app.tenant_id`` on each tenant-scoped session. TMS's runtime in
``autonomy-app`` honours this contract (same plumbing SCP and DP
have been using). Connections without ``app.tenant_id`` set will see
zero rows — that's the correct failure mode under the platform's
single-home rule.

Idempotent via ``pg_policies`` catalog lookup.
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260518_soc2_cat3_rls_batch1"
down_revision = "20260518_inv_projection_config_id_not_null"
branch_labels = None
depends_on = None


_TENANT_DIRECT_TABLES = (
    "external_signal_sources",
    "external_signals",
)

_VIA_CONFIG_TABLES = (
    "forecast",
    "forecast_versions",
)


def _enable_rls_with_policy(table: str, policy_expr: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT (
                SELECT relrowsecurity FROM pg_class
                WHERE oid = 'public.{table}'::regclass
            ) THEN
                ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE schemaname = 'public'
                  AND tablename = '{table}'
                  AND policyname = 'tenant_isolation'
            ) THEN
                CREATE POLICY tenant_isolation ON public.{table}
                  USING ({policy_expr});
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    direct = "tenant_id = current_setting('app.tenant_id', true)::int"
    via_config = (
        "config_id IN ("
        "SELECT id FROM public.supply_chain_configs "
        "WHERE tenant_id = current_setting('app.tenant_id', true)::int"
        ")"
    )

    for table in _TENANT_DIRECT_TABLES:
        _enable_rls_with_policy(table, direct)
    for table in _VIA_CONFIG_TABLES:
        _enable_rls_with_policy(table, via_config)


def downgrade() -> None:
    for table in _TENANT_DIRECT_TABLES + _VIA_CONFIG_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON public.{table};")
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY;")
