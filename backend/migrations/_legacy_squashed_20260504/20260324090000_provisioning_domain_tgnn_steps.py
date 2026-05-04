"""Replace execution_tgnn provisioning step with 4 domain-specific steps.

Revision ID: 20260324090000
Revises: 20260323100000
Create Date: 2026-03-24 09:00:00.000000

Replaces the single execution_tgnn step in config_provisioning_status with:
  - lgbm_forecast  : LightGBM baseline demand forecasting
  - demand_tgnn    : Demand Planning tGNN
  - supply_tgnn    : Supply Planning tGNN
  - inventory_tgnn : Inventory Optimization tGNN

The execution_tgnn_* columns are dropped (the old rows will have NULL for the
new columns, which is the correct "pending" default — the application reads
the column value and treats NULL as "pending").
"""

from alembic import op
import sqlalchemy as sa


revision = "20260324090000"
down_revision = "20260323100000"
branch_labels = None
depends_on = None

_TABLE = "config_provisioning_status"

# New columns to add (12 total: 4 steps × 3 columns each)
_NEW_COLUMNS = [
    ("lgbm_forecast_status",    sa.String(20),  "pending"),
    ("lgbm_forecast_at",        sa.DateTime(),  None),
    ("lgbm_forecast_error",     sa.Text(),      None),
    ("demand_tgnn_status",      sa.String(20),  "pending"),
    ("demand_tgnn_at",          sa.DateTime(),  None),
    ("demand_tgnn_error",       sa.Text(),      None),
    ("supply_tgnn_status",      sa.String(20),  "pending"),
    ("supply_tgnn_at",          sa.DateTime(),  None),
    ("supply_tgnn_error",       sa.Text(),      None),
    ("inventory_tgnn_status",   sa.String(20),  "pending"),
    ("inventory_tgnn_at",       sa.DateTime(),  None),
    ("inventory_tgnn_error",    sa.Text(),      None),
]

# Old columns to drop
_OLD_COLUMNS = [
    "execution_tgnn_status",
    "execution_tgnn_at",
    "execution_tgnn_error",
]


def upgrade():
    # Add the four new step column triples
    for col_name, col_type, col_default in _NEW_COLUMNS:
        kwargs = {"nullable": True}
        if col_default is not None:
            kwargs["server_default"] = col_default
        op.add_column(_TABLE, sa.Column(col_name, col_type, **kwargs))

    # Drop the old execution_tgnn columns
    for col_name in _OLD_COLUMNS:
        op.drop_column(_TABLE, col_name)


def downgrade():
    # Restore execution_tgnn columns
    op.add_column(_TABLE, sa.Column("execution_tgnn_status", sa.String(20),
                                    nullable=True, server_default="pending"))
    op.add_column(_TABLE, sa.Column("execution_tgnn_at", sa.DateTime(), nullable=True))
    op.add_column(_TABLE, sa.Column("execution_tgnn_error", sa.Text(), nullable=True))

    # Drop the four domain-specific step column triples
    for col_name, _col_type, _col_default in _NEW_COLUMNS:
        op.drop_column(_TABLE, col_name)
