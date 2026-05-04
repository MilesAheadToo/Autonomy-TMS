"""Add override tracking columns to all powell_*_decisions tables

Adds override_action, override_values, original_values,
override_reason_code, override_reason_text, override_user_id,
override_at to all 11 decision tables via HiveSignalMixin.

Revision ID: 20260315_override
Revises: 20260315_history_tables
Create Date: 2026-03-15
"""
from alembic import op
import sqlalchemy as sa

revision = "20260315_override"
down_revision = "20260315_history"
branch_labels = None
depends_on = None

TABLES = [
    "powell_atp_decisions",
    "powell_rebalance_decisions",
    "powell_po_decisions",
    "powell_order_exceptions",
    "powell_mo_decisions",
    "powell_to_decisions",
    "powell_quality_decisions",
    "powell_maintenance_decisions",
    "powell_subcontracting_decisions",
    "powell_forecast_adjustment_decisions",
    "powell_buffer_decisions",
]

COLUMNS = [
    ("override_action", sa.String(20)),
    ("override_values", sa.JSON),
    ("original_values", sa.JSON),
    ("override_reason_code", sa.String(50)),
    ("override_reason_text", sa.Text),
    ("override_user_id", sa.Integer),
    ("override_at", sa.DateTime),
]


def upgrade():
    for table in TABLES:
        for col_name, col_type in COLUMNS:
            op.add_column(table, sa.Column(col_name, col_type, nullable=True))


def downgrade():
    for table in TABLES:
        for col_name, _ in COLUMNS:
            op.drop_column(table, col_name)
