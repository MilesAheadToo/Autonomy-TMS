"""§3.80 Cat 3 bulk pass 3: cover supply_chain_config_id / sc_config_id variants.

Revision ID: 20260518_soc2_cat3_rls_bulk3
Revises: 20260518_soc2_cat3_rls_bulk2
Create Date: 2026-05-18

Passes 1 + 2 dropped the audit from 245 → 9. The remaining 2 audit-
fixable tables (capacity_plans, mps_plans) use ``supply_chain_config_id``
instead of ``config_id`` for the scope FK column. The audit recognizes
all three column-name variants (``config_id``, ``supply_chain_config_id``,
``sc_config_id``) per ``scripts/audits/soc2_tenant_scope_audit.py``
``SCOPE_FK_COL`` / ``ALT_SCOPE_FK_COLS``; my prior loops only checked
the canonical ``config_id``.

This pass scans both schemas (public + conformal) for any table with
any of the three scope-FK column names and attaches the appropriate
via-config policy.

Same exclusion list and idempotency guards as passes 1 + 2.
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260518_soc2_cat3_rls_bulk3"
down_revision = "20260518_soc2_cat3_rls_bulk2"
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
_SCOPE_FK_COLUMNS = ("config_id", "supply_chain_config_id", "sc_config_id")


def upgrade() -> None:
    excludes_sql = ", ".join(f"'{t}'" for t in _EXCLUDE)
    schemas_sql = ", ".join(f"'{s}'" for s in _SCHEMAS)
    op.execute(
        f"""
        DO $$
        DECLARE
            tbl_rec record;
            scope_col text;
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

                -- Skip if a tenant_id column exists; pass 2 already handled it.
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = tbl_rec.table_schema
                      AND table_name = tbl_rec.table_name
                      AND column_name = 'tenant_id'
                ) THEN
                    CONTINUE;
                END IF;

                -- Find which scope-FK column variant this table uses.
                SELECT column_name INTO scope_col
                FROM information_schema.columns
                WHERE table_schema = tbl_rec.table_schema
                  AND table_name = tbl_rec.table_name
                  AND column_name IN ('config_id', 'supply_chain_config_id', 'sc_config_id')
                ORDER BY
                    CASE column_name
                        WHEN 'config_id' THEN 1
                        WHEN 'supply_chain_config_id' THEN 2
                        WHEN 'sc_config_id' THEN 3
                    END
                LIMIT 1;

                IF scope_col IS NULL THEN
                    CONTINUE;
                END IF;

                EXECUTE format(
                    'ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
                    tbl_rec.table_schema, tbl_rec.table_name
                );
                EXECUTE format(
                    'CREATE POLICY tenant_isolation ON %I.%I '
                    'USING (%I IN ('
                    '  SELECT id FROM public.supply_chain_configs '
                    '  WHERE tenant_id = current_setting(''app.tenant_id'', true)::int'
                    '))',
                    tbl_rec.table_schema, tbl_rec.table_name, scope_col
                );
            END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    # Drop policy + disable RLS for any table touched by this pass.
    # Conservative: scan same schemas and drop tenant_isolation if present
    # AND the table uses the alt scope-FK columns (so we don't undo passes
    # 1 + 2's work).
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
                  AND EXISTS (
                    SELECT 1 FROM information_schema.columns c
                    WHERE c.table_schema = t.table_schema
                      AND c.table_name = t.table_name
                      AND c.column_name IN ('supply_chain_config_id', 'sc_config_id')
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM information_schema.columns c
                    WHERE c.table_schema = t.table_schema
                      AND c.table_name = t.table_name
                      AND c.column_name IN ('config_id', 'tenant_id')
                  )
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
