"""SOC II: Isolate agent state, checkpoints, and audit into dedicated schemas

Creates 4 new schemas:
- agents: Powell/TRM agent operational state
- conformal: Conformal prediction calibration state
- checkpoints: Model binary artifacts and training config
- audit: Decision audit trail, outcomes, replay buffer

Adds missing tenant_id/config_id columns for tenant+config isolation.
Moves tables from public to their correct schema.
Adds RLS policies for tenant isolation.

Revision ID: 20260330_soc2_schemas
Revises: 20260329_tbg_rename
Create Date: 2026-03-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '20260330_soc2_schemas'
down_revision: Union[str, None] = '20260329_tbg_rename'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ========================================================================
    # 1. Create schemas
    # ========================================================================
    op.execute("CREATE SCHEMA IF NOT EXISTS agents")
    op.execute("CREATE SCHEMA IF NOT EXISTS conformal")
    op.execute("CREATE SCHEMA IF NOT EXISTS checkpoints")
    op.execute("CREATE SCHEMA IF NOT EXISTS audit")

    # ========================================================================
    # 2. Add missing tenant_id columns (derived from config_id → tenant_id)
    # ========================================================================

    # Powell decision tables (11) — have config_id but no tenant_id
    powell_decision_tables = [
        'powell_atp_decisions', 'powell_po_decisions', 'powell_buffer_decisions',
        'powell_rebalance_decisions', 'powell_forecast_adjustment_decisions',
        'powell_mo_decisions', 'powell_to_decisions', 'powell_quality_decisions',
        'powell_maintenance_decisions', 'powell_order_exceptions',
        'powell_subcontracting_decisions',
    ]
    for tbl in powell_decision_tables:
        op.execute(f"""
            ALTER TABLE {tbl}
            ADD COLUMN IF NOT EXISTS tenant_id INTEGER
            REFERENCES tenants(id) ON DELETE CASCADE
        """)
        # Backfill from config_id → supply_chain_configs.tenant_id
        op.execute(f"""
            UPDATE {tbl} SET tenant_id = sc.tenant_id
            FROM supply_chain_configs sc
            WHERE {tbl}.config_id = sc.id AND {tbl}.tenant_id IS NULL
        """)

    # Powell state tables — have config_id but no tenant_id
    for tbl in ['powell_policy_parameters', 'powell_value_function', 'powell_allocations']:
        op.execute(f"""
            ALTER TABLE {tbl}
            ADD COLUMN IF NOT EXISTS tenant_id INTEGER
            REFERENCES tenants(id) ON DELETE CASCADE
        """)
        op.execute(f"""
            UPDATE {tbl} SET tenant_id = sc.tenant_id
            FROM supply_chain_configs sc
            WHERE {tbl}.config_id = sc.id AND {tbl}.tenant_id IS NULL
        """)

    # Powell calibration log — missing both tenant_id and config_id
    op.execute("""
        ALTER TABLE powell_calibration_log
        ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE
    """)
    op.execute("""
        ALTER TABLE powell_calibration_log
        ADD COLUMN IF NOT EXISTS config_id INTEGER REFERENCES supply_chain_configs(id) ON DELETE CASCADE
    """)

    # Powell training run — missing both
    op.execute("""
        ALTER TABLE powell_training_run
        ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE
    """)
    op.execute("""
        ALTER TABLE powell_training_run
        ADD COLUMN IF NOT EXISTS config_id INTEGER REFERENCES supply_chain_configs(id) ON DELETE CASCADE
    """)

    # Powell belief state — has tenant_id but missing config_id
    op.execute("""
        ALTER TABLE powell_belief_state
        ADD COLUMN IF NOT EXISTS config_id INTEGER REFERENCES supply_chain_configs(id) ON DELETE CASCADE
    """)

    # Powell escalation log — has tenant_id but missing config_id
    op.execute("""
        ALTER TABLE powell_escalation_log
        ADD COLUMN IF NOT EXISTS config_id INTEGER REFERENCES supply_chain_configs(id) ON DELETE CASCADE
    """)

    # TRM outcome tables (5) — missing both
    trm_outcome_tables = [
        'trm_atp_outcome', 'trm_po_outcome', 'trm_order_tracking_outcome',
        'trm_rebalancing_outcome', 'trm_safety_stock_outcome',
    ]
    for tbl in trm_outcome_tables:
        op.execute(f"""
            ALTER TABLE {tbl}
            ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE
        """)
        op.execute(f"""
            ALTER TABLE {tbl}
            ADD COLUMN IF NOT EXISTS config_id INTEGER REFERENCES supply_chain_configs(id) ON DELETE CASCADE
        """)

    # Override tables — missing both
    for tbl in ['override_effectiveness_posteriors', 'override_causal_match_pairs']:
        op.execute(f"""
            ALTER TABLE {tbl}
            ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE
        """)
        op.execute(f"""
            ALTER TABLE {tbl}
            ADD COLUMN IF NOT EXISTS config_id INTEGER REFERENCES supply_chain_configs(id) ON DELETE CASCADE
        """)

    # Checkpoint tables — add missing columns
    for tbl in ['trm_training_config', 'trm_site_training_config']:
        op.execute(f"""
            ALTER TABLE {tbl}
            ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE
        """)
        op.execute(f"""
            ALTER TABLE {tbl}
            ADD COLUMN IF NOT EXISTS config_id INTEGER REFERENCES supply_chain_configs(id) ON DELETE CASCADE
        """)

    op.execute("""
        ALTER TABLE trm_base_model
        ADD COLUMN IF NOT EXISTS config_id INTEGER REFERENCES supply_chain_configs(id) ON DELETE CASCADE
    """)

    op.execute("""
        ALTER TABLE supply_chain_training_artifacts
        ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE
    """)

    # ========================================================================
    # 3. Move tables to correct schemas
    # ========================================================================

    # --- agents schema ---
    agents_tables = powell_decision_tables + [
        'powell_policy_parameters', 'powell_value_function', 'powell_allocations',
        'powell_training_config', 'powell_training_run',
        'powell_escalation_log', 'powell_calibration_log',
        'powell_sop_embeddings', 'powell_belief_state',
        'agent_configs', 'agent_decision_metrics', 'agent_stochastic_params',
        'agent_decisions', 'agent_action', 'agent_suggestions',
    ]
    for tbl in agents_tables:
        op.execute(f"ALTER TABLE IF EXISTS public.{tbl} SET SCHEMA agents")

    # --- checkpoints schema ---
    checkpoints_tables = [
        'model_checkpoints', 'trm_base_model',
        'trm_training_config', 'trm_site_training_config',
        'training_datasets', 'supply_chain_training_artifacts',
    ]
    for tbl in checkpoints_tables:
        op.execute(f"ALTER TABLE IF EXISTS public.{tbl} SET SCHEMA checkpoints")

    # --- audit schema ---
    audit_tables = [
        'trm_atp_decision_log', 'trm_atp_outcome',
        'trm_rebalancing_decision_log', 'trm_rebalancing_outcome',
        'trm_po_decision_log', 'trm_po_outcome',
        'trm_safety_stock_decision_log', 'trm_safety_stock_outcome',
        'trm_order_tracking_decision_log', 'trm_order_tracking_outcome',
        'trm_replay_buffer',
        'override_effectiveness_posteriors', 'override_causal_match_pairs',
        'decision_history',
    ]
    for tbl in audit_tables:
        op.execute(f"ALTER TABLE IF EXISTS public.{tbl} SET SCHEMA audit")

    # ========================================================================
    # 4. RLS policies for tenant isolation
    # ========================================================================
    for schema in ['agents', 'checkpoints', 'audit']:
        # Enable RLS on all tables in the schema that have tenant_id
        op.execute(f"""
            DO $$
            DECLARE
                tbl RECORD;
            BEGIN
                FOR tbl IN
                    SELECT t.table_name
                    FROM information_schema.tables t
                    JOIN information_schema.columns c ON t.table_name = c.table_name AND t.table_schema = c.table_schema
                    WHERE t.table_schema = '{schema}' AND c.column_name = 'tenant_id'
                LOOP
                    EXECUTE format('ALTER TABLE {schema}.%I ENABLE ROW LEVEL SECURITY', tbl.table_name);
                    EXECUTE format(
                        'CREATE POLICY tenant_isolation ON {schema}.%I FOR ALL USING (tenant_id = current_setting(''app.tenant_id'')::int)',
                        tbl.table_name
                    );
                END LOOP;
            END $$;
        """)

    # ========================================================================
    # 5. Grant access to application role
    # ========================================================================
    for schema in ['agents', 'conformal', 'checkpoints', 'audit']:
        op.execute(f"GRANT USAGE ON SCHEMA {schema} TO autonomy_user")
        op.execute(f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA {schema} TO autonomy_user")
        op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT ALL ON TABLES TO autonomy_user")

    # ========================================================================
    # 6. Add schemas to search_path so existing queries still work
    # ========================================================================
    op.execute("""
        ALTER DATABASE autonomy SET search_path TO public, agents, conformal, checkpoints, audit
    """)


def downgrade() -> None:
    # Move tables back to public
    for schema in ['agents', 'checkpoints', 'audit']:
        op.execute(f"""
            DO $$
            DECLARE
                tbl RECORD;
            BEGIN
                FOR tbl IN SELECT table_name FROM information_schema.tables WHERE table_schema = '{schema}'
                LOOP
                    EXECUTE format('ALTER TABLE {schema}.%I SET SCHEMA public', tbl.table_name);
                END LOOP;
            END $$;
        """)

    # Drop schemas
    for schema in ['agents', 'conformal', 'checkpoints', 'audit']:
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
