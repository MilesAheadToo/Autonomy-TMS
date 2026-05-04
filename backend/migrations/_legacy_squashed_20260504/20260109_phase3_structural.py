"""Phase 3: AWS Supply Chain structural refactoring (NON-BREAKING)

Revision ID: 20260109_phase3_structural
Revises: 20260108_aws_renames
Create Date: 2026-01-09 00:00:00.000000

Creates AWS-standard tables alongside existing tables:
- inv_level: Inventory snapshot data (split from item_node_configs)
- inv_policy: Inventory policy configuration (split from item_node_configs)
- sourcing_rules: Flattened sourcing (replaces item_node_suppliers junction)
- shipment: Persistent shipment tracking
- inbound_order: Order header
- inbound_order_line: Order line items

This is NON-BREAKING - old tables remain functional for backward compatibility.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = '20260109_phase3_structural'
down_revision = '20260108_aws_renames'
branch_labels = None
depends_on = None


def upgrade():
    """
    Create AWS-standard table structure (additive, non-breaking).
    """

    # ========================================================================
    # 1. INV_LEVEL: Inventory snapshot data
    # ========================================================================

    print("Creating inv_level table...")
    op.create_table(
        'inv_level',
        sa.Column('id', sa.Integer(), nullable=False, comment='Inventory level ID'),
        sa.Column('product_id', sa.Integer(), nullable=False, comment='FK to items (product)'),
        sa.Column('site_id', sa.Integer(), nullable=False, comment='FK to nodes (site)'),
        sa.Column('on_hand_qty', sa.Numeric(10, 2), server_default=sa.text("0"), comment='Current inventory on hand'),
        sa.Column('available_qty', sa.Numeric(10, 2), server_default=sa.text("0"), comment='Available for sale/use'),
        sa.Column('reserved_qty', sa.Numeric(10, 2), server_default=sa.text("0"), comment='Reserved for orders'),
        sa.Column('in_transit_qty', sa.Numeric(10, 2), server_default=sa.text("0"), comment='In transit to this site'),
        sa.Column('backorder_qty', sa.Numeric(10, 2), server_default=sa.text("0"), comment='Backorder quantity'),
        sa.Column('safety_stock_qty', sa.Numeric(10, 2), nullable=True, comment='Safety stock target'),
        sa.Column('reorder_point_qty', sa.Numeric(10, 2), nullable=True, comment='Reorder point'),
        sa.Column('snapshot_date', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP'), comment='Snapshot timestamp'),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True,
                  server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'],
                                name='fk_inv_level_product_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['site_id'], ['nodes.id'],
                                name='fk_inv_level_site_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('product_id', 'site_id', 'snapshot_date', name='uk_inv_level'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('ix_inv_level_product_id', 'inv_level', ['product_id'], unique=False)
    op.create_index('ix_inv_level_site_id', 'inv_level', ['site_id'], unique=False)
    op.create_index('ix_inv_level_snapshot_date', 'inv_level', ['snapshot_date'], unique=False)

    print("✓ inv_level table created")

    # ========================================================================
    # 2. INV_POLICY: Inventory policy configuration
    # ========================================================================

    print("Creating inv_policy table...")
    op.create_table(
        'inv_policy',
        sa.Column('id', sa.Integer(), nullable=False, comment='Inventory policy ID'),
        sa.Column('product_id', sa.Integer(), nullable=False, comment='FK to items (product)'),
        sa.Column('site_id', sa.Integer(), nullable=False, comment='FK to nodes (site)'),
        sa.Column('policy_type', sa.String(50), nullable=False, server_default='base_stock',
                  comment='Policy type: base_stock, min_max, periodic_review'),
        sa.Column('target_qty', sa.Numeric(10, 2), nullable=True, comment='Target inventory level'),
        sa.Column('min_qty', sa.Numeric(10, 2), nullable=True, comment='Minimum stock level'),
        sa.Column('max_qty', sa.Numeric(10, 2), nullable=True, comment='Maximum stock level'),
        sa.Column('reorder_point', sa.Numeric(10, 2), nullable=True, comment='Reorder point quantity'),
        sa.Column('order_qty', sa.Numeric(10, 2), nullable=True, comment='Order quantity'),
        sa.Column('review_period', sa.Integer(), nullable=True, comment='Review period (days)'),
        sa.Column('service_level', sa.Numeric(5, 2), nullable=True, comment='Target service level %'),
        sa.Column('holding_cost', sa.Numeric(10, 2), nullable=True, comment='Holding cost per unit per period'),
        sa.Column('backlog_cost', sa.Numeric(10, 2), nullable=True, comment='Backlog cost per unit per period'),
        sa.Column('selling_price', sa.Numeric(10, 2), nullable=True, comment='Selling price per unit'),
        sa.Column('eff_start_date', sa.DateTime(), nullable=False, server_default='1900-01-01 00:00:00',
                  comment='Effective start date'),
        sa.Column('eff_end_date', sa.DateTime(), nullable=False, server_default='9999-12-31 23:59:59',
                  comment='Effective end date'),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True,
                  server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'],
                                name='fk_inv_policy_product_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['site_id'], ['nodes.id'],
                                name='fk_inv_policy_site_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('product_id', 'site_id', 'eff_start_date', name='uk_inv_policy'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('ix_inv_policy_product_id', 'inv_policy', ['product_id'], unique=False)
    op.create_index('ix_inv_policy_site_id', 'inv_policy', ['site_id'], unique=False)

    print("✓ inv_policy table created")

    # Migrate data from item_node_configs to inv_policy
    print("Migrating data from item_node_configs to inv_policy...")
    op.execute(text("""
        INSERT INTO inv_policy
        (product_id, site_id, policy_type, target_qty, holding_cost, backlog_cost, selling_price)
        SELECT
            product_id,
            site_id,
            'base_stock' as policy_type,
            JSON_EXTRACT(inventory_target_range, '$.min') +
                (JSON_EXTRACT(inventory_target_range, '$.max') - JSON_EXTRACT(inventory_target_range, '$.min')) / 2 as target_qty,
            JSON_EXTRACT(holding_cost_range, '$.min') +
                (JSON_EXTRACT(holding_cost_range, '$.max') - JSON_EXTRACT(holding_cost_range, '$.min')) / 2 as holding_cost,
            JSON_EXTRACT(backlog_cost_range, '$.min') +
                (JSON_EXTRACT(backlog_cost_range, '$.max') - JSON_EXTRACT(backlog_cost_range, '$.min')) / 2 as backlog_cost,
            JSON_EXTRACT(selling_price_range, '$.min') +
                (JSON_EXTRACT(selling_price_range, '$.max') - JSON_EXTRACT(selling_price_range, '$.min')) / 2 as selling_price
        FROM item_node_configs
    """))
    print("✓ Data migrated to inv_policy")

    # ========================================================================
    # 3. SOURCING_RULES: Flattened sourcing rules
    # ========================================================================

    print("Creating sourcing_rules table...")
    op.create_table(
        'sourcing_rules',
        sa.Column('id', sa.Integer(), nullable=False, comment='Sourcing rule ID'),
        sa.Column('product_id', sa.Integer(), nullable=False, comment='FK to items (product)'),
        sa.Column('site_id', sa.Integer(), nullable=False, comment='FK to nodes (destination site)'),
        sa.Column('supplier_site_id', sa.Integer(), nullable=False, comment='FK to nodes (supplier site)'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default=sa.text("0"),
                  comment='Priority (0 = highest)'),
        sa.Column('sourcing_rule_type', sa.String(50), nullable=False, server_default='transfer',
                  comment='Rule type: transfer, purchase, make'),
        sa.Column('allocation_percent', sa.Numeric(5, 2), nullable=False, server_default='100.00',
                  comment='Allocation percentage'),
        sa.Column('min_qty', sa.Numeric(10, 2), nullable=True, comment='Minimum order quantity'),
        sa.Column('max_qty', sa.Numeric(10, 2), nullable=True, comment='Maximum order quantity'),
        sa.Column('qty_multiple', sa.Numeric(10, 2), nullable=True, comment='Order quantity multiple'),
        sa.Column('lead_time', sa.Integer(), nullable=True, comment='Lead time in periods'),
        sa.Column('unit_cost', sa.Numeric(10, 2), nullable=True, comment='Cost per unit'),
        sa.Column('eff_start_date', sa.DateTime(), nullable=False, server_default='1900-01-01 00:00:00',
                  comment='Effective start date'),
        sa.Column('eff_end_date', sa.DateTime(), nullable=False, server_default='9999-12-31 23:59:59',
                  comment='Effective end date'),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True,
                  server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'],
                                name='fk_sourcing_rules_product_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['site_id'], ['nodes.id'],
                                name='fk_sourcing_rules_site_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['supplier_site_id'], ['nodes.id'],
                                name='fk_sourcing_rules_supplier_site_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('product_id', 'site_id', 'supplier_site_id', 'eff_start_date',
                           name='uk_sourcing_rules'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('ix_sourcing_rules_product_id', 'sourcing_rules', ['product_id'], unique=False)
    op.create_index('ix_sourcing_rules_site_id', 'sourcing_rules', ['site_id'], unique=False)
    op.create_index('ix_sourcing_rules_supplier_site_id', 'sourcing_rules', ['supplier_site_id'], unique=False)

    print("✓ sourcing_rules table created")

    # Migrate data from item_node_suppliers to sourcing_rules
    print("Migrating data from item_node_suppliers to sourcing_rules...")
    op.execute(text("""
        INSERT INTO sourcing_rules
        (product_id, site_id, supplier_site_id, priority, sourcing_rule_type)
        SELECT
            inc.product_id,
            inc.site_id,
            ins.supplier_site_id,
            ins.priority,
            'transfer' as sourcing_rule_type
        FROM item_node_suppliers ins
        JOIN item_node_configs inc ON ins.item_node_config_id = inc.id
    """))
    print("✓ Data migrated to sourcing_rules")

    # ========================================================================
    # 4. SHIPMENT: Persistent shipment tracking
    # ========================================================================

    print("Creating shipment table...")
    op.create_table(
        'shipment',
        sa.Column('id', sa.Integer(), nullable=False, comment='Shipment ID'),
        sa.Column('shipment_number', sa.String(100), nullable=True, comment='User-friendly identifier'),
        sa.Column('product_id', sa.Integer(), nullable=False, comment='FK to items (product)'),
        sa.Column('from_site_id', sa.Integer(), nullable=False, comment='FK to nodes (origin site)'),
        sa.Column('to_site_id', sa.Integer(), nullable=False, comment='FK to nodes (destination site)'),
        sa.Column('lane_id', sa.Integer(), nullable=True, comment='FK to lanes'),
        sa.Column('quantity', sa.Numeric(10, 2), nullable=False, comment='Planned quantity'),
        sa.Column('shipped_qty', sa.Numeric(10, 2), nullable=True, comment='Actual shipped quantity'),
        sa.Column('received_qty', sa.Numeric(10, 2), nullable=True, comment='Actual received quantity'),
        sa.Column('shipment_status', sa.String(50), nullable=False, server_default='in_transit',
                  comment='Status: planned, in_transit, delivered, cancelled'),
        sa.Column('carrier_tpartner_id', sa.Integer(), nullable=True, comment='FK to trading_partner (carrier)'),
        sa.Column('ship_date', sa.DateTime(), nullable=True, comment='Actual ship date'),
        sa.Column('scheduled_delivery_date', sa.DateTime(), nullable=True, comment='Expected delivery'),
        sa.Column('actual_delivery_date', sa.DateTime(), nullable=True, comment='Actual delivery'),
        sa.Column('transit_time', sa.Integer(), nullable=True, comment='Actual transit time'),
        sa.Column('game_id', sa.Integer(), nullable=True, comment='FK to games (for Beer Game)'),
        sa.Column('round_number', sa.Integer(), nullable=True, comment='Round when shipped'),
        sa.Column('arrival_round', sa.Integer(), nullable=True, comment='Round when arriving'),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True,
                  server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'],
                                name='fk_shipment_product_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['from_site_id'], ['nodes.id'],
                                name='fk_shipment_from_site_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['to_site_id'], ['nodes.id'],
                                name='fk_shipment_to_site_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['lane_id'], ['lanes.id'],
                                name='fk_shipment_lane_id', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['carrier_tpartner_id'], ['trading_partner.id'],
                                name='fk_shipment_carrier_tpartner_id', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'],
                                name='fk_shipment_game_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('ix_shipment_shipment_number', 'shipment', ['shipment_number'], unique=True)
    op.create_index('ix_shipment_product_id', 'shipment', ['product_id'], unique=False)
    op.create_index('ix_shipment_from_site_id', 'shipment', ['from_site_id'], unique=False)
    op.create_index('ix_shipment_to_site_id', 'shipment', ['to_site_id'], unique=False)
    op.create_index('ix_shipment_game_id', 'shipment', ['game_id'], unique=False)
    op.create_index('ix_shipment_status', 'shipment', ['shipment_status'], unique=False)

    print("✓ shipment table created")

    # ========================================================================
    # 5. INBOUND_ORDER: Order header
    # ========================================================================

    print("Creating inbound_order table...")
    op.create_table(
        'inbound_order',
        sa.Column('id', sa.Integer(), nullable=False, comment='Order ID'),
        sa.Column('order_number', sa.String(100), nullable=True, comment='User-friendly identifier'),
        sa.Column('from_site_id', sa.Integer(), nullable=False, comment='FK to nodes (supplier)'),
        sa.Column('to_site_id', sa.Integer(), nullable=False, comment='FK to nodes (customer)'),
        sa.Column('order_type', sa.String(50), nullable=False, server_default='transfer',
                  comment='Order type: transfer, purchase, replenishment'),
        sa.Column('order_status', sa.String(50), nullable=False, server_default='open',
                  comment='Status: open, confirmed, shipped, delivered, cancelled'),
        sa.Column('order_date', sa.DateTime(), nullable=False, comment='Order date'),
        sa.Column('requested_delivery_date', sa.DateTime(), nullable=True, comment='Requested delivery'),
        sa.Column('promised_delivery_date', sa.DateTime(), nullable=True, comment='Promised delivery'),
        sa.Column('actual_delivery_date', sa.DateTime(), nullable=True, comment='Actual delivery'),
        sa.Column('total_qty', sa.Numeric(10, 2), nullable=True, comment='Total quantity'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default=sa.text("0"), comment='Order priority'),
        sa.Column('game_id', sa.Integer(), nullable=True, comment='FK to games (for Beer Game)'),
        sa.Column('round_number', sa.Integer(), nullable=True, comment='Round when ordered'),
        sa.Column('due_round', sa.Integer(), nullable=True, comment='Round when needed'),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True,
                  server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['from_site_id'], ['nodes.id'],
                                name='fk_inbound_order_from_site_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['to_site_id'], ['nodes.id'],
                                name='fk_inbound_order_to_site_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'],
                                name='fk_inbound_order_game_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('ix_inbound_order_order_number', 'inbound_order', ['order_number'], unique=True)
    op.create_index('ix_inbound_order_from_site_id', 'inbound_order', ['from_site_id'], unique=False)
    op.create_index('ix_inbound_order_to_site_id', 'inbound_order', ['to_site_id'], unique=False)
    op.create_index('ix_inbound_order_game_id', 'inbound_order', ['game_id'], unique=False)
    op.create_index('ix_inbound_order_status', 'inbound_order', ['order_status'], unique=False)

    print("✓ inbound_order table created")

    # ========================================================================
    # 6. INBOUND_ORDER_LINE: Order line items
    # ========================================================================

    print("Creating inbound_order_line table...")
    op.create_table(
        'inbound_order_line',
        sa.Column('id', sa.Integer(), nullable=False, comment='Order line ID'),
        sa.Column('order_id', sa.Integer(), nullable=False, comment='FK to inbound_order'),
        sa.Column('line_number', sa.Integer(), nullable=False, comment='Line sequence'),
        sa.Column('product_id', sa.Integer(), nullable=False, comment='FK to items (product)'),
        sa.Column('quantity', sa.Numeric(10, 2), nullable=False, comment='Ordered quantity'),
        sa.Column('shipped_qty', sa.Numeric(10, 2), server_default=sa.text("0"), comment='Shipped quantity'),
        sa.Column('received_qty', sa.Numeric(10, 2), server_default=sa.text("0"), comment='Received quantity'),
        sa.Column('unit_price', sa.Numeric(10, 2), nullable=True, comment='Unit price'),
        sa.Column('line_status', sa.String(50), nullable=False, server_default='open',
                  comment='Status: open, partial, fulfilled, cancelled'),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True,
                  server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['order_id'], ['inbound_order.id'],
                                name='fk_inbound_order_line_order_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'],
                                name='fk_inbound_order_line_product_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_id', 'line_number', name='uk_inbound_order_line'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('ix_inbound_order_line_order_id', 'inbound_order_line', ['order_id'], unique=False)
    op.create_index('ix_inbound_order_line_product_id', 'inbound_order_line', ['product_id'], unique=False)

    print("✓ inbound_order_line table created")

    print("\n✅ Phase 3 structural refactoring complete!")
    print("   • inv_level table created (inventory snapshots)")
    print("   • inv_policy table created and populated (inventory policies)")
    print("   • sourcing_rules table created and populated (sourcing rules)")
    print("   • shipment table created (shipment tracking)")
    print("   • inbound_order table created (order headers)")
    print("   • inbound_order_line table created (order lines)")
    print("\n   Old tables (item_node_configs, item_node_suppliers) remain for backward compatibility.")


def downgrade():
    """
    Remove Phase 3 tables (restore to Phase 2 state).
    """

    print("⚠️  Removing Phase 3 tables...")

    # Drop tables in reverse order (respect foreign keys)
    op.drop_index('ix_inbound_order_line_product_id', table_name='inbound_order_line')
    op.drop_index('ix_inbound_order_line_order_id', table_name='inbound_order_line')
    op.drop_table('inbound_order_line')

    op.drop_index('ix_inbound_order_status', table_name='inbound_order')
    op.drop_index('ix_inbound_order_game_id', table_name='inbound_order')
    op.drop_index('ix_inbound_order_to_site_id', table_name='inbound_order')
    op.drop_index('ix_inbound_order_from_site_id', table_name='inbound_order')
    op.drop_index('ix_inbound_order_order_number', table_name='inbound_order')
    op.drop_table('inbound_order')

    op.drop_index('ix_shipment_status', table_name='shipment')
    op.drop_index('ix_shipment_game_id', table_name='shipment')
    op.drop_index('ix_shipment_to_site_id', table_name='shipment')
    op.drop_index('ix_shipment_from_site_id', table_name='shipment')
    op.drop_index('ix_shipment_product_id', table_name='shipment')
    op.drop_index('ix_shipment_shipment_number', table_name='shipment')
    op.drop_table('shipment')

    op.drop_index('ix_sourcing_rules_supplier_site_id', table_name='sourcing_rules')
    op.drop_index('ix_sourcing_rules_site_id', table_name='sourcing_rules')
    op.drop_index('ix_sourcing_rules_product_id', table_name='sourcing_rules')
    op.drop_table('sourcing_rules')

    op.drop_index('ix_inv_policy_site_id', table_name='inv_policy')
    op.drop_index('ix_inv_policy_product_id', table_name='inv_policy')
    op.drop_table('inv_policy')

    op.drop_index('ix_inv_level_snapshot_date', table_name='inv_level')
    op.drop_index('ix_inv_level_site_id', table_name='inv_level')
    op.drop_index('ix_inv_level_product_id', table_name='inv_level')
    op.drop_table('inv_level')

    print("✅ Phase 3 downgrade complete - old tables restored")
