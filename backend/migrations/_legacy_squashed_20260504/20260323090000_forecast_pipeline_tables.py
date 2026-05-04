"""Create forecast pipeline tables.

Revision ID: 20260323090000
Revises: 20260322093000
Create Date: 2026-03-23 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260323090000"
down_revision = "20260322093000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forecast_pipeline_config",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id"), nullable=False),
        sa.Column("time_bucket", sa.String(length=10), nullable=False, server_default="W"),
        sa.Column("forecast_horizon", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("min_clusters", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("max_clusters", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("min_observations", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("forecast_metric", sa.String(length=20), nullable=False, server_default="wape"),
        sa.Column("model_type", sa.String(length=50), nullable=False, server_default="clustered_naive"),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_forecast_pipeline_config_group_id", "forecast_pipeline_config", ["customer_id"])
    op.create_index("ix_forecast_pipeline_config_config_id", "forecast_pipeline_config", ["config_id"])

    op.create_table(
        "forecast_pipeline_run",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("pipeline_config_id", sa.Integer(), sa.ForeignKey("forecast_pipeline_config.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id"), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("run_log", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("model_type", sa.String(length=50), nullable=False, server_default="clustered_naive"),
        sa.Column("forecast_metric", sa.String(length=20), nullable=False, server_default="wape"),
        sa.Column("records_processed", sa.Integer(), nullable=True),
    )
    op.create_index("ix_forecast_pipeline_run_pipeline_config_id", "forecast_pipeline_run", ["pipeline_config_id"])
    op.create_index("ix_forecast_pipeline_run_group_id", "forecast_pipeline_run", ["customer_id"])
    op.create_index("ix_forecast_pipeline_run_config_id", "forecast_pipeline_run", ["config_id"])

    op.create_table(
        "forecast_pipeline_cluster",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("forecast_pipeline_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("unique_id", sa.String(length=200), nullable=False),
        sa.Column("product_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("centroid_features", sa.JSON(), nullable=True),
    )
    op.create_index("ix_fp_cluster_run_unique", "forecast_pipeline_cluster", ["run_id", "unique_id"])
    op.create_index("ix_fp_cluster_run_cluster", "forecast_pipeline_cluster", ["run_id", "cluster_id"])

    op.create_table(
        "forecast_pipeline_prediction",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("forecast_pipeline_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("forecast_date", sa.Date(), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.String(length=50), nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=False, server_default="v1"),
        sa.Column("forecast_p10", sa.Float(), nullable=True),
        sa.Column("forecast_p50", sa.Float(), nullable=False),
        sa.Column("forecast_p90", sa.Float(), nullable=True),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_forecast_pipeline_prediction_run_id", "forecast_pipeline_prediction", ["run_id"])
    op.create_index("ix_forecast_pipeline_prediction_forecast_date", "forecast_pipeline_prediction", ["forecast_date"])
    op.create_index("ix_fp_pred_run_prod_site_date", "forecast_pipeline_prediction", ["run_id", "product_id", "site_id", "forecast_date"])

    op.create_table(
        "forecast_pipeline_metric",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("forecast_pipeline_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric_scope", sa.String(length=20), nullable=False),
        sa.Column("scope_key", sa.String(length=100), nullable=False, server_default="overall"),
        sa.Column("metric_name", sa.String(length=30), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=True),
    )
    op.create_index("ix_forecast_pipeline_metric_run_id", "forecast_pipeline_metric", ["run_id"])
    op.create_index("ix_fp_metric_run_scope_name", "forecast_pipeline_metric", ["run_id", "metric_scope", "scope_key", "metric_name"])

    op.create_table(
        "forecast_pipeline_feature_importance",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("forecast_pipeline_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feature_name", sa.String(length=100), nullable=False),
        sa.Column("importance_score", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
    )
    op.create_index("ix_forecast_pipeline_feature_importance_run_id", "forecast_pipeline_feature_importance", ["run_id"])
    op.create_index("ix_fp_importance_run_rank", "forecast_pipeline_feature_importance", ["run_id", "rank"])

    op.create_table(
        "forecast_pipeline_publish_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("forecast_pipeline_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("published_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("records_published", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_forecast_pipeline_publish_log_run_id", "forecast_pipeline_publish_log", ["run_id"])
    op.create_index("ix_fp_publish_run", "forecast_pipeline_publish_log", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_fp_publish_run", table_name="forecast_pipeline_publish_log")
    op.drop_index("ix_forecast_pipeline_publish_log_run_id", table_name="forecast_pipeline_publish_log")
    op.drop_table("forecast_pipeline_publish_log")

    op.drop_index("ix_fp_importance_run_rank", table_name="forecast_pipeline_feature_importance")
    op.drop_index("ix_forecast_pipeline_feature_importance_run_id", table_name="forecast_pipeline_feature_importance")
    op.drop_table("forecast_pipeline_feature_importance")

    op.drop_index("ix_fp_metric_run_scope_name", table_name="forecast_pipeline_metric")
    op.drop_index("ix_forecast_pipeline_metric_run_id", table_name="forecast_pipeline_metric")
    op.drop_table("forecast_pipeline_metric")

    op.drop_index("ix_fp_pred_run_prod_site_date", table_name="forecast_pipeline_prediction")
    op.drop_index("ix_forecast_pipeline_prediction_forecast_date", table_name="forecast_pipeline_prediction")
    op.drop_index("ix_forecast_pipeline_prediction_run_id", table_name="forecast_pipeline_prediction")
    op.drop_table("forecast_pipeline_prediction")

    op.drop_index("ix_fp_cluster_run_cluster", table_name="forecast_pipeline_cluster")
    op.drop_index("ix_fp_cluster_run_unique", table_name="forecast_pipeline_cluster")
    op.drop_table("forecast_pipeline_cluster")

    op.drop_index("ix_forecast_pipeline_run_config_id", table_name="forecast_pipeline_run")
    op.drop_index("ix_forecast_pipeline_run_group_id", table_name="forecast_pipeline_run")
    op.drop_index("ix_forecast_pipeline_run_pipeline_config_id", table_name="forecast_pipeline_run")
    op.drop_table("forecast_pipeline_run")

    op.drop_index("ix_forecast_pipeline_config_config_id", table_name="forecast_pipeline_config")
    op.drop_index("ix_forecast_pipeline_config_group_id", table_name="forecast_pipeline_config")
    op.drop_table("forecast_pipeline_config")
