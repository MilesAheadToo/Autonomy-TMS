"""Add config_id and tenant_id to powell_site_agent_checkpoints.

These columns enable filtering site tGNN checkpoints by config and tenant,
which is required for multi-tenant checkpoint isolation (SOC II) and for
the provisioning service to verify checkpoint persistence.

Revision ID: 20260403_site_ckpt
Revises: a7630db18e62
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa


revision = "20260403_site_ckpt"
down_revision = "a7630db18e62"
branch_labels = None
depends_on = None


def _column_exists(conn, table, column):
    """Check if a column exists in a table."""
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c"
    ), {"t": table, "c": column})
    return result.first() is not None


def upgrade():
    conn = op.get_bind()

    # Add config_id to powell_site_agent_checkpoints
    if not _column_exists(conn, "powell_site_agent_checkpoints", "config_id"):
        op.add_column(
            "powell_site_agent_checkpoints",
            sa.Column(
                "config_id",
                sa.Integer(),
                sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"),
                nullable=True,
                comment="SC config this checkpoint was trained for",
            ),
        )
        op.create_index(
            "ix_site_ckpt_config_id",
            "powell_site_agent_checkpoints",
            ["config_id"],
        )

    # Add tenant_id to powell_site_agent_checkpoints
    if not _column_exists(conn, "powell_site_agent_checkpoints", "tenant_id"):
        op.add_column(
            "powell_site_agent_checkpoints",
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=True,
                comment="Tenant owning this checkpoint (SOC II isolation)",
            ),
        )
        op.create_index(
            "ix_site_ckpt_tenant_id",
            "powell_site_agent_checkpoints",
            ["tenant_id"],
        )


def downgrade():
    conn = op.get_bind()

    if _column_exists(conn, "powell_site_agent_checkpoints", "tenant_id"):
        op.drop_index("ix_site_ckpt_tenant_id", table_name="powell_site_agent_checkpoints")
        op.drop_column("powell_site_agent_checkpoints", "tenant_id")

    if _column_exists(conn, "powell_site_agent_checkpoints", "config_id"):
        op.drop_index("ix_site_ckpt_config_id", table_name="powell_site_agent_checkpoints")
        op.drop_column("powell_site_agent_checkpoints", "config_id")
