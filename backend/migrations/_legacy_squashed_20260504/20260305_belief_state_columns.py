"""Add missing columns to powell_belief_state table.

The SQLAlchemy model defines columns (nonconformity_score, nonconformity_threshold,
empirical_coverage, interval_width_mean, last_recalibration, drift_detected,
drift_score, distribution_fit, observation_count) that were never migrated to the DB.
Also drops the obsolete 'alpha' column.

Revision ID: 20260305_belief_cols
Revises: 20260304_data_drift_monitor, 20260305_metric_config
Create Date: 2026-03-05 19:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260305_belief_cols"
down_revision = ("20260304_data_drift_monitor", "20260305_metric_config")
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(bind)
    if table_name not in insp.get_table_names():
        return False
    return any(col["name"] == column_name for col in insp.get_columns(table_name))


TABLE = "powell_belief_state"

# (column_name, type, comment)
NEW_COLUMNS = [
    ("nonconformity_score", sa.Float(), "Current nonconformity score (lower = more typical)"),
    ("nonconformity_threshold", sa.Float(), "Threshold for flagging unusual situations"),
    ("empirical_coverage", sa.Float(), "Actual observed coverage rate over history"),
    ("interval_width_mean", sa.Float(), "Mean interval width (measure of precision)"),
    ("last_recalibration", sa.DateTime(), "When intervals were last recalibrated"),
    ("drift_detected", sa.Boolean(), "Whether significant drift has been detected"),
    ("drift_score", sa.Float(), "Drift detection score (e.g., CUSUM statistic)"),
    ("distribution_fit", sa.JSON(), "Fitted distribution metadata: {dist_type, params, ks_pvalue, is_normal_like}"),
    ("observation_count", sa.Integer(), "Number of observations used for this belief state"),
]


def upgrade() -> None:
    bind = op.get_bind()
    for col_name, col_type, comment in NEW_COLUMNS:
        if not _has_column(bind, TABLE, col_name):
            kwargs = {"nullable": True, "comment": comment}
            if col_name == "drift_detected":
                kwargs["server_default"] = sa.text("false")
            if col_name == "observation_count":
                kwargs["server_default"] = sa.text("0")
            op.add_column(TABLE, sa.Column(col_name, col_type, **kwargs))

    # Drop obsolete 'alpha' column (replaced by conformal_coverage)
    if _has_column(bind, TABLE, "alpha"):
        op.drop_column(TABLE, "alpha")


def downgrade() -> None:
    bind = op.get_bind()
    for col_name, col_type, _comment in reversed(NEW_COLUMNS):
        if _has_column(bind, TABLE, col_name):
            op.drop_column(TABLE, col_name)

    if not _has_column(bind, TABLE, "alpha"):
        op.add_column(TABLE, sa.Column("alpha", sa.Float(), nullable=True))
