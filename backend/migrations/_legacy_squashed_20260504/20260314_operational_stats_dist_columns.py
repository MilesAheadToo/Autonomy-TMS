"""Add *_dist JSON columns to production_process and vendor_lead_times.

Stores stochastic distribution parameters computed from SAP operational
statistics (HANA SQL aggregation).  Format:
  {"type": "lognormal", "mean_log": ..., "stddev_log": ..., "min": ..., "max": ...}
NULL = use deterministic base field value.

Revision ID: 20260314_op_stats_dist
Revises: 20260313_sap_file_mapping
Create Date: 2026-03-14
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "20260314_op_stats_dist"
down_revision = "20260313_sap_file_mapping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # production_process: 5 new distribution columns
    op.add_column(
        "production_process",
        sa.Column("operation_time_dist", sa.JSON, nullable=True),
    )
    op.add_column(
        "production_process",
        sa.Column("setup_time_dist", sa.JSON, nullable=True),
    )
    op.add_column(
        "production_process",
        sa.Column("yield_dist", sa.JSON, nullable=True),
    )
    op.add_column(
        "production_process",
        sa.Column("mtbf_dist", sa.JSON, nullable=True),
    )
    op.add_column(
        "production_process",
        sa.Column("mttr_dist", sa.JSON, nullable=True),
    )

    # vendor_lead_times: 1 new distribution column
    op.add_column(
        "vendor_lead_times",
        sa.Column("lead_time_dist", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vendor_lead_times", "lead_time_dist")
    op.drop_column("production_process", "mttr_dist")
    op.drop_column("production_process", "mtbf_dist")
    op.drop_column("production_process", "yield_dist")
    op.drop_column("production_process", "setup_time_dist")
    op.drop_column("production_process", "operation_time_dist")
