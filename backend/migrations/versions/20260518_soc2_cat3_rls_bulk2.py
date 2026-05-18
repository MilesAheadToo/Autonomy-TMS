"""§3.80 Category 3 bulk pass 2: cover conformal schema + nullable tenant_id tables.

Revision ID: 20260518_soc2_cat3_rls_bulk2
Revises: 20260518_soc2_cat3_rls_bulk
Create Date: 2026-05-18

After the first bulk pass (``20260518_soc2_cat3_rls_bulk``) the audit
dropped from 245 → 25. The remaining 25 split into:

- ~5 ``conformal.*`` schema tables (bulk pass 1 only scanned
  ``public``)
- ~15 ``public.*`` tables flagged ``rls_off_on_tenant_scoped`` but
  not picked up by pass 1 — these have ``tenant_id`` as a *nullable*
  column. Pass 1 required ``is_nullable = 'NO'``; the audit
  considers any tenant_id column as tenant-scoping evidence.
- 4 deliberate exclusions: ``plane_registration``, ``risk_alerts``,
  ``supply_chain_configs``, ``users``.
- 1 Cat 2 (``hazard_redundant_scope_name`` on ``supply_plan_requests``)
  — different category, separate fix.

This pass:

1. Adds the ``conformal`` schema to the loop scope.
2. Drops the ``is_nullable = 'NO'`` filter so tables with nullable
   tenant_id are also covered. Rationale: a row with ``tenant_id IS
   NULL`` fails the ``tenant_id = current_setting(…)::int``
   comparison, so it's filtered out — which is the correct fail-
   closed behaviour for orphan rows. The audit's classification is
   "has a tenant_id column" → "tenant-scoped"; we mirror that.

Same exclusions as pass 1: risk_alerts (Core §3.62 home),
supply_chain_configs (parent of via-config policy), tenants, users,
plane_registration.

Idempotent: skips tables that already have a tenant_isolation
policy from pass 1 or the legacy schema migration.
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260518_soc2_cat3_rls_bulk2"
down_revision = "20260518_soc2_cat3_rls_bulk"
branch_labels = None
depends_on = None


_EXCLUDE = (
    "risk_alerts",
    "supply_chain_configs",
    "tenants",
    "users",
    "plane_registration",
)
_SCHEMAS = ("public", "conformal")


def upgrade() -> None:
    excludes_sql = ", ".join(f"'{t}'" for t in _EXCLUDE)
    schemas_sql = ", ".join(f"'{s}'" for s in _SCHEMAS)
    op.execute(
        f"""
        DO $$
        DECLARE
            tbl_rec   record;
            has_tenant boolean;
            has_config_id boolean;
            already_has_policy boolean;
        BEGIN
            FOR tbl_rec IN
                SELECT t.table_schema, t.table_name
                FROM information_schema.tables t
                WHERE t.table_schema IN ({schemas_sql})
                  AND t.table_type = 'BASE TABLE'
                  AND t.table_name NOT IN ({excludes_sql})
            LOOP
                SELECT EXISTS (
                    SELECT 1 FROM pg_policies
                    WHERE schemaname = tbl_rec.table_schema
                      AND tablename = tbl_rec.table_name
                      AND policyname = 'tenant_isolation'
                ) INTO already_has_policy;
                IF already_has_policy THEN
                    CONTINUE;
                END IF;

                -- Any tenant_id column (nullable or not) is enough.
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = tbl_rec.table_schema
                      AND table_name = tbl_rec.table_name
                      AND column_name = 'tenant_id'
                ) INTO has_tenant;

                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = tbl_rec.table_schema
                      AND table_name = tbl_rec.table_name
                      AND column_name = 'config_id'
                ) INTO has_config_id;

                IF has_tenant THEN
                    EXECUTE format(
                        'ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
                        tbl_rec.table_schema, tbl_rec.table_name
                    );
                    EXECUTE format(
                        'CREATE POLICY tenant_isolation ON %I.%I '
                        'USING (tenant_id = current_setting(''app.tenant_id'', true)::int)',
                        tbl_rec.table_schema, tbl_rec.table_name
                    );
                ELSIF has_config_id THEN
                    EXECUTE format(
                        'ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
                        tbl_rec.table_schema, tbl_rec.table_name
                    );
                    EXECUTE format(
                        'CREATE POLICY tenant_isolation ON %I.%I '
                        'USING (config_id IN ('
                        '  SELECT id FROM public.supply_chain_configs '
                        '  WHERE tenant_id = current_setting(''app.tenant_id'', true)::int'
                        '))',
                        tbl_rec.table_schema, tbl_rec.table_name
                    );
                END IF;
            END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    excludes_sql = ", ".join(f"'{t}'" for t in _EXCLUDE)
    schemas_sql = ", ".join(f"'{s}'" for s in _SCHEMAS)
    op.execute(
        f"""
        DO $$
        DECLARE
            tbl_rec record;
        BEGIN
            FOR tbl_rec IN
                SELECT t.table_schema, t.table_name
                FROM information_schema.tables t
                WHERE t.table_schema IN ({schemas_sql})
                  AND t.table_type = 'BASE TABLE'
                  AND t.table_name NOT IN ({excludes_sql})
            LOOP
                EXECUTE format(
                    'DROP POLICY IF EXISTS tenant_isolation ON %I.%I',
                    tbl_rec.table_schema, tbl_rec.table_name
                );
                EXECUTE format(
                    'ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY',
                    tbl_rec.table_schema, tbl_rec.table_name
                );
            END LOOP;
        END $$;
        """
    )
