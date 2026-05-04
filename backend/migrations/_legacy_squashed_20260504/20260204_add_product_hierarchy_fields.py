"""Add product hierarchy fields for breadcrumb display

Revision ID: 20260204_add_product_hierarchy_fields
Revises: 20260204_training_to_learning
Create Date: 2026-02-04

This migration adds category, family, and product_group columns to the product
table to support product hierarchy breadcrumb display in the UI.

Hierarchy format: Category > Family > Group > Product
Example: Frozen > Proteins > Chicken > Chicken Breast IQF
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers
revision = '20260204_add_product_hierarchy_fields'
down_revision = '20260204_training_to_learning'
branch_labels = None
depends_on = None


def column_exists(table_name, column_name, conn):
    """Check if a column exists in a table."""
    result = conn.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = :table AND column_name = :column
    """), {"table": table_name, "column": column_name})
    return result.fetchone() is not None


def upgrade() -> None:
    """Add hierarchy columns to product table."""
    conn = op.get_bind()

    # Add category column if it doesn't exist
    if not column_exists('product', 'category', conn):
        op.add_column('product', sa.Column(
            'category',
            sa.String(100),
            nullable=True,
            comment='Top-level category (e.g., Frozen, Refrigerated)'
        ))

    # Add family column if it doesn't exist
    if not column_exists('product', 'family', conn):
        op.add_column('product', sa.Column(
            'family',
            sa.String(100),
            nullable=True,
            comment='Product family (e.g., Proteins, Dairy)'
        ))

    # Add product_group column if it doesn't exist
    if not column_exists('product', 'product_group', conn):
        op.add_column('product', sa.Column(
            'product_group',
            sa.String(100),
            nullable=True,
            comment='Product group (e.g., Chicken, Beef)'
        ))

    # Create indexes for efficient filtering
    op.create_index(
        'ix_product_category',
        'product',
        ['category'],
        unique=False,
        if_not_exists=True
    )
    op.create_index(
        'ix_product_family',
        'product',
        ['family'],
        unique=False,
        if_not_exists=True
    )


def downgrade() -> None:
    """Remove hierarchy columns from product table."""
    conn = op.get_bind()

    # Drop indexes first
    op.drop_index('ix_product_family', table_name='product', if_exists=True)
    op.drop_index('ix_product_category', table_name='product', if_exists=True)

    # Drop columns
    if column_exists('product', 'product_group', conn):
        op.drop_column('product', 'product_group')

    if column_exists('product', 'family', conn):
        op.drop_column('product', 'family')

    if column_exists('product', 'category', conn):
        op.drop_column('product', 'category')
