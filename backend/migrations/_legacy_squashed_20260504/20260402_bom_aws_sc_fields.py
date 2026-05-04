"""Add AWS SC DM fields and bom_usage extension to product_bom

Revision ID: 20260402_bom_fields
Revises: 20260330_soc2_schemas
Create Date: 2026-04-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '20260402_bom_fields'
down_revision: Union[str, None] = '20260330_soc2_schemas'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('product_bom', sa.Column('site_id', sa.Integer(), sa.ForeignKey('site.id'), nullable=True))
    op.add_column('product_bom', sa.Column('level', sa.Integer(), nullable=True, comment='BOM level in explosion hierarchy'))
    op.add_column('product_bom', sa.Column('component_line_number', sa.Integer(), nullable=True))
    op.add_column('product_bom', sa.Column('component_quantity_uom', sa.String(20), nullable=True))
    op.add_column('product_bom', sa.Column('lifecycle_phase', sa.String(50), nullable=True))
    op.add_column('product_bom', sa.Column('assembly_cost', sa.Double(), nullable=True))
    op.add_column('product_bom', sa.Column('assembly_cost_uom', sa.String(20), nullable=True))
    op.add_column('product_bom', sa.Column('description', sa.String(500), nullable=True))
    op.add_column('product_bom', sa.Column('alternative_product_id', sa.String(100), nullable=True))
    op.add_column('product_bom', sa.Column('alternate_group_id', sa.String(100), nullable=True))
    op.add_column('product_bom', sa.Column('alternate_product_qty', sa.Double(), nullable=True))
    op.add_column('product_bom', sa.Column('alternate_product_qty_uom', sa.String(20), nullable=True))
    op.add_column('product_bom', sa.Column('ratio', sa.Double(), nullable=True, comment='Proportional split ratio for co-products / by-products'))
    op.add_column('product_bom', sa.Column('creation_date', sa.DateTime(), nullable=True))
    op.add_column('product_bom', sa.Column('change_date', sa.DateTime(), nullable=True))
    op.add_column('product_bom', sa.Column('bom_usage', sa.String(20), nullable=True, comment='Extension: planning, sales, template'))


def downgrade() -> None:
    op.drop_column('product_bom', 'bom_usage')
    op.drop_column('product_bom', 'change_date')
    op.drop_column('product_bom', 'creation_date')
    op.drop_column('product_bom', 'ratio')
    op.drop_column('product_bom', 'alternate_product_qty_uom')
    op.drop_column('product_bom', 'alternate_product_qty')
    op.drop_column('product_bom', 'alternate_group_id')
    op.drop_column('product_bom', 'alternative_product_id')
    op.drop_column('product_bom', 'description')
    op.drop_column('product_bom', 'assembly_cost_uom')
    op.drop_column('product_bom', 'assembly_cost')
    op.drop_column('product_bom', 'lifecycle_phase')
    op.drop_column('product_bom', 'component_quantity_uom')
    op.drop_column('product_bom', 'component_line_number')
    op.drop_column('product_bom', 'level')
    op.drop_column('product_bom', 'site_id')
