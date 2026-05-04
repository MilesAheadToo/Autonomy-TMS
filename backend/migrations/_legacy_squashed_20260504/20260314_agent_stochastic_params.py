"""Add agent_stochastic_params table.

Per-agent stochastic variable values with is_default flag and source tracking.
Each TRM agent type gets its own stochastic parameters, allowing industry
defaults to be selectively overridden by SAP import or manual editing.

Revision ID: 20260314_agent_stochastic_params
Revises: 20260314_tenant_industry
Create Date: 2026-03-14
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "20260314_agent_stochastic_params"
down_revision = "20260314_tenant_industry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_stochastic_params",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "config_id",
            sa.Integer(),
            sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "site_id",
            sa.Integer(),
            sa.ForeignKey("site.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("trm_type", sa.String(50), nullable=False, index=True),
        sa.Column("param_name", sa.String(80), nullable=False),
        sa.Column("distribution", sa.JSON(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "source",
            sa.String(20),
            nullable=False,
            server_default="industry_default",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "config_id", "site_id", "trm_type", "param_name",
            name="uq_agent_stochastic_param",
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_stochastic_params")
