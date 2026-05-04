"""Add scenario_events table for what-if event injection.

Revision ID: 20260318_scenario_events
Revises: 20260318_outbound_order
"""

revision = "20260318_scenario_events"
down_revision = "20260318_outbound_order"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "scenario_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("affected_entities", sa.JSON(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("cdc_triggered", sa.JSON(), nullable=True),
        sa.Column("decisions_generated", sa.Integer(), server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="APPLIED"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("reverted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_scenario_event_config", "scenario_events", ["config_id"])
    op.create_index("idx_scenario_event_tenant", "scenario_events", ["tenant_id", "created_at"])
    op.create_index("idx_scenario_event_type", "scenario_events", ["event_type"])


def downgrade():
    op.drop_index("idx_scenario_event_type", table_name="scenario_events")
    op.drop_index("idx_scenario_event_tenant", table_name="scenario_events")
    op.drop_index("idx_scenario_event_config", table_name="scenario_events")
    op.drop_table("scenario_events")
