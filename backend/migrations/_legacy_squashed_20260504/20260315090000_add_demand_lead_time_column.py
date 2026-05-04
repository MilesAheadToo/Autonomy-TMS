"""Ensure lanes have demand_lead_time with default 1.

Revision ID: 20260315090000
Revises: 20260312090000
Create Date: 2026-03-15 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260315090000"
down_revision = "20260312090000"
branch_labels = None
depends_on = None


def _get_lane_columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns("lanes")}


def upgrade():
    bind = op.get_bind()
    columns = _get_lane_columns(bind)

    # Align naming with supply_lead_time -> demand_lead_time
    rename_from = None
    if "demand_lead_time" in columns:
        rename_from = None
    elif "demand_leadtime" in columns:
        rename_from = "demand_leadtime"
    elif "order_lead_time" in columns:
        rename_from = "order_lead_time"
    elif "order_leadtime" in columns:
        rename_from = "order_leadtime"

    if rename_from:
        op.execute(
            f"ALTER TABLE lanes CHANGE COLUMN {rename_from} demand_lead_time JSON NULL"
        )
    elif "demand_lead_time" not in columns:
        op.add_column("lanes", sa.Column("demand_lead_time", sa.JSON(), nullable=True))

    # Default any missing values to deterministic 1 to match supply_lead_time naming
    op.execute(
        """UPDATE lanes
           SET demand_lead_time = '{"type": "deterministic", "value": 1}'
           WHERE demand_lead_time IS NULL"""
    )


def downgrade():
    bind = op.get_bind()
    columns = _get_lane_columns(bind)

    # Revert to the legacy name if present, otherwise drop the column
    if "demand_lead_time" in columns:
        if "order_lead_time" not in columns:
            op.execute(
                "ALTER TABLE lanes CHANGE COLUMN demand_lead_time order_lead_time JSON NULL"
            )
        else:
            op.drop_column("lanes", "demand_lead_time")
