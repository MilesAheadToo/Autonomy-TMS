"""Add AIIO status and decision_level to all powell_*_decisions tables.

Every agent decision follows the AIIO model:
- ACTIONED: Agent executed (default — no human approval needed)
- INFORMED: Surfaced to human in Decision Stream
- INSPECTED: Human reviewed, agent action stands
- OVERRIDDEN: Human rejected with alternative

Revision ID: 20260324_aiio
Revises: 20260322_site_plan_cfg
Create Date: 2026-03-24
"""
from alembic import op
import sqlalchemy as sa

revision = "20260324_aiio"
down_revision = None
branch_labels = None
depends_on = None

# All 11 powell_*_decisions tables that inherit HiveSignalMixin
POWELL_TABLES = [
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


def upgrade():
    for table in POWELL_TABLES:
        # Add status column (AIIO) — default ACTIONED
        op.add_column(
            table,
            sa.Column(
                "status",
                sa.String(20),
                nullable=False,
                server_default="ACTIONED",
                comment="AIIO: ACTIONED|INFORMED|INSPECTED|OVERRIDDEN",
            ),
        )
        op.create_index(f"ix_{table}_status", table, ["status"])

        # Add decision_level column — default execution (TRM layer)
        op.add_column(
            table,
            sa.Column(
                "decision_level",
                sa.String(20),
                nullable=False,
                server_default="execution",
                comment="Powell layer: execution|tactical|strategic",
            ),
        )
        op.create_index(f"ix_{table}_decision_level", table, ["decision_level"])

    # Backfill: all existing decisions are ACTIONED (agent executed)
    # No backfill SQL needed — server_default handles it


def downgrade():
    for table in POWELL_TABLES:
        op.drop_index(f"ix_{table}_decision_level", table_name=table)
        op.drop_column(table, "decision_level")
        op.drop_index(f"ix_{table}_status", table_name=table)
        op.drop_column(table, "status")
