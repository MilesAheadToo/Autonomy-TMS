"""fix item_node_configs product_id to string

Update item_node_configs.product_id from Integer to String to match
the new Product table schema.

Revision ID: 20260203_fix_item_node
Revises: 20260203_rename_round_id
Create Date: 2026-02-03 14:45:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260203_fix_item_node'
down_revision = '20260203_rename_round_id'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Drop the old foreign key constraint (points to items.id)
    op.drop_constraint('item_node_configs_product_id_fkey', 'item_node_configs', type_='foreignkey')

    # 2. Clear existing data that references old integer IDs
    op.execute("DELETE FROM item_node_configs")

    # 3. Change product_id from Integer to String
    op.execute("""
        ALTER TABLE item_node_configs
        ALTER COLUMN product_id TYPE VARCHAR(100)
        USING product_id::VARCHAR(100)
    """)

    # 4. Create new foreign key to product.id
    op.create_foreign_key(
        'fk_item_node_configs_product',
        'item_node_configs', 'product',
        ['product_id'], ['id'],
        ondelete='CASCADE'
    )


def downgrade():
    # 1. Drop the new foreign key
    op.drop_constraint('fk_item_node_configs_product', 'item_node_configs', type_='foreignkey')

    # 2. Revert to Integer (will fail if non-numeric data exists)
    op.execute("""
        ALTER TABLE item_node_configs
        ALTER COLUMN product_id TYPE INTEGER
        USING NULLIF(product_id, '')::INTEGER
    """)

    # 3. Recreate old foreign key to items.id
    op.create_foreign_key(
        'item_node_configs_product_id_fkey',
        'item_node_configs', 'items',
        ['product_id'], ['id'],
        ondelete='CASCADE'
    )
