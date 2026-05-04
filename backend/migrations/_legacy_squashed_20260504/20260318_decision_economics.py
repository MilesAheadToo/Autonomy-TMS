"""Add economic impact columns to all powell_*_decisions tables and
create tenant_decision_thresholds for per-TRM-type routing.

Implements 3-dimensional decision routing (Urgency × Likelihood × Benefit)
grounded in Kahneman & Tversky's Prospect Theory (1979).

Revision ID: 20260318_econ
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa

revision = "20260318_econ"
down_revision = None
branch_labels = None
depends_on = None

# All 11 powell_*_decisions tables share HiveSignalMixin columns
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


def upgrade() -> None:
    # Add economic columns to all 11 powell decision tables
    for table in POWELL_TABLES:
        op.add_column(
            table,
            sa.Column("cost_of_inaction", sa.Float(), nullable=True,
                       comment="$/period cost of doing nothing"),
        )
        op.add_column(
            table,
            sa.Column("time_pressure", sa.Float(), nullable=True,
                       comment="0-1 how fast the decision window closes"),
        )
        op.add_column(
            table,
            sa.Column("expected_benefit", sa.Float(), nullable=True,
                       comment="$ net gain from recommended action"),
        )

    # Add benefit_threshold to tenant_bsc_config
    op.add_column(
        "tenant_bsc_config",
        sa.Column("benefit_threshold", sa.Float(), nullable=False,
                   server_default="0.0",
                   comment="Min $ benefit for auto-action (0 = disabled)"),
    )

    # Create per-TRM-type threshold table
    op.create_table(
        "tenant_decision_thresholds",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False, index=True),
        sa.Column("trm_type", sa.String(50), nullable=False),
        sa.Column("urgency_threshold", sa.Float(), nullable=True),
        sa.Column("likelihood_threshold", sa.Float(), nullable=True),
        sa.Column("benefit_threshold", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                   server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("tenant_id", "trm_type",
                            name="uq_tenant_trm_threshold"),
    )


def downgrade() -> None:
    op.drop_table("tenant_decision_thresholds")
    op.drop_column("tenant_bsc_config", "benefit_threshold")
    for table in POWELL_TABLES:
        op.drop_column(table, "expected_benefit")
        op.drop_column(table, "time_pressure")
        op.drop_column(table, "cost_of_inaction")
