"""Add forecast_median to forecast-related tables.

Revision ID: 20260323100000
Revises: 20260323090000
Create Date: 2026-03-23 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260323100000"
down_revision = "20260323090000"
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(bind)
    if table_name not in insp.get_table_names():
        return False
    return any(col["name"] == column_name for col in insp.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()

    if _has_column(bind, "forecast", "forecast_median") is False and "forecast" in sa.inspect(bind).get_table_names():
        op.add_column("forecast", sa.Column("forecast_median", sa.Float(), nullable=True))
        op.execute("UPDATE forecast SET forecast_median = COALESCE(forecast_p50, forecast_quantity) WHERE forecast_median IS NULL")

    if _has_column(bind, "demand_plan", "forecast_median") is False and "demand_plan" in sa.inspect(bind).get_table_names():
        op.add_column("demand_plan", sa.Column("forecast_median", sa.Float(), nullable=True))
        op.execute(
            "UPDATE demand_plan "
            "SET forecast_median = COALESCE(forecast_p50, consensus_forecast, statistical_forecast) "
            "WHERE forecast_median IS NULL"
        )

    if (
        _has_column(bind, "forecast_pipeline_prediction", "forecast_median") is False
        and "forecast_pipeline_prediction" in sa.inspect(bind).get_table_names()
    ):
        op.add_column("forecast_pipeline_prediction", sa.Column("forecast_median", sa.Float(), nullable=True))
        op.execute(
            "UPDATE forecast_pipeline_prediction "
            "SET forecast_median = COALESCE(forecast_p50) "
            "WHERE forecast_median IS NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "forecast_pipeline_prediction" in insp.get_table_names() and _has_column(bind, "forecast_pipeline_prediction", "forecast_median"):
        op.drop_column("forecast_pipeline_prediction", "forecast_median")
    if "demand_plan" in insp.get_table_names() and _has_column(bind, "demand_plan", "forecast_median"):
        op.drop_column("demand_plan", "forecast_median")
    if "forecast" in insp.get_table_names() and _has_column(bind, "forecast", "forecast_median"):
        op.drop_column("forecast", "forecast_median")
