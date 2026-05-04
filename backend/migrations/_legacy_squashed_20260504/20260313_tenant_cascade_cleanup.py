"""Add ondelete=CASCADE to all tenant_id and config_id FKs for comprehensive tenant deletion.

Many tables had tenant_id or config_id foreign keys without ondelete="CASCADE",
causing orphaned data when tenants were deleted. This migration adds CASCADE to all
such FKs so that database-level cascading is consistent with the explicit SQL
deletion in TenantService.delete_tenant().

Revision ID: 20260313_cascade
Revises: 99b1d0fb8f3a
Create Date: 2026-03-13
"""
from alembic import op

# revision identifiers
revision = "20260313_cascade"
down_revision = "99b1d0fb8f3a"
branch_labels = None
depends_on = None


# (table_name, column_name, referred_table, referred_column)
TENANT_FK_FIXES = [
    # Slack signals
    ("slack_connections", "tenant_id", "tenants", "id"),
    ("slack_signals", "tenant_id", "tenants", "id"),
    # Inventory projections
    ("inventory_projection", "tenant_id", "tenants", "id"),
    ("atp_projection", "tenant_id", "tenants", "id"),
    ("ctp_projection", "tenant_id", "tenants", "id"),
    ("order_promise", "tenant_id", "tenants", "id"),
    # Agent actions
    ("agent_action", "tenant_id", "tenants", "id"),
    # Monte Carlo
    ("monte_carlo_runs", "tenant_id", "tenants", "id"),
    # Pegging
    ("supply_demand_pegging", "tenant_id", "tenants", "id"),
    ("aatp_consumption_record", "tenant_id", "tenants", "id"),
    # User directives
    ("user_directives", "tenant_id", "tenants", "id"),
    # Powell training
    ("powell_training_config", "tenant_id", "tenants", "id"),
    ("powell_training_run", "tenant_id", "tenants", "id"),
    # Promotional planning
    ("promotions", "tenant_id", "tenants", "id"),
    ("promotion_history", "tenant_id", "tenants", "id"),
    # Powell agent state
    ("powell_cdc_thresholds", "tenant_id", "tenants", "id"),
    # Email signals
    ("email_connections", "tenant_id", "tenants", "id"),
    ("email_signals", "tenant_id", "tenants", "id"),
    # Planning cascade
    ("planning_policy_envelope", "tenant_id", "tenants", "id"),
    ("supply_baseline_pack", "tenant_id", "tenants", "id"),
    ("supply_commit", "tenant_id", "tenants", "id"),
    ("solver_baseline_pack", "tenant_id", "tenants", "id"),
    ("allocation_commit", "tenant_id", "tenants", "id"),
    ("planning_feedback_signal", "tenant_id", "tenants", "id"),
    ("agent_decision_metrics", "tenant_id", "tenants", "id"),
    ("layer_license", "tenant_id", "tenants", "id"),
    # Planning hierarchy
    ("planning_hierarchy_config", "tenant_id", "tenants", "id"),
    # Escalation log
    ("powell_escalation_log", "tenant_id", "tenants", "id"),
    # Decision governance
    ("decision_governance_policies", "tenant_id", "tenants", "id"),
    ("guardrail_directives", "tenant_id", "tenants", "id"),
    # Product lifecycle
    ("product_lifecycle", "tenant_id", "tenants", "id"),
    ("npi_projects", "tenant_id", "tenants", "id"),
    ("eol_plans", "tenant_id", "tenants", "id"),
    ("markdown_plans", "tenant_id", "tenants", "id"),
    ("lifecycle_history", "tenant_id", "tenants", "id"),
    # TRM training data
    ("trm_training_episodes", "tenant_id", "tenants", "id"),
    ("trm_training_batches", "tenant_id", "tenants", "id"),
    ("trm_curriculum_progress", "tenant_id", "tenants", "id"),
    ("trm_evaluation_results", "tenant_id", "tenants", "id"),
    ("trm_behavioral_cloning_data", "tenant_id", "tenants", "id"),
    ("trm_rl_experience_buffer", "tenant_id", "tenants", "id"),
    # Deployment pipeline
    ("deployment_pipeline_run", "tenant_id", "tenants", "id"),
    # Risk
    ("risk_alerts", "tenant_id", "tenants", "id"),
]

CONFIG_FK_FIXES = [
    # AWS SC entity tables (config_id → supply_chain_configs.id)
    ("company", "config_id", "supply_chain_configs", "id"),
    ("geography", "config_id", "supply_chain_configs", "id"),
    ("trading_partners", "config_id", "supply_chain_configs", "id"),
    ("product_hierarchy", "config_id", "supply_chain_configs", "id"),
    ("sourcing_rules", "config_id", "supply_chain_configs", "id"),
    ("inv_policy", "config_id", "supply_chain_configs", "id"),
    ("inv_level", "config_id", "supply_chain_configs", "id"),
    ("supply_planning_parameters", "config_id", "supply_chain_configs", "id"),
    ("production_process", "config_id", "supply_chain_configs", "id"),
    ("supply_plan", "config_id", "supply_chain_configs", "id"),
    ("forecast", "config_id", "supply_chain_configs", "id"),
    ("reservation", "config_id", "supply_chain_configs", "id"),
    ("outbound_order_line", "config_id", "supply_chain_configs", "id"),
    ("shipment", "config_id", "supply_chain_configs", "id"),
    ("inbound_order", "config_id", "supply_chain_configs", "id"),
    ("inbound_order_line", "config_id", "supply_chain_configs", "id"),
    ("inbound_order_line_schedule", "config_id", "supply_chain_configs", "id"),
    ("shipment_stop", "config_id", "supply_chain_configs", "id"),
    ("shipment_lot", "config_id", "supply_chain_configs", "id"),
    ("outbound_shipment", "config_id", "supply_chain_configs", "id"),
    ("segmentation", "config_id", "supply_chain_configs", "id"),
    ("supplementary_time_series", "config_id", "supply_chain_configs", "id"),
    ("process_header", "config_id", "supply_chain_configs", "id"),
    ("process_operation", "config_id", "supply_chain_configs", "id"),
    ("process_product", "config_id", "supply_chain_configs", "id"),
    ("customer_cost", "config_id", "supply_chain_configs", "id"),
    ("inventory_projection", "config_id", "supply_chain_configs", "id"),
    ("fulfillment_order", "config_id", "supply_chain_configs", "id"),
    ("consensus_demand", "config_id", "supply_chain_configs", "id"),
    ("backorder", "config_id", "supply_chain_configs", "id"),
    ("final_assembly_schedule", "config_id", "supply_chain_configs", "id"),
]


def _find_fk_constraint_name(conn, table_name, column_name, referred_table):
    """Find the FK constraint name by inspecting the database."""
    from sqlalchemy import inspect as sa_inspect
    try:
        inspector = sa_inspect(conn)
        for fk in inspector.get_foreign_keys(table_name):
            if (
                fk.get("referred_table") == referred_table
                and column_name in fk.get("constrained_columns", [])
            ):
                return fk.get("name")
    except Exception:
        pass
    return None


def upgrade():
    conn = op.get_bind()
    all_fixes = TENANT_FK_FIXES + CONFIG_FK_FIXES

    for table_name, col_name, ref_table, ref_col in all_fixes:
        constraint_name = _find_fk_constraint_name(conn, table_name, col_name, ref_table)
        if constraint_name is None:
            # Table or FK may not exist in this environment
            continue
        try:
            op.drop_constraint(constraint_name, table_name, type_="foreignkey")
            op.create_foreign_key(
                constraint_name,
                table_name,
                ref_table,
                [col_name],
                [ref_col],
                ondelete="CASCADE",
            )
        except Exception:
            # Table may not exist in all environments
            pass


def downgrade():
    conn = op.get_bind()
    all_fixes = TENANT_FK_FIXES + CONFIG_FK_FIXES

    for table_name, col_name, ref_table, ref_col in all_fixes:
        constraint_name = _find_fk_constraint_name(conn, table_name, col_name, ref_table)
        if constraint_name is None:
            continue
        try:
            op.drop_constraint(constraint_name, table_name, type_="foreignkey")
            op.create_foreign_key(
                constraint_name,
                table_name,
                ref_table,
                [col_name],
                [ref_col],
            )
        except Exception:
            pass
