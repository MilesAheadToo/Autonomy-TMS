"""Add tenant_bsc_config table.

Stores per-tenant BSC weights for CDT simulation calibration loss function.
Phase 1: holding_cost_weight + backlog_cost_weight (equal default 0.5 each).
Future phases add customer, operational, and strategic pillars.

Revision ID: 20260326_tenant_bsc_config
Revises: 20260325_planning_trm_tables
Create Date: 2026-03-26
"""

from alembic import op
import sqlalchemy as sa

revision = "20260326_tenant_bsc_config"
down_revision = ("20260325_planning_trm_tables", "20260311_config_mode")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_bsc_config",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("holding_cost_weight", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("backlog_cost_weight", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("customer_weight", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("operational_weight", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("strategic_weight", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_bsc_config_tenant_id"),
    )
    op.create_index(
        "ix_tenant_bsc_config_tenant_id",
        "tenant_bsc_config",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_bsc_config_tenant_id", table_name="tenant_bsc_config")
    op.drop_table("tenant_bsc_config")
