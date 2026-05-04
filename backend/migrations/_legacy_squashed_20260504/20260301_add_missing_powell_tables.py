"""add_missing_powell_tables

Revision ID: 20260301_missing_powell
Revises: 20260227_participant_rename
Create Date: 2026-03-01

Add missing Powell/TRM tables and agent_decisions columns:
1. powell_forecast_adjustment_decisions — signal-driven forecast adjustments
2. powell_buffer_decisions — inventory buffer parameter adjustments
3. override_effectiveness_posteriors — Bayesian Beta posteriors for override quality
4. override_causal_match_pairs — propensity-score matched pairs for causal inference
5. Add 4 missing columns to agent_decisions (agent_counterfactual_reward, etc.)
"""

revision = "20260301_missing_powell"
down_revision = "20260227_participant_rename"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    # 1. powell_forecast_adjustment_decisions
    op.create_table(
        "powell_forecast_adjustment_decisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.String(100), nullable=False),
        sa.Column("site_id", sa.String(100), nullable=False),
        sa.Column("signal_source", sa.String(50), nullable=False),
        sa.Column("signal_type", sa.String(50), nullable=False),
        sa.Column("signal_text", sa.Text(), nullable=True),
        sa.Column("signal_confidence", sa.Float(), nullable=True),
        sa.Column("current_forecast_value", sa.Float(), nullable=True),
        sa.Column("adjustment_direction", sa.String(20), nullable=False),
        sa.Column("adjustment_magnitude", sa.Float(), nullable=True),
        sa.Column("adjustment_pct", sa.Float(), nullable=True),
        sa.Column("adjusted_forecast_value", sa.Float(), nullable=True),
        sa.Column("time_horizon_periods", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("state_features", sa.JSON(), nullable=True),
        sa.Column("was_applied", sa.Boolean(), nullable=True),
        sa.Column("actual_demand", sa.Float(), nullable=True),
        sa.Column("forecast_error_before", sa.Float(), nullable=True),
        sa.Column("forecast_error_after", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        # HiveSignalMixin columns
        sa.Column("signal_context", sa.JSON(), nullable=True),
        sa.Column("urgency_at_time", sa.Float(), nullable=True),
        sa.Column("triggered_by", sa.String(200), nullable=True),
        sa.Column("signals_emitted", sa.JSON(), nullable=True),
        sa.Column("cycle_phase", sa.String(50), nullable=True),
        sa.Column("cycle_id", sa.String(100), nullable=True),
    )
    op.create_index("idx_forecast_adj_config", "powell_forecast_adjustment_decisions", ["config_id"])
    op.create_index("idx_forecast_adj_product_site", "powell_forecast_adjustment_decisions", ["product_id", "site_id"])
    op.create_index("idx_forecast_adj_signal_source", "powell_forecast_adjustment_decisions", ["signal_source"])
    op.create_index("idx_forecast_adj_signal_type", "powell_forecast_adjustment_decisions", ["signal_type"])
    op.create_index("idx_forecast_adj_created", "powell_forecast_adjustment_decisions", ["created_at"])

    # 2. powell_buffer_decisions
    op.create_table(
        "powell_buffer_decisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.String(100), nullable=False),
        sa.Column("location_id", sa.String(100), nullable=False),
        sa.Column("baseline_ss", sa.Float(), nullable=False),
        sa.Column("multiplier", sa.Float(), nullable=False),
        sa.Column("adjusted_ss", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("demand_cv", sa.Float(), nullable=True),
        sa.Column("current_dos", sa.Float(), nullable=True),
        sa.Column("seasonal_index", sa.Float(), nullable=True),
        sa.Column("recent_stockout_count", sa.Integer(), nullable=True),
        sa.Column("state_features", sa.JSON(), nullable=True),
        sa.Column("was_applied", sa.Boolean(), nullable=True),
        sa.Column("actual_stockout_occurred", sa.Boolean(), nullable=True),
        sa.Column("actual_dos_after", sa.Float(), nullable=True),
        sa.Column("excess_holding_cost", sa.Float(), nullable=True),
        sa.Column("actual_service_level", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        # HiveSignalMixin columns
        sa.Column("signal_context", sa.JSON(), nullable=True),
        sa.Column("urgency_at_time", sa.Float(), nullable=True),
        sa.Column("triggered_by", sa.String(200), nullable=True),
        sa.Column("signals_emitted", sa.JSON(), nullable=True),
        sa.Column("cycle_phase", sa.String(50), nullable=True),
        sa.Column("cycle_id", sa.String(100), nullable=True),
    )
    op.create_index("idx_buffer_config", "powell_buffer_decisions", ["config_id"])
    op.create_index("idx_buffer_product_loc", "powell_buffer_decisions", ["product_id", "location_id"])
    op.create_index("idx_buffer_created", "powell_buffer_decisions", ["created_at"])

    # 3. override_effectiveness_posteriors
    op.create_table(
        "override_effectiveness_posteriors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("trm_type", sa.String(50), nullable=False),
        sa.Column("site_key", sa.String(100), nullable=True),
        sa.Column("alpha", sa.Float(), default=1.0, nullable=False),
        sa.Column("beta_param", sa.Float(), default=1.0, nullable=False),
        sa.Column("expected_effectiveness", sa.Float(), default=0.5),
        sa.Column("observation_count", sa.Integer(), default=0),
        sa.Column("training_weight", sa.Float(), default=0.85),
        sa.Column("last_updated", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "trm_type", "site_key", name="uq_posterior_user_trm_site"),
    )
    op.create_index("idx_posterior_user_trm", "override_effectiveness_posteriors", ["user_id", "trm_type"])

    # 4. override_causal_match_pairs
    op.create_table(
        "override_causal_match_pairs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("overridden_decision_id", sa.Integer(), sa.ForeignKey("powell_site_agent_decisions.id"), nullable=True),
        sa.Column("control_decision_id", sa.Integer(), sa.ForeignKey("powell_site_agent_decisions.id"), nullable=True),
        sa.Column("trm_type", sa.String(50), nullable=False),
        sa.Column("state_distance", sa.Float(), nullable=True),
        sa.Column("propensity_score", sa.Float(), nullable=True),
        sa.Column("override_reward", sa.Float(), nullable=True),
        sa.Column("control_reward", sa.Float(), nullable=True),
        sa.Column("treatment_effect", sa.Float(), nullable=True),
        sa.Column("match_quality", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("idx_causal_match_trm", "override_causal_match_pairs", ["trm_type", "match_quality"])

    # 5. Add missing columns to agent_decisions
    op.add_column("agent_decisions", sa.Column("agent_counterfactual_reward", sa.Float(), nullable=True))
    op.add_column("agent_decisions", sa.Column("human_actual_reward", sa.Float(), nullable=True))
    op.add_column("agent_decisions", sa.Column("override_delta", sa.Float(), nullable=True))
    op.add_column("agent_decisions", sa.Column("override_classification", sa.String(20), nullable=True))

    # 6. Add missing columns to performance_metrics
    op.add_column("performance_metrics", sa.Column("override_effectiveness_rate", sa.Float(), nullable=True))
    op.add_column("performance_metrics", sa.Column("override_net_delta", sa.Float(), nullable=True))

    # 7. Add HiveSignalMixin columns to existing Powell tables
    hive_columns = [
        ("signal_context", sa.JSON()),
        ("urgency_at_time", sa.Float()),
        ("triggered_by", sa.String(200)),
        ("signals_emitted", sa.JSON()),
        ("cycle_phase", sa.String(50)),
        ("cycle_id", sa.String(100)),
    ]
    for table in [
        "powell_atp_decisions",
        "powell_po_decisions",
        "powell_rebalance_decisions",
        "powell_order_exceptions",
    ]:
        for col_name, col_type in hive_columns:
            op.add_column(table, sa.Column(col_name, col_type, nullable=True))


def downgrade():
    # Remove HiveSignalMixin columns from existing Powell tables
    for table in [
        "powell_atp_decisions",
        "powell_po_decisions",
        "powell_rebalance_decisions",
        "powell_order_exceptions",
    ]:
        for col_name in ["signal_context", "urgency_at_time", "triggered_by", "signals_emitted", "cycle_phase", "cycle_id"]:
            op.drop_column(table, col_name)

    op.drop_column("performance_metrics", "override_net_delta")
    op.drop_column("performance_metrics", "override_effectiveness_rate")

    op.drop_column("agent_decisions", "override_classification")
    op.drop_column("agent_decisions", "override_delta")
    op.drop_column("agent_decisions", "human_actual_reward")
    op.drop_column("agent_decisions", "agent_counterfactual_reward")

    op.drop_table("override_causal_match_pairs")
    op.drop_table("override_effectiveness_posteriors")
    op.drop_table("powell_buffer_decisions")
    op.drop_table("powell_forecast_adjustment_decisions")
