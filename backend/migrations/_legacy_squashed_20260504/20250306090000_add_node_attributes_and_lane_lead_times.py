"""Add node attributes and lane lead time metadata."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20250306090000"
down_revision = "20250305094500"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nodes",
        sa.Column(
            "attributes",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.execute(
        "UPDATE nodes SET attributes = '{}' WHERE attributes IS NULL"
    )
    op.alter_column("nodes", "attributes", server_default=None)

    op.add_column("lanes", sa.Column("order_lead_time", sa.JSON(), nullable=True))
    op.add_column("lanes", sa.Column("supply_lead_time", sa.JSON(), nullable=True))
    op.execute(
        """UPDATE lanes
           SET order_lead_time = '{"type": "deterministic", "value": 0}'
           WHERE order_lead_time IS NULL"""
    )
    op.execute(
        """UPDATE lanes
           SET supply_lead_time = '{"type": "deterministic", "value": 1}'
           WHERE supply_lead_time IS NULL"""
    )


def downgrade() -> None:
    op.drop_column("lanes", "supply_lead_time")
    op.drop_column("lanes", "order_lead_time")
    op.drop_column("nodes", "attributes")
