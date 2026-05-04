"""create_missing_powell_execution_decisions

Revision ID: 20260307_powell_exec
Revises: 20260306_active_baseline_constraint
Create Date: 2026-03-07

Create 5 missing Powell execution decision tables (idempotent):
1. powell_mo_decisions — Manufacturing Order execution decisions
2. powell_to_decisions — Transfer Order execution decisions
3. powell_quality_decisions — Quality disposition decisions
4. powell_maintenance_decisions — Maintenance scheduling decisions
5. powell_subcontracting_decisions — Subcontracting routing decisions
"""

revision = "20260307_powell_exec"
down_revision = "20260306_active_baseline_constraint"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def _table_exists(table_name: str) -> bool:
    """Check if a table already exists (idempotent migrations)."""
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
    ), {"t": table_name})
    return result.fetchone() is not None


def _index_exists(index_name: str) -> bool:
    """Check if an index already exists."""
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE indexname = :i"
    ), {"i": index_name})
    return result.fetchone() is not None


def upgrade():
    # ── 1. powell_mo_decisions ────────────────────────────────────────
    if not _table_exists("powell_mo_decisions"):
        op.create_table(
            "powell_mo_decisions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("production_order_id", sa.String(100), nullable=False),
            sa.Column("product_id", sa.String(100), nullable=False),
            sa.Column("site_id", sa.String(100), nullable=False),
            sa.Column("planned_qty", sa.Float(), nullable=False),
            sa.Column("decision_type", sa.String(50), nullable=False),
            sa.Column("sequence_position", sa.Integer(), nullable=True),
            sa.Column("priority_override", sa.Integer(), nullable=True),
            sa.Column("resource_id", sa.String(100), nullable=True),
            sa.Column("setup_time_hours", sa.Float(), nullable=True),
            sa.Column("run_time_hours", sa.Float(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("state_features", sa.JSON(), nullable=True),
            sa.Column("was_executed", sa.Boolean(), nullable=True),
            sa.Column("actual_completion_date", sa.DateTime(), nullable=True),
            sa.Column("actual_qty", sa.Float(), nullable=True),
            sa.Column("actual_yield_pct", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            # HiveSignalMixin columns
            sa.Column("urgency_at_time", sa.Float(), nullable=True),
            sa.Column("signal_type", sa.String(50), nullable=True),
            sa.Column("signal_payload", sa.JSON(), nullable=True),
            sa.Column("decision_method", sa.String(50), nullable=True),
            sa.Column("risk_bound", sa.Float(), nullable=True),
            sa.Column("risk_assessment", sa.JSON(), nullable=True),
            sa.Column("triggered_by", sa.String(100), nullable=True),
        )
    if not _index_exists("idx_mo_config"):
        op.create_index("idx_mo_config", "powell_mo_decisions", ["config_id"])
    if not _index_exists("idx_mo_product_site"):
        op.create_index("idx_mo_product_site", "powell_mo_decisions", ["product_id", "site_id"])
    if not _index_exists("idx_mo_production_order"):
        op.create_index("idx_mo_production_order", "powell_mo_decisions", ["production_order_id"])
    if not _index_exists("idx_mo_created"):
        op.create_index("idx_mo_created", "powell_mo_decisions", ["created_at"])

    # ── 2. powell_to_decisions ────────────────────────────────────────
    if not _table_exists("powell_to_decisions"):
        op.create_table(
            "powell_to_decisions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("transfer_order_id", sa.String(100), nullable=False),
            sa.Column("product_id", sa.String(100), nullable=False),
            sa.Column("source_site_id", sa.String(100), nullable=False),
            sa.Column("dest_site_id", sa.String(100), nullable=False),
            sa.Column("planned_qty", sa.Float(), nullable=False),
            sa.Column("decision_type", sa.String(50), nullable=False),
            sa.Column("transportation_mode", sa.String(50), nullable=True),
            sa.Column("estimated_transit_days", sa.Float(), nullable=True),
            sa.Column("priority", sa.Integer(), nullable=True),
            sa.Column("trigger_reason", sa.String(50), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("state_features", sa.JSON(), nullable=True),
            sa.Column("was_executed", sa.Boolean(), nullable=True),
            sa.Column("actual_ship_date", sa.Date(), nullable=True),
            sa.Column("actual_receipt_date", sa.Date(), nullable=True),
            sa.Column("actual_qty", sa.Float(), nullable=True),
            sa.Column("actual_transit_days", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            # HiveSignalMixin columns
            sa.Column("urgency_at_time", sa.Float(), nullable=True),
            sa.Column("signal_type", sa.String(50), nullable=True),
            sa.Column("signal_payload", sa.JSON(), nullable=True),
            sa.Column("decision_method", sa.String(50), nullable=True),
            sa.Column("risk_bound", sa.Float(), nullable=True),
            sa.Column("risk_assessment", sa.JSON(), nullable=True),
            sa.Column("triggered_by", sa.String(100), nullable=True),
        )
    if not _index_exists("idx_powell_to_config"):
        op.create_index("idx_powell_to_config", "powell_to_decisions", ["config_id"])
    if not _index_exists("idx_powell_to_product"):
        op.create_index("idx_powell_to_product", "powell_to_decisions", ["product_id"])
    if not _index_exists("idx_powell_to_source_dest"):
        op.create_index("idx_powell_to_source_dest", "powell_to_decisions", ["source_site_id", "dest_site_id"])
    if not _index_exists("idx_powell_to_created"):
        op.create_index("idx_powell_to_created", "powell_to_decisions", ["created_at"])

    # ── 3. powell_quality_decisions ───────────────────────────────────
    if not _table_exists("powell_quality_decisions"):
        op.create_table(
            "powell_quality_decisions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("quality_order_id", sa.String(100), nullable=False),
            sa.Column("product_id", sa.String(100), nullable=False),
            sa.Column("site_id", sa.String(100), nullable=False),
            sa.Column("lot_number", sa.String(100), nullable=True),
            sa.Column("inspection_type", sa.String(50), nullable=True),
            sa.Column("inspection_qty", sa.Float(), nullable=True),
            sa.Column("defect_rate", sa.Float(), nullable=True),
            sa.Column("defect_category", sa.String(100), nullable=True),
            sa.Column("severity_level", sa.String(20), nullable=True),
            sa.Column("disposition", sa.String(50), nullable=False),
            sa.Column("disposition_reason", sa.Text(), nullable=True),
            sa.Column("rework_cost_estimate", sa.Float(), nullable=True),
            sa.Column("scrap_cost_estimate", sa.Float(), nullable=True),
            sa.Column("service_risk_if_accepted", sa.Float(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("state_features", sa.JSON(), nullable=True),
            sa.Column("was_executed", sa.Boolean(), nullable=True),
            sa.Column("actual_disposition", sa.String(50), nullable=True),
            sa.Column("actual_rework_cost", sa.Float(), nullable=True),
            sa.Column("actual_scrap_cost", sa.Float(), nullable=True),
            sa.Column("customer_complaints_after", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            # HiveSignalMixin columns
            sa.Column("urgency_at_time", sa.Float(), nullable=True),
            sa.Column("signal_type", sa.String(50), nullable=True),
            sa.Column("signal_payload", sa.JSON(), nullable=True),
            sa.Column("decision_method", sa.String(50), nullable=True),
            sa.Column("risk_bound", sa.Float(), nullable=True),
            sa.Column("risk_assessment", sa.JSON(), nullable=True),
            sa.Column("triggered_by", sa.String(100), nullable=True),
        )
    if not _index_exists("idx_quality_config"):
        op.create_index("idx_quality_config", "powell_quality_decisions", ["config_id"])
    if not _index_exists("idx_quality_product_site"):
        op.create_index("idx_quality_product_site", "powell_quality_decisions", ["product_id", "site_id"])
    if not _index_exists("idx_quality_order"):
        op.create_index("idx_quality_order", "powell_quality_decisions", ["quality_order_id"])
    if not _index_exists("idx_quality_lot"):
        op.create_index("idx_quality_lot", "powell_quality_decisions", ["lot_number"])
    if not _index_exists("idx_quality_created"):
        op.create_index("idx_quality_created", "powell_quality_decisions", ["created_at"])

    # ── 4. powell_maintenance_decisions ───────────────────────────────
    if not _table_exists("powell_maintenance_decisions"):
        op.create_table(
            "powell_maintenance_decisions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("maintenance_order_id", sa.String(100), nullable=False),
            sa.Column("asset_id", sa.String(100), nullable=False),
            sa.Column("site_id", sa.String(100), nullable=False),
            sa.Column("maintenance_type", sa.String(50), nullable=False),
            sa.Column("decision_type", sa.String(50), nullable=False),
            sa.Column("scheduled_date", sa.Date(), nullable=True),
            sa.Column("deferred_to_date", sa.Date(), nullable=True),
            sa.Column("estimated_downtime_hours", sa.Float(), nullable=True),
            sa.Column("production_impact_units", sa.Float(), nullable=True),
            sa.Column("spare_parts_available", sa.Boolean(), nullable=True),
            sa.Column("priority", sa.Integer(), nullable=True),
            sa.Column("risk_score_if_deferred", sa.Float(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("state_features", sa.JSON(), nullable=True),
            sa.Column("was_executed", sa.Boolean(), nullable=True),
            sa.Column("actual_start_date", sa.DateTime(), nullable=True),
            sa.Column("actual_completion_date", sa.DateTime(), nullable=True),
            sa.Column("actual_downtime_hours", sa.Float(), nullable=True),
            sa.Column("breakdown_occurred", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            # HiveSignalMixin columns
            sa.Column("urgency_at_time", sa.Float(), nullable=True),
            sa.Column("signal_type", sa.String(50), nullable=True),
            sa.Column("signal_payload", sa.JSON(), nullable=True),
            sa.Column("decision_method", sa.String(50), nullable=True),
            sa.Column("risk_bound", sa.Float(), nullable=True),
            sa.Column("risk_assessment", sa.JSON(), nullable=True),
            sa.Column("triggered_by", sa.String(100), nullable=True),
        )
    if not _index_exists("idx_maintenance_config"):
        op.create_index("idx_maintenance_config", "powell_maintenance_decisions", ["config_id"])
    if not _index_exists("idx_maintenance_asset"):
        op.create_index("idx_maintenance_asset", "powell_maintenance_decisions", ["asset_id"])
    if not _index_exists("idx_maintenance_site"):
        op.create_index("idx_maintenance_site", "powell_maintenance_decisions", ["site_id"])
    if not _index_exists("idx_maintenance_type"):
        op.create_index("idx_maintenance_type", "powell_maintenance_decisions", ["maintenance_type"])
    if not _index_exists("idx_maintenance_created"):
        op.create_index("idx_maintenance_created", "powell_maintenance_decisions", ["created_at"])

    # ── 5. powell_subcontracting_decisions ────────────────────────────
    if not _table_exists("powell_subcontracting_decisions"):
        op.create_table(
            "powell_subcontracting_decisions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("product_id", sa.String(100), nullable=False),
            sa.Column("site_id", sa.String(100), nullable=False),
            sa.Column("required_qty", sa.Float(), nullable=False),
            sa.Column("routing_decision", sa.String(50), nullable=False),
            sa.Column("internal_capacity_pct", sa.Float(), nullable=True),
            sa.Column("subcontractor_id", sa.String(100), nullable=True),
            sa.Column("subcontractor_lead_time_days", sa.Float(), nullable=True),
            sa.Column("subcontractor_cost_per_unit", sa.Float(), nullable=True),
            sa.Column("internal_cost_per_unit", sa.Float(), nullable=True),
            sa.Column("quality_risk_score", sa.Float(), nullable=True),
            sa.Column("split_ratio_internal", sa.Float(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("state_features", sa.JSON(), nullable=True),
            sa.Column("was_executed", sa.Boolean(), nullable=True),
            sa.Column("actual_routing", sa.String(50), nullable=True),
            sa.Column("actual_cost_per_unit", sa.Float(), nullable=True),
            sa.Column("actual_lead_time_days", sa.Float(), nullable=True),
            sa.Column("quality_issues_count", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            # HiveSignalMixin columns
            sa.Column("urgency_at_time", sa.Float(), nullable=True),
            sa.Column("signal_type", sa.String(50), nullable=True),
            sa.Column("signal_payload", sa.JSON(), nullable=True),
            sa.Column("decision_method", sa.String(50), nullable=True),
            sa.Column("risk_bound", sa.Float(), nullable=True),
            sa.Column("risk_assessment", sa.JSON(), nullable=True),
            sa.Column("triggered_by", sa.String(100), nullable=True),
        )
    if not _index_exists("idx_subcontracting_config"):
        op.create_index("idx_subcontracting_config", "powell_subcontracting_decisions", ["config_id"])
    if not _index_exists("idx_subcontracting_product_site"):
        op.create_index("idx_subcontracting_product_site", "powell_subcontracting_decisions", ["product_id", "site_id"])
    if not _index_exists("idx_subcontracting_created"):
        op.create_index("idx_subcontracting_created", "powell_subcontracting_decisions", ["created_at"])


def downgrade():
    op.drop_table("powell_subcontracting_decisions")
    op.drop_table("powell_maintenance_decisions")
    op.drop_table("powell_quality_decisions")
    op.drop_table("powell_to_decisions")
    op.drop_table("powell_mo_decisions")
