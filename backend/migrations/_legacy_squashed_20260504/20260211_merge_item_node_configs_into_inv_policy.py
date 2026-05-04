"""Merge item_node_configs into inv_policy

The item_node_configs table is a legacy Beer Game table that duplicates
inv_policy's product-site configuration. This migration:
1. Adds initial_inventory_range column to inv_policy
2. Migrates all item_node_configs rows into inv_policy
3. Drops the item_node_configs table

Revision ID: 20260211_merge_inc
Revises: 20260208_decision_tracking
Create Date: 2026-02-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


revision = '20260211_merge_inc'
down_revision = '20260208_decision_tracking'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add initial_inventory_range to inv_policy (the only column inv_policy lacks)
    op.add_column('inv_policy', sa.Column('initial_inventory_range', JSON, nullable=True))

    # 2. Migrate data from item_node_configs into inv_policy
    #    Only insert rows that don't already exist (by product_id + site_id)
    conn = op.get_bind()

    # Check if item_node_configs table exists and has data
    result = conn.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_name = 'item_node_configs'"
    ))
    if result.scalar() == 0:
        print("item_node_configs table does not exist, skipping migration")
        return

    count_before = conn.execute(sa.text("SELECT COUNT(*) FROM item_node_configs")).scalar()
    print(f"Found {count_before} rows in item_node_configs to migrate")

    if count_before > 0:
        # Get config_id for each site (item_node_configs doesn't store config_id)
        conn.execute(sa.text("""
            INSERT INTO inv_policy (
                product_id, site_id, config_id,
                inventory_target_range, initial_inventory_range,
                holding_cost_range, backlog_cost_range, selling_price_range,
                ss_policy, is_active
            )
            SELECT
                inc.product_id,
                inc.site_id,
                s.config_id,
                inc.inventory_target_range,
                inc.initial_inventory_range,
                inc.holding_cost_range,
                inc.backlog_cost_range,
                inc.selling_price_range,
                'abs_level',
                'Y'
            FROM item_node_configs inc
            JOIN site s ON inc.site_id = s.id
            WHERE NOT EXISTS (
                SELECT 1 FROM inv_policy ip
                WHERE ip.product_id = inc.product_id
                AND ip.site_id = inc.site_id
                AND ip.config_id = s.config_id
            )
        """))

        count_after = conn.execute(sa.text(
            "SELECT COUNT(*) FROM inv_policy WHERE ss_policy = 'abs_level'"
        )).scalar()
        print(f"Migrated rows into inv_policy (abs_level policies: {count_after})")

    # 3. Drop dependent item_node_suppliers table (empty, replaced by sourcing_rules)
    result2 = conn.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_name = 'item_node_suppliers'"
    ))
    if result2.scalar() > 0:
        op.drop_table('item_node_suppliers')
        print("Dropped item_node_suppliers table")

    # 4. Drop the old table
    op.drop_table('item_node_configs')
    print("Dropped item_node_configs table")


def downgrade():
    # Recreate item_node_configs table
    op.create_table(
        'item_node_configs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('product_id', sa.String(100), sa.ForeignKey('product.id'), nullable=False),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('site.id'), nullable=False),
        sa.Column('inventory_target_range', JSON),
        sa.Column('initial_inventory_range', JSON),
        sa.Column('holding_cost_range', JSON),
        sa.Column('backlog_cost_range', JSON),
        sa.Column('selling_price_range', JSON),
        sa.UniqueConstraint('product_id', 'site_id', name='_product_site_uc'),
    )

    # Copy data back from inv_policy
    conn = op.get_bind()
    conn.execute(sa.text("""
        INSERT INTO item_node_configs (
            product_id, site_id,
            inventory_target_range, initial_inventory_range,
            holding_cost_range, backlog_cost_range, selling_price_range
        )
        SELECT
            product_id, site_id,
            inventory_target_range, initial_inventory_range,
            holding_cost_range, backlog_cost_range, selling_price_range
        FROM inv_policy
        WHERE inventory_target_range IS NOT NULL
        OR initial_inventory_range IS NOT NULL
        OR holding_cost_range IS NOT NULL
    """))

    # Remove the column from inv_policy
    op.drop_column('inv_policy', 'initial_inventory_range')
