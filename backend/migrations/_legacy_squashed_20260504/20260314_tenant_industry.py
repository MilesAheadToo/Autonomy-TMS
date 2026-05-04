"""Add industry column to tenants table.

Stores the customer's industry vertical which drives default stochastic
distribution parameters for new supply chain configs.

Revision ID: 20260314_tenant_industry
Revises: 20260314_op_stats_dist
Create Date: 2026-03-14
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "20260314_tenant_industry"
down_revision = "20260314_op_stats_dist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the enum type first
    tenant_industry_enum = sa.Enum(
        'food_beverage', 'pharmaceutical', 'automotive', 'electronics',
        'chemical', 'industrial_equipment', 'consumer_goods', 'metals_mining',
        'aerospace_defense', 'building_materials', 'textile_apparel',
        'wholesale_distribution', 'third_party_logistics',
        name='tenant_industry_enum',
    )
    tenant_industry_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "tenants",
        sa.Column(
            "industry",
            tenant_industry_enum,
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "industry")
    sa.Enum(name='tenant_industry_enum').drop(op.get_bind(), checkfirst=True)
