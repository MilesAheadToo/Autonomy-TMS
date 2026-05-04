"""Add LightGBM metadata columns to forecast_pipeline_run.

Revision ID: 20260312_lgbm_forecast
Revises: ddfb5f63890a
Create Date: 2026-03-12 00:00:00.000000

Extension to forecast_pipeline_run to track LightGBM training outcomes:
  - lgbm_checkpoint_path: path to the cluster checkpoint directory
  - lgbm_wape_p50: training WAPE for the P50 quantile model
  - lgbm_series_count: number of series that used LightGBM (sufficient history)
  - lgbm_fallback_count: number of series that fell back to Holt-Winters

All columns are nullable — existing rows (Holt-Winters-only runs) remain valid.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260312_lgbm_forecast"
down_revision: Union[str, None] = "ddfb5f63890a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "forecast_pipeline_run",
        sa.Column("lgbm_checkpoint_path", sa.String(500), nullable=True),
    )
    op.add_column(
        "forecast_pipeline_run",
        sa.Column("lgbm_wape_p50", sa.Float(), nullable=True),
    )
    op.add_column(
        "forecast_pipeline_run",
        sa.Column("lgbm_series_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "forecast_pipeline_run",
        sa.Column("lgbm_fallback_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("forecast_pipeline_run", "lgbm_fallback_count")
    op.drop_column("forecast_pipeline_run", "lgbm_series_count")
    op.drop_column("forecast_pipeline_run", "lgbm_wape_p50")
    op.drop_column("forecast_pipeline_run", "lgbm_checkpoint_path")
