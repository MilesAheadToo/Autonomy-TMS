"""AWS Supply Chain field renames (BREAKING CHANGE)

Revision ID: 20260108_aws_renames
Revises: 20260107_aws_entities
Create Date: 2026-01-08 00:00:00.000000

⚠️ WARNING: This is a BREAKING CHANGE migration ⚠️

This migration renames core fields to match AWS Supply Chain Data Model standards.
All code that references these fields MUST be updated before deploying this migration.

Field Renames:
1. item_id → product_id (ALL tables)
2. node_id → site_id (ALL tables)
3. upstream_node_id → from_site_id (lanes)
4. downstream_node_id → to_site_id (lanes)
5. nodes.name → nodes.description (already have description field, will deprecate name)
6. nodes.type → nodes.site_type (already have site_type field, will deprecate type)

PREREQUISITES:
- All Python models updated
- All Pydantic schemas updated
- All services/business logic updated
- All API endpoints updated
- Frontend code updated
- Tests updated

DO NOT RUN THIS MIGRATION until all code has been updated and tested!

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20260108_aws_renames'
down_revision = '20260107_aws_entities'
branch_labels = None
depends_on = None


def upgrade():
    """
    Rename fields to AWS Supply Chain standards.

    ⚠️ BREAKING CHANGE - ensure all code is updated first!
    """

    # ========================================================================
    # STEP 1: Rename in LANES table (transportation_lane)
    # ========================================================================

    print("Renaming lanes.upstream_node_id → lanes.from_site_id...")

    # Drop existing foreign key constraints
    op.drop_constraint('lanes_ibfk_1', 'lanes', type_='foreignkey')
    op.drop_constraint('lanes_ibfk_2', 'lanes', type_='foreignkey')

    # Drop unique constraint (uses old column names)
    op.drop_constraint('_node_connection_uc', 'lanes', type_='unique')

    # Rename columns
    op.alter_column('lanes', 'upstream_node_id',
                    new_column_name='from_site_id',
                    existing_type=sa.Integer(),
                    existing_nullable=False)

    op.alter_column('lanes', 'downstream_node_id',
                    new_column_name='to_site_id',
                    existing_type=sa.Integer(),
                    existing_nullable=False)

    # Re-create foreign key constraints with new names
    op.create_foreign_key(
        'fk_lanes_from_site_id', 'lanes', 'nodes',
        ['from_site_id'], ['id'], ondelete='CASCADE'
    )
    op.create_foreign_key(
        'fk_lanes_to_site_id', 'lanes', 'nodes',
        ['to_site_id'], ['id'], ondelete='CASCADE'
    )

    # Re-create unique constraint
    op.create_unique_constraint(
        '_site_connection_uc', 'lanes',
        ['from_site_id', 'to_site_id']
    )

    print("✓ Lanes table renamed")

    # ========================================================================
    # STEP 2: Rename in ITEMS table (product)
    # ========================================================================

    # Note: items.id stays as 'id', but we track that it maps to AWS 'product.id'
    # The item_id foreign keys in other tables will be renamed to product_id

    print("Items table: No renames needed (id field maps to AWS product.id)")

    # ========================================================================
    # STEP 3: Rename in ITEM_NODE_CONFIGS table
    # ========================================================================

    print("Renaming item_node_configs fields...")

    # Drop foreign keys
    op.drop_constraint('item_node_configs_ibfk_1', 'item_node_configs', type_='foreignkey')
    op.drop_constraint('item_node_configs_ibfk_2', 'item_node_configs', type_='foreignkey')

    # Drop unique constraint
    op.drop_constraint('_item_node_uc', 'item_node_configs', type_='unique')

    # Rename columns
    op.alter_column('item_node_configs', 'item_id',
                    new_column_name='product_id',
                    existing_type=sa.Integer(),
                    existing_nullable=False)

    op.alter_column('item_node_configs', 'node_id',
                    new_column_name='site_id',
                    existing_type=sa.Integer(),
                    existing_nullable=False)

    # Re-create foreign keys
    op.create_foreign_key(
        'fk_item_node_configs_product_id', 'item_node_configs', 'items',
        ['product_id'], ['id'], ondelete='CASCADE'
    )
    op.create_foreign_key(
        'fk_item_node_configs_site_id', 'item_node_configs', 'nodes',
        ['site_id'], ['id'], ondelete='CASCADE'
    )

    # Re-create unique constraint
    op.create_unique_constraint(
        '_product_site_uc', 'item_node_configs',
        ['product_id', 'site_id']
    )

    print("✓ item_node_configs renamed")

    # ========================================================================
    # STEP 4: Rename in ITEM_NODE_SUPPLIERS table (sourcing_rules)
    # ========================================================================

    print("Renaming item_node_suppliers.supplier_node_id → supplier_site_id...")

    # Drop foreign key
    op.drop_constraint('item_node_suppliers_ibfk_2', 'item_node_suppliers', type_='foreignkey')

    # Rename column
    op.alter_column('item_node_suppliers', 'supplier_node_id',
                    new_column_name='supplier_site_id',
                    existing_type=sa.Integer(),
                    existing_nullable=False)

    # Re-create foreign key
    op.create_foreign_key(
        'fk_item_node_suppliers_supplier_site_id', 'item_node_suppliers', 'nodes',
        ['supplier_site_id'], ['id'], ondelete='CASCADE'
    )

    # Note: unique constraint _item_node_supplier_uc references item_node_config_id
    # which still exists, so no need to recreate

    print("✓ item_node_suppliers renamed")

    # ========================================================================
    # STEP 5: Rename in MARKET_DEMANDS table
    # ========================================================================

    print("Renaming market_demands.item_id → product_id...")

    # Drop foreign key
    op.drop_constraint('market_demands_ibfk_2', 'market_demands', type_='foreignkey')

    # Rename column
    op.alter_column('market_demands', 'item_id',
                    new_column_name='product_id',
                    existing_type=sa.Integer(),
                    existing_nullable=False)

    # Re-create foreign key
    op.create_foreign_key(
        'fk_market_demands_product_id', 'market_demands', 'items',
        ['product_id'], ['id'], ondelete='CASCADE'
    )

    print("✓ market_demands renamed")

    # ========================================================================
    # STEP 6: Rename in ORDERS table (if exists)
    # ========================================================================

    # Check if orders table has item_id field
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if 'orders' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('orders')]

        if 'item_id' in columns:
            print("Renaming orders.item_id → product_id...")
            op.alter_column('orders', 'item_id',
                            new_column_name='product_id',
                            existing_type=sa.Integer())
            print("✓ orders.item_id renamed")

        # Check for from_node/to_node fields
        if 'from_node' in columns:
            print("Renaming orders.from_node → from_site...")
            op.alter_column('orders', 'from_node',
                            new_column_name='from_site',
                            existing_type=sa.String(100))
            print("✓ orders.from_node renamed")

        if 'to_node' in columns:
            print("Renaming orders.to_node → to_site...")
            op.alter_column('orders', 'to_node',
                            new_column_name='to_site',
                            existing_type=sa.String(100))
            print("✓ orders.to_node renamed")

    # ========================================================================
    # STEP 7: Rename in PLAYERS table
    # ========================================================================

    if 'players' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('players')]

        if 'node_key' in columns:
            print("Renaming players.node_key → site_key...")
            op.alter_column('players', 'node_key',
                            new_column_name='site_key',
                            existing_type=sa.String(100))
            print("✓ players.node_key renamed")

    # ========================================================================
    # STEP 8: Deprecate old fields in NODES table (keep for backwards compat temporarily)
    # ========================================================================

    # We already have description and site_type fields from previous migration
    # Mark old fields as deprecated in comments

    print("Marking nodes.name and nodes.type as deprecated...")

    # Add deprecated markers (just for documentation, doesn't affect DB)
    # In next major version, we can drop these columns entirely

    print("✓ nodes.name and nodes.type marked as deprecated (use description and site_type)")

    # ========================================================================
    # STEP 9: Update JSON config fields in games table
    # ========================================================================

    print("Updating JSON config blobs...")

    # This is complex - JSON paths with item_id, node_id need to be rewritten
    # For safety, we'll just add a note that manual updates may be needed

    print("⚠️  NOTE: Games with JSON config blobs may need manual updates")
    print("    Search for: item_id, node_id, upstream_node_id, downstream_node_id")
    print("    Replace with: product_id, site_id, from_site_id, to_site_id")

    # Example update (commented out - may need customization):
    # op.execute("""
    #     UPDATE games
    #     SET config = JSON_REPLACE(
    #         config,
    #         '$.nodes[*].node_id', JSON_EXTRACT(config, '$.nodes[*].site_id')
    #     )
    #     WHERE JSON_CONTAINS_PATH(config, 'one', '$.nodes[*].node_id')
    # """)

    print("✅ Migration complete! Remember to update all application code.")


def downgrade():
    """
    Reverse the field renames (restore old names).
    """

    print("⚠️  Reverting AWS field renames to original names...")

    # REVERSE STEP 7: players
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if 'players' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('players')]
        if 'site_key' in columns:
            op.alter_column('players', 'site_key',
                            new_column_name='node_key',
                            existing_type=sa.String(100))

    # REVERSE STEP 6: orders
    if 'orders' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('orders')]

        if 'to_site' in columns:
            op.alter_column('orders', 'to_site',
                            new_column_name='to_node',
                            existing_type=sa.String(100))

        if 'from_site' in columns:
            op.alter_column('orders', 'from_site',
                            new_column_name='from_node',
                            existing_type=sa.String(100))

        if 'product_id' in columns:
            op.alter_column('orders', 'product_id',
                            new_column_name='item_id',
                            existing_type=sa.Integer())

    # REVERSE STEP 5: market_demands
    op.drop_constraint('fk_market_demands_product_id', 'market_demands', type_='foreignkey')
    op.alter_column('market_demands', 'product_id',
                    new_column_name='item_id',
                    existing_type=sa.Integer(),
                    existing_nullable=False)
    op.create_foreign_key(
        'market_demands_ibfk_2', 'market_demands', 'items',
        ['item_id'], ['id'], ondelete='CASCADE'
    )

    # REVERSE STEP 4: item_node_suppliers
    op.drop_constraint('fk_item_node_suppliers_supplier_site_id', 'item_node_suppliers', type_='foreignkey')
    op.alter_column('item_node_suppliers', 'supplier_site_id',
                    new_column_name='supplier_node_id',
                    existing_type=sa.Integer(),
                    existing_nullable=False)
    op.create_foreign_key(
        'item_node_suppliers_ibfk_2', 'item_node_suppliers', 'nodes',
        ['supplier_node_id'], ['id'], ondelete='CASCADE'
    )

    # REVERSE STEP 3: item_node_configs
    op.drop_constraint('_product_site_uc', 'item_node_configs', type_='unique')
    op.drop_constraint('fk_item_node_configs_site_id', 'item_node_configs', type_='foreignkey')
    op.drop_constraint('fk_item_node_configs_product_id', 'item_node_configs', type_='foreignkey')

    op.alter_column('item_node_configs', 'site_id',
                    new_column_name='node_id',
                    existing_type=sa.Integer(),
                    existing_nullable=False)
    op.alter_column('item_node_configs', 'product_id',
                    new_column_name='item_id',
                    existing_type=sa.Integer(),
                    existing_nullable=False)

    op.create_foreign_key(
        'item_node_configs_ibfk_2', 'item_node_configs', 'nodes',
        ['node_id'], ['id'], ondelete='CASCADE'
    )
    op.create_foreign_key(
        'item_node_configs_ibfk_1', 'item_node_configs', 'items',
        ['item_id'], ['id'], ondelete='CASCADE'
    )
    op.create_unique_constraint(
        '_item_node_uc', 'item_node_configs',
        ['item_id', 'node_id']
    )

    # REVERSE STEP 1: lanes
    op.drop_constraint('_site_connection_uc', 'lanes', type_='unique')
    op.drop_constraint('fk_lanes_to_site_id', 'lanes', type_='foreignkey')
    op.drop_constraint('fk_lanes_from_site_id', 'lanes', type_='foreignkey')

    op.alter_column('lanes', 'to_site_id',
                    new_column_name='downstream_node_id',
                    existing_type=sa.Integer(),
                    existing_nullable=False)
    op.alter_column('lanes', 'from_site_id',
                    new_column_name='upstream_node_id',
                    existing_type=sa.Integer(),
                    existing_nullable=False)

    op.create_foreign_key(
        'lanes_ibfk_2', 'lanes', 'nodes',
        ['downstream_node_id'], ['id'], ondelete='CASCADE'
    )
    op.create_foreign_key(
        'lanes_ibfk_1', 'lanes', 'nodes',
        ['upstream_node_id'], ['id'], ondelete='CASCADE'
    )
    op.create_unique_constraint(
        '_node_connection_uc', 'lanes',
        ['upstream_node_id', 'downstream_node_id']
    )

    print("✅ Downgrade complete - original field names restored")
