"""Add site_planning_config table and erp_planning_params column on inv_policy.

Stores ERP-specific planning heuristic configuration per (product, site).
The digital twin simulation dispatches to the correct heuristic based on
planning_method and lot_sizing_rule. Raw ERP fields stored in erp_params JSONB.

See DIGITAL_TWIN.md sections 8A.9 (per-site config model) and 8C.5 (storage approach).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260322_site_plan_cfg"
down_revision = "20260319_model_ckpt"
branch_labels = None
depends_on = None


def upgrade():
    # --- 1. Create site_planning_config table ---
    op.create_table(
        "site_planning_config",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "config_id", sa.Integer(),
            sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id", sa.Integer(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "site_id", sa.Integer(),
            sa.ForeignKey("site.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_id", sa.String(100), nullable=False),

        # Universal planning parameters
        sa.Column("planning_method", sa.String(30), nullable=False, server_default="REORDER_POINT"),
        sa.Column("lot_sizing_rule", sa.String(30), nullable=False, server_default="LOT_FOR_LOT"),

        # Lot sizing parameters
        sa.Column("fixed_lot_size", sa.Double(), nullable=True),
        sa.Column("min_order_quantity", sa.Double(), nullable=True),
        sa.Column("max_order_quantity", sa.Double(), nullable=True),
        sa.Column("order_multiple", sa.Double(), nullable=True),

        # Time fences
        sa.Column("frozen_horizon_days", sa.Integer(), nullable=True),
        sa.Column("planning_time_fence_days", sa.Integer(), nullable=True),

        # Forecast consumption
        sa.Column("forecast_consumption_mode", sa.String(10), nullable=True),
        sa.Column("forecast_consumption_fwd_days", sa.Integer(), nullable=True),
        sa.Column("forecast_consumption_bwd_days", sa.Integer(), nullable=True),

        # Procurement
        sa.Column("procurement_type", sa.String(20), nullable=True),
        sa.Column("strategy_group", sa.String(10), nullable=True),
        sa.Column("mrp_controller", sa.String(10), nullable=True),

        # ERP extension
        sa.Column("erp_source", sa.String(20), nullable=True),
        sa.Column("erp_params", JSONB(), nullable=True),

        # Audit
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # Indexes
    op.create_index(
        "ix_spc_config_site_product",
        "site_planning_config",
        ["config_id", "site_id", "product_id"],
        unique=True,
    )
    op.create_index(
        "ix_spc_tenant",
        "site_planning_config",
        ["tenant_id"],
    )

    # SOC II: Row-Level Security
    op.execute("ALTER TABLE site_planning_config ENABLE ROW LEVEL SECURITY")

    # --- 2. Add erp_planning_params JSONB column to inv_policy ---
    op.add_column(
        "inv_policy",
        sa.Column("erp_planning_params", JSONB(), nullable=True),
    )


def downgrade():
    op.drop_column("inv_policy", "erp_planning_params")
    op.execute("ALTER TABLE site_planning_config DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_spc_tenant", table_name="site_planning_config")
    op.drop_index("ix_spc_config_site_product", table_name="site_planning_config")
    op.drop_table("site_planning_config")
