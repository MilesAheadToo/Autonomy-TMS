"""Register planning hierarchy tables in Alembic (retroactive)

These tables were created via SQLAlchemy create_all() at startup
but were not tracked by Alembic. This migration retroactively
registers them for SOC II schema audit compliance.

Uses IF NOT EXISTS to handle databases where tables already exist.

Revision ID: 20260326_hierarchy
Revises: a7630db18e62
Create Date: 2026-03-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '20260326_hierarchy'
down_revision: Union[str, None] = 'a7630db18e62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    """Check if a table already exists in the database."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT EXISTS (SELECT FROM pg_tables WHERE tablename = :t)"),
        {"t": table_name},
    )
    return result.scalar()


def upgrade() -> None:
    # --- Site Hierarchy Node ---
    if not _table_exists("site_hierarchy_node"):
        op.create_table(
            "site_hierarchy_node",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("geography_id", sa.String(100), sa.ForeignKey("geography.id"), nullable=True),
            sa.Column("site_id", sa.Integer(), sa.ForeignKey("site.id"), nullable=True),
            sa.Column("parent_id", sa.Integer(), sa.ForeignKey("site_hierarchy_node.id"), nullable=True),
            sa.Column("hierarchy_level", sa.String(20), nullable=False),
            sa.Column("code", sa.String(50), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("hierarchy_path", sa.String(500), nullable=False, index=True),
            sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_plannable", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("default_lead_time_days", sa.Integer(), nullable=True),
            sa.Column("default_capacity", sa.Float(), nullable=True),
            sa.Column("gnn_node_features", postgresql.JSON(), nullable=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        )
        op.create_index("idx_site_hierarchy_path", "site_hierarchy_node", ["hierarchy_path"])
        op.create_index("idx_site_hierarchy_level", "site_hierarchy_node", ["hierarchy_level", "tenant_id"])

    # --- Product Hierarchy Node ---
    if not _table_exists("product_hierarchy_node"):
        op.create_table(
            "product_hierarchy_node",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("product_hierarchy_id", sa.String(100), sa.ForeignKey("product_hierarchy.id"), nullable=True),
            sa.Column("product_id", sa.String(100), sa.ForeignKey("product.id"), nullable=True),
            sa.Column("parent_id", sa.Integer(), sa.ForeignKey("product_hierarchy_node.id"), nullable=True),
            sa.Column("hierarchy_level", sa.String(20), nullable=False),
            sa.Column("code", sa.String(50), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("hierarchy_path", sa.String(500), nullable=False, index=True),
            sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_plannable", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("default_lead_time_days", sa.Integer(), nullable=True),
            sa.Column("base_demand_pattern", sa.String(50), nullable=True),
            sa.Column("demand_split_factors", postgresql.JSON(), nullable=True),
            sa.Column("gnn_node_features", postgresql.JSON(), nullable=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        )
        op.create_index("idx_product_hierarchy_path", "product_hierarchy_node", ["hierarchy_path"])
        op.create_index("idx_product_hierarchy_level", "product_hierarchy_node", ["hierarchy_level", "tenant_id"])

    # --- Planning Hierarchy Config ---
    if not _table_exists("planning_hierarchy_config"):
        op.create_table(
            "planning_hierarchy_config",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id"), nullable=True),
            sa.Column("planning_type", sa.String(50), nullable=False),
            sa.Column("site_hierarchy_level", sa.String(20), nullable=False, server_default="SITE"),
            sa.Column("product_hierarchy_level", sa.String(20), nullable=False, server_default="PRODUCT"),
            sa.Column("time_bucket", sa.String(20), nullable=False, server_default="WEEK"),
            sa.Column("horizon_months", sa.Integer(), nullable=False, server_default="6"),
            sa.Column("frozen_periods", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("slushy_periods", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("update_frequency_hours", sa.Integer(), nullable=False, server_default="168"),
            sa.Column("powell_policy_class", sa.String(20), nullable=False, server_default="vfa"),
            sa.Column("gnn_model_type", sa.String(50), nullable=True),
            sa.Column("gnn_checkpoint_path", sa.String(500), nullable=True),
            sa.Column("parent_planning_type", sa.String(50), nullable=True),
            sa.Column("consistency_tolerance", sa.Float(), nullable=False, server_default="0.10"),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("planning_hierarchy_config")
    op.drop_table("product_hierarchy_node")
    op.drop_table("site_hierarchy_node")
