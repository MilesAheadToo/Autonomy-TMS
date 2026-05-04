"""Add data_drift_records and data_drift_alerts tables.

The DataDriftMonitor provides long-horizon distributional shift detection:
- data_drift_records: one measurement per (config, product, site, window, type, date)
- data_drift_alerts:  aggregated action-threshold breaches per config per day

Revision ID: 20260304_data_drift_monitor
Revises: 20260304_po_line_shipped_qty
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260304_data_drift_monitor"
down_revision = "20260304_po_line_shipped_qty"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # ── data_drift_records ────────────────────────────────────────────────────
    if "data_drift_records" not in existing_tables:
        op.create_table(
            "data_drift_records",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id"), nullable=False),
            sa.Column("product_id", sa.String(100), sa.ForeignKey("product.id"), nullable=True),
            sa.Column("site_id", sa.Integer(), sa.ForeignKey("site.id"), nullable=True),
            sa.Column("analysis_date", sa.Date(), server_default=sa.text("CURRENT_DATE"), nullable=False),
            sa.Column("baseline_start", sa.Date(), nullable=True),
            sa.Column("baseline_end", sa.Date(), nullable=True),
            sa.Column("window_start", sa.Date(), nullable=True),
            sa.Column("window_end", sa.Date(), nullable=True),
            sa.Column("window_days", sa.Integer(), nullable=False),
            sa.Column("drift_type", sa.String(30), nullable=False),
            sa.Column("psi_score", sa.Double(), nullable=True),
            sa.Column("ks_statistic", sa.Double(), nullable=True),
            sa.Column("ks_p_value", sa.Double(), nullable=True),
            sa.Column("js_divergence", sa.Double(), nullable=True),
            sa.Column("mean_shift", sa.Double(), nullable=True),
            sa.Column("variance_ratio", sa.Double(), nullable=True),
            sa.Column("drift_score", sa.Double(), nullable=True),
            sa.Column("drift_severity", sa.String(20), server_default=sa.text("'none'"), nullable=True),
            sa.Column("drift_detected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("baseline_stats", postgresql.JSONB(), nullable=True),
            sa.Column("window_stats", postgresql.JSONB(), nullable=True),
            sa.Column("metrics", postgresql.JSONB(), nullable=True),
            sa.Column("alert_sent", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("escalated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("escalation_log_id", sa.Integer(),
                      sa.ForeignKey("powell_escalation_log.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("idx_drift_config_date", "data_drift_records", ["config_id", "analysis_date"])
        op.create_index("idx_drift_product_site", "data_drift_records", ["product_id", "site_id"])
        op.create_index("idx_drift_window", "data_drift_records", ["window_days"])
        op.create_index("idx_drift_severity", "data_drift_records", ["drift_severity"])
        op.create_index("idx_drift_detected", "data_drift_records", ["drift_detected", "analysis_date"])

    # ── data_drift_alerts ─────────────────────────────────────────────────────
    if "data_drift_alerts" not in existing_tables:
        op.create_table(
            "data_drift_alerts",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id"), nullable=False),
            sa.Column("alert_date", sa.Date(), server_default=sa.text("CURRENT_DATE"), nullable=False),
            sa.Column("max_drift_score", sa.Double(), nullable=True),
            sa.Column("max_severity", sa.String(20), nullable=True),
            sa.Column("affected_products", sa.Integer(), nullable=True),
            sa.Column("affected_sites", sa.Integer(), nullable=True),
            sa.Column("dominant_drift_type", sa.String(30), nullable=True),
            sa.Column("psi_triggered", sa.Boolean(), server_default=sa.text("false"), nullable=True),
            sa.Column("ks_triggered", sa.Boolean(), server_default=sa.text("false"), nullable=True),
            sa.Column("calibration_triggered", sa.Boolean(), server_default=sa.text("false"), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("acknowledged", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("acknowledged_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
            sa.Column("resolution_notes", sa.Text(), nullable=True),
            sa.Column("escalation_log_id", sa.Integer(),
                      sa.ForeignKey("powell_escalation_log.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("idx_drift_alert_config", "data_drift_alerts", ["config_id", "alert_date"])
        op.create_index("idx_drift_alert_unacked", "data_drift_alerts", ["acknowledged", "alert_date"])
        op.create_index("idx_drift_alert_severity", "data_drift_alerts", ["max_severity"])


def downgrade() -> None:
    op.drop_table("data_drift_alerts")
    op.drop_table("data_drift_records")
