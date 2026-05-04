"""add_mps_key_materials

Revision ID: a93b79577147
Revises: 430a780e55b4
Create Date: 2026-01-22 06:49:42.175239

Add MPS key materials support:
1. Adds is_key_material flag to product_bom table
2. Creates mps_key_material_requirements table for rough-cut planning

Key materials are critical components that require planning at the MPS level:
- Long lead time items (>4 weeks)
- Bottleneck/constrained resources
- High-value components
- Strategic materials with limited suppliers

This follows industry standard practice where:
- MPS: Plans finished goods + key materials (strategic/rough-cut)
- MRP: Plans all components (tactical/detailed)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a93b79577147'
down_revision: Union[str, None] = '430a780e55b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =========================================================================
    # 1. Add is_key_material flag to product_bom
    # =========================================================================
    op.add_column(
        'product_bom',
        sa.Column('is_key_material', sa.String(10), server_default='false', nullable=False)
    )

    # =========================================================================
    # 2. Create mps_key_material_requirements table
    # =========================================================================
    op.create_table(
        'mps_key_material_requirements',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),

        # Plan Reference
        sa.Column('plan_id', sa.Integer, sa.ForeignKey('mps_plans.id', ondelete='CASCADE'), nullable=False, index=True),

        # Parent MPS Item (Finished Good)
        sa.Column('mps_item_id', sa.Integer, sa.ForeignKey('mps_plan_items.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('parent_product_id', sa.Integer, sa.ForeignKey('items.id'), nullable=False, index=True),

        # Key Material (Component)
        sa.Column('key_material_product_id', sa.Integer, sa.ForeignKey('items.id'), nullable=False, index=True),
        sa.Column('key_material_site_id', sa.Integer, sa.ForeignKey('nodes.id'), nullable=False, index=True),

        # BOM Relationship
        sa.Column('bom_level', sa.Integer, nullable=False),
        sa.Column('component_quantity', sa.Float, nullable=False),
        sa.Column('scrap_percentage', sa.Float, nullable=False, server_default='0.0'),

        # Time-Phased Gross Requirements
        sa.Column('weekly_gross_requirements', postgresql.JSON, nullable=False),

        # Total Requirements
        sa.Column('total_gross_requirement', sa.Float, nullable=False, server_default='0.0'),

        # Key Material Flags
        sa.Column('is_bottleneck', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('is_long_lead_time', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('is_strategic', sa.Boolean, nullable=False, server_default='false'),

        # Lead Time Information
        sa.Column('procurement_lead_time_days', sa.Integer, nullable=True),

        # Metadata
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.current_timestamp()),
    )

    # Create indexes for performance
    op.create_index('idx_key_mat_plan_id', 'mps_key_material_requirements', ['plan_id'])
    op.create_index('idx_key_mat_mps_item_id', 'mps_key_material_requirements', ['mps_item_id'])
    op.create_index('idx_key_mat_parent_product', 'mps_key_material_requirements', ['parent_product_id'])
    op.create_index('idx_key_mat_key_material', 'mps_key_material_requirements', ['key_material_product_id'])
    op.create_index('idx_key_mat_site', 'mps_key_material_requirements', ['key_material_site_id'])


def downgrade() -> None:
    # Drop mps_key_material_requirements table
    op.drop_index('idx_key_mat_site', 'mps_key_material_requirements')
    op.drop_index('idx_key_mat_key_material', 'mps_key_material_requirements')
    op.drop_index('idx_key_mat_parent_product', 'mps_key_material_requirements')
    op.drop_index('idx_key_mat_mps_item_id', 'mps_key_material_requirements')
    op.drop_index('idx_key_mat_plan_id', 'mps_key_material_requirements')
    op.drop_table('mps_key_material_requirements')

    # Remove is_key_material column from product_bom
    op.drop_column('product_bom', 'is_key_material')
