"""Add metric_config column to supply_chain_configs for Gartner SCOR metric hierarchy.

Revision ID: 20260305_metric_config
Revises: 20260323100000
Create Date: 2026-03-05 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260305_metric_config"
down_revision = "20260323100000"
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(bind)
    if table_name not in insp.get_table_names():
        return False
    return any(col["name"] == column_name for col in insp.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "supply_chain_configs", "metric_config"):
        op.add_column(
            "supply_chain_configs",
            sa.Column(
                "metric_config",
                sa.JSON(),
                nullable=True,
                comment="Gartner SCOR metric config overrides. Keys: sop_weights, tgnn_weights, trm_weights.",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "supply_chain_configs", "metric_config"):
        op.drop_column("supply_chain_configs", "metric_config")
