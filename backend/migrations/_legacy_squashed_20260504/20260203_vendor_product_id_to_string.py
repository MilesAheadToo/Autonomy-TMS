"""migrate vendor tables to use product table with string id

Updates vendor_products and vendor_lead_times tables to reference the
new Product table (product.id) instead of the legacy items table.

Changes:
- vendor_products.product_id: Integer -> String(100)
- vendor_lead_times.product_id: Integer -> String(100)
- Foreign keys updated to reference product.id

Revision ID: 20260203_vendor_product_id_to_string
Revises: 20260202_alt_to_scen
Create Date: 2026-02-03 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260203_vendor_product_id_to_string'
down_revision = '20260202_alt_to_scen'
branch_labels = None
depends_on = None


def upgrade():
    # ========================================================================
    # vendor_products: Migrate product_id from Integer to String
    # ========================================================================

    # 1. Drop the old foreign key constraint
    op.drop_constraint('vendor_products_product_id_fkey', 'vendor_products', type_='foreignkey')

    # 2. Drop the unique constraint that includes product_id
    op.drop_constraint('uq_vendor_product_effective', 'vendor_products', type_='unique')

    # 3. Drop the index on product_id
    op.drop_index('ix_vendor_products_product_id', table_name='vendor_products')

    # 4. Alter column type from Integer to String
    # First, we need to handle any existing data - convert integer to string
    op.execute("""
        ALTER TABLE vendor_products
        ALTER COLUMN product_id TYPE VARCHAR(100)
        USING product_id::VARCHAR(100)
    """)

    # 5. Recreate the foreign key constraint to reference product.id
    op.create_foreign_key(
        'fk_vendor_products_product',
        'vendor_products', 'product',
        ['product_id'], ['id'],
        ondelete='CASCADE'
    )

    # 6. Recreate the unique constraint
    op.create_unique_constraint(
        'uq_vendor_product_effective',
        'vendor_products',
        ['tpartner_id', 'product_id', 'eff_start_date']
    )

    # 7. Recreate the index
    op.create_index('ix_vendor_products_product_id', 'vendor_products', ['product_id'])

    # ========================================================================
    # vendor_lead_times: Migrate product_id from Integer to String
    # ========================================================================

    # 1. Drop the old foreign key constraint
    op.drop_constraint('vendor_lead_times_product_id_fkey', 'vendor_lead_times', type_='foreignkey')

    # 2. Drop the index on product_id
    op.drop_index('ix_vendor_lead_times_product_id', table_name='vendor_lead_times')

    # 3. Alter column type from Integer to String
    op.execute("""
        ALTER TABLE vendor_lead_times
        ALTER COLUMN product_id TYPE VARCHAR(100)
        USING product_id::VARCHAR(100)
    """)

    # 4. Recreate the foreign key constraint to reference product.id
    op.create_foreign_key(
        'fk_vendor_lead_times_product',
        'vendor_lead_times', 'product',
        ['product_id'], ['id'],
        ondelete='CASCADE'
    )

    # 5. Recreate the index
    op.create_index('ix_vendor_lead_times_product_id', 'vendor_lead_times', ['product_id'])


def downgrade():
    # ========================================================================
    # vendor_lead_times: Revert product_id from String to Integer
    # ========================================================================

    # 1. Drop the new foreign key constraint
    op.drop_constraint('fk_vendor_lead_times_product', 'vendor_lead_times', type_='foreignkey')

    # 2. Drop the index
    op.drop_index('ix_vendor_lead_times_product_id', table_name='vendor_lead_times')

    # 3. Alter column type back to Integer (will fail if non-numeric data exists)
    op.execute("""
        ALTER TABLE vendor_lead_times
        ALTER COLUMN product_id TYPE INTEGER
        USING NULLIF(product_id, '')::INTEGER
    """)

    # 4. Recreate the old foreign key constraint to items.id
    op.create_foreign_key(
        'vendor_lead_times_product_id_fkey',
        'vendor_lead_times', 'items',
        ['product_id'], ['id'],
        ondelete='CASCADE'
    )

    # 5. Recreate the index
    op.create_index('ix_vendor_lead_times_product_id', 'vendor_lead_times', ['product_id'])

    # ========================================================================
    # vendor_products: Revert product_id from String to Integer
    # ========================================================================

    # 1. Drop the new foreign key constraint
    op.drop_constraint('fk_vendor_products_product', 'vendor_products', type_='foreignkey')

    # 2. Drop the unique constraint
    op.drop_constraint('uq_vendor_product_effective', 'vendor_products', type_='unique')

    # 3. Drop the index
    op.drop_index('ix_vendor_products_product_id', table_name='vendor_products')

    # 4. Alter column type back to Integer (will fail if non-numeric data exists)
    op.execute("""
        ALTER TABLE vendor_products
        ALTER COLUMN product_id TYPE INTEGER
        USING NULLIF(product_id, '')::INTEGER
    """)

    # 5. Recreate the old foreign key constraint to items.id
    op.create_foreign_key(
        'vendor_products_product_id_fkey',
        'vendor_products', 'items',
        ['product_id'], ['id'],
        ondelete='CASCADE'
    )

    # 6. Recreate the unique constraint
    op.create_unique_constraint(
        'uq_vendor_product_effective',
        'vendor_products',
        ['tpartner_id', 'product_id', 'eff_start_date']
    )

    # 7. Recreate the index
    op.create_index('ix_vendor_products_product_id', 'vendor_products', ['product_id'])
