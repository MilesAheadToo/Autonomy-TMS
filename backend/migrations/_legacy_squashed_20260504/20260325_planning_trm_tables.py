"""Add planning TRM decision tables.

Revision ID: 20260325_planning_trm_tables
Revises: 93f865a0dab9
Create Date: 2026-03-25 09:00:00.000000

Creates four new planning TRM decision tables:
  - powell_demand_adjustment_decisions
  - powell_inventory_adjustment_decisions
  - powell_supply_adjustment_decisions
  - powell_rccp_adjustment_decisions
"""

from alembic import op
import sqlalchemy as sa


revision = "20260325_planning_trm_tables"
down_revision = "93f865a0dab9"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    from sqlalchemy import inspect
    conn = op.get_bind()
    return inspect(conn).has_table(table_name)


def upgrade() -> None:
    # --- powell_demand_adjustment_decisions ---
    if not _table_exists("powell_demand_adjustment_decisions"):
        op.create_table(
            "powell_demand_adjustment_decisions",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=True),
            sa.Column("config_id", sa.Integer, nullable=False),
            sa.Column("product_id", sa.String, nullable=False),
            sa.Column("site_id", sa.String, nullable=False),
            sa.Column("period_week", sa.Date, nullable=True),
            sa.Column("gnn_p50_forecast", sa.Numeric, nullable=True),
            sa.Column("adjustment_factor", sa.Numeric, nullable=True),
            sa.Column("adjusted_forecast", sa.Numeric, nullable=True),
            sa.Column("confidence", sa.Numeric, nullable=True),
            sa.Column("urgency", sa.Numeric, nullable=True),
            sa.Column("reasoning", sa.Text, nullable=True),
            sa.Column("decision_source", sa.String, server_default="demand_adjustment_trm"),
            # Outcome columns (populated by OutcomeCollector at +4 weeks)
            sa.Column("actual_demand", sa.Numeric, nullable=True),
            sa.Column("mape_before", sa.Numeric, nullable=True),
            sa.Column("mape_after", sa.Numeric, nullable=True),
            sa.Column("outcome_collected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )

    # --- powell_inventory_adjustment_decisions ---
    if not _table_exists("powell_inventory_adjustment_decisions"):
        op.create_table(
            "powell_inventory_adjustment_decisions",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=True),
            sa.Column("config_id", sa.Integer, nullable=False),
            sa.Column("product_id", sa.String, nullable=False),
            sa.Column("site_id", sa.String, nullable=False),
            sa.Column("gnn_ss_quantity", sa.Numeric, nullable=True),
            sa.Column("ss_adjustment_delta", sa.Numeric, nullable=True),
            sa.Column("adjusted_ss_quantity", sa.Numeric, nullable=True),
            sa.Column("confidence", sa.Numeric, nullable=True),
            sa.Column("urgency", sa.Numeric, nullable=True),
            sa.Column("reasoning", sa.Text, nullable=True),
            sa.Column("decision_source", sa.String, server_default="inventory_adjustment_trm"),
            # Outcome columns (populated at +2 weeks)
            sa.Column("actual_stockout_rate", sa.Numeric, nullable=True),
            sa.Column("holding_cost_actual", sa.Numeric, nullable=True),
            sa.Column("stockout_cost_actual", sa.Numeric, nullable=True),
            sa.Column("outcome_collected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )

    # --- powell_supply_adjustment_decisions ---
    if not _table_exists("powell_supply_adjustment_decisions"):
        op.create_table(
            "powell_supply_adjustment_decisions",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=True),
            sa.Column("config_id", sa.Integer, nullable=False),
            sa.Column("product_id", sa.String, nullable=False),
            sa.Column("site_id", sa.String, nullable=False),
            sa.Column("period_week", sa.Date, nullable=True),
            sa.Column("gnn_supply_qty", sa.Numeric, nullable=True),
            sa.Column("adjustment_factor", sa.Numeric, nullable=True),
            sa.Column("adjusted_supply_qty", sa.Numeric, nullable=True),
            sa.Column("confidence", sa.Numeric, nullable=True),
            sa.Column("urgency", sa.Numeric, nullable=True),
            sa.Column("reasoning", sa.Text, nullable=True),
            sa.Column("decision_source", sa.String, server_default="supply_adjustment_trm"),
            # Outcome columns (populated at +7 days)
            sa.Column("actual_receipt_qty", sa.Numeric, nullable=True),
            sa.Column("execution_rate", sa.Numeric, nullable=True),
            sa.Column("outcome_collected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )

    # --- powell_rccp_adjustment_decisions ---
    if not _table_exists("powell_rccp_adjustment_decisions"):
        op.create_table(
            "powell_rccp_adjustment_decisions",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=True),
            sa.Column("config_id", sa.Integer, nullable=False),
            sa.Column("site_id", sa.String, nullable=False),
            sa.Column("resource_id", sa.String, nullable=False),
            sa.Column("period_week", sa.Date, nullable=True),
            sa.Column("gnn_utilisation_pct", sa.Numeric, nullable=True),
            sa.Column("overtime_delta_hours", sa.Numeric, nullable=True),
            sa.Column("mps_defer_flag", sa.Boolean, nullable=True),
            sa.Column("escalate_to_sop", sa.Boolean, nullable=True),
            sa.Column("confidence", sa.Numeric, nullable=True),
            sa.Column("urgency", sa.Numeric, nullable=True),
            sa.Column("reasoning", sa.Text, nullable=True),
            sa.Column("decision_source", sa.String, server_default="rccp_adjustment_trm"),
            # Outcome columns (populated at +2 weeks)
            sa.Column("actual_utilisation_pct", sa.Numeric, nullable=True),
            sa.Column("overtime_cost_actual", sa.Numeric, nullable=True),
            sa.Column("backlog_units_actual", sa.Numeric, nullable=True),
            sa.Column("outcome_collected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    for table in [
        "powell_rccp_adjustment_decisions",
        "powell_supply_adjustment_decisions",
        "powell_inventory_adjustment_decisions",
        "powell_demand_adjustment_decisions",
    ]:
        if _table_exists(table):
            op.drop_table(table)
