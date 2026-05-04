"""Add drift detection fields to forecast pipeline tables.

Revision ID: 20260304_forecast_pipeline_drift
Revises: ddfb5f63890a
Create Date: 2026-03-04

"""
from alembic import op
import sqlalchemy as sa


revision = "20260304_forecast_pipeline_drift"
down_revision = "ddfb5f63890a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- forecast_pipeline_run: drift tracking ---
    op.add_column("forecast_pipeline_run",
        sa.Column("drift_detected", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("forecast_pipeline_run",
        sa.Column("drift_reason", sa.String(), nullable=True))
    op.add_column("forecast_pipeline_run",
        sa.Column("drift_wape_current", sa.Float(), nullable=True))
    op.add_column("forecast_pipeline_run",
        sa.Column("drift_wape_baseline", sa.Float(), nullable=True))
    op.add_column("forecast_pipeline_run",
        sa.Column("stages_executed", sa.String(20), nullable=True, server_default=sa.text("'1,2,3,4'")))

    # --- forecast_pipeline_config: drift thresholds ---
    op.add_column("forecast_pipeline_config",
        sa.Column("wape_drift_threshold", sa.Float(), nullable=False, server_default=sa.text("0.25")))
    op.add_column("forecast_pipeline_config",
        sa.Column("wape_relative_threshold", sa.Float(), nullable=False, server_default=sa.text("0.30")))
    op.add_column("forecast_pipeline_config",
        sa.Column("pattern_change_threshold", sa.Float(), nullable=False, server_default=sa.text("0.20")))
    op.add_column("forecast_pipeline_config",
        sa.Column("auto_refit_on_drift", sa.Boolean(), nullable=False, server_default=sa.text("true")))


def downgrade() -> None:
    op.drop_column("forecast_pipeline_run", "drift_detected")
    op.drop_column("forecast_pipeline_run", "drift_reason")
    op.drop_column("forecast_pipeline_run", "drift_wape_current")
    op.drop_column("forecast_pipeline_run", "drift_wape_baseline")
    op.drop_column("forecast_pipeline_run", "stages_executed")

    op.drop_column("forecast_pipeline_config", "wape_drift_threshold")
    op.drop_column("forecast_pipeline_config", "wape_relative_threshold")
    op.drop_column("forecast_pipeline_config", "pattern_change_threshold")
    op.drop_column("forecast_pipeline_config", "auto_refit_on_drift")
