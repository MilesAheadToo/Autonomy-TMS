"""Add scenario engine tables

Machine-speed what-if planning: agent_scenarios, agent_scenario_actions,
scenario_templates.

See docs/internal/SCENARIO_ENGINE.md for full architecture.

Revision ID: 20260324_scenario_engine
Revises: 20260323_ek
"""

from alembic import op
import sqlalchemy as sa

revision = "20260324_scenario_engine"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # --- agent_scenarios ---
    op.create_table(
        "agent_scenarios",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id"), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("parent_scenario_id", sa.Integer(), sa.ForeignKey("agent_scenarios.id"), nullable=True),
        sa.Column("trigger_decision_id", sa.Integer(), nullable=True),
        sa.Column("trigger_trm_type", sa.String(50), nullable=False),
        sa.Column("trigger_context", sa.JSON(), nullable=True),
        sa.Column("decision_level", sa.String(20), nullable=False, server_default="execution"),
        sa.Column("status", sa.String(20), nullable=False, server_default="CREATED"),
        sa.Column("raw_bsc_score", sa.Float(), nullable=True),
        sa.Column("compound_likelihood", sa.Float(), nullable=True),
        sa.Column("urgency_discount", sa.Float(), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("bsc_breakdown", sa.JSON(), nullable=True),
        sa.Column("context_weights", sa.JSON(), nullable=True),
        sa.Column("simulation_days", sa.Integer(), nullable=True),
        sa.Column("simulation_seed", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("scored_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_agent_scenarios_config", "agent_scenarios", ["config_id", "status"])
    op.create_index("ix_agent_scenarios_trigger", "agent_scenarios", ["trigger_trm_type", "created_at"])
    op.create_index("ix_agent_scenarios_tenant", "agent_scenarios", ["tenant_id", "status"])

    # --- agent_scenario_actions ---
    op.create_table(
        "agent_scenario_actions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scenario_id", sa.Integer(), sa.ForeignKey("agent_scenarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trm_type", sa.String(50), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("action_params", sa.JSON(), nullable=True),
        sa.Column("responsible_agent", sa.String(50), nullable=True),
        sa.Column("decision_likelihood", sa.Float(), nullable=True),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("estimated_benefit", sa.Float(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PROPOSED"),
        sa.Column("actioned_decision_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_scenario_actions_scenario", "agent_scenario_actions", ["scenario_id"])

    # --- scenario_templates ---
    op.create_table(
        "scenario_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trm_type", sa.String(50), nullable=False),
        sa.Column("template_key", sa.String(100), nullable=False),
        sa.Column("template_name", sa.String(255), nullable=False),
        sa.Column("template_params", sa.JSON(), nullable=True),
        sa.Column("alpha", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("beta_param", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("uses_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_scenario_templates_trm", "scenario_templates", ["trm_type", "tenant_id"])


def downgrade():
    op.drop_table("agent_scenario_actions")
    op.drop_table("scenario_templates")
    op.drop_table("agent_scenarios")
