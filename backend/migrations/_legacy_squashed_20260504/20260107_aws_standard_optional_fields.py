"""Add AWS Supply Chain standard optional fields (non-breaking)

Revision ID: 20260107_aws_optional
Revises: 20260107_item_node_supplier
Create Date: 2026-01-07 15:00:00.000000

This migration adds optional AWS Supply Chain Data Model standard fields
without breaking existing functionality. All additions have sensible defaults.

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20260107_aws_optional'
down_revision = '20260107_item_node_supplier'
branch_labels = None
depends_on = None


def upgrade():
    """
    Add AWS-standard optional fields to existing tables.

    This is a NON-BREAKING change - all fields are nullable or have defaults.
    Existing code will continue to work without modification.
    """

    # ========================================================================
    # 1. NODES table - Add AWS 'site' standard fields
    # ========================================================================

    # Geographic and lifecycle fields
    op.add_column('nodes', sa.Column('geo_id', sa.Integer(), nullable=True,
                                     comment='Geographic location reference (FK to geography table when created)'))
    op.add_column('nodes', sa.Column('latitude', sa.Numeric(precision=10, scale=8), nullable=True,
                                     comment='Geographic latitude coordinate'))
    op.add_column('nodes', sa.Column('longitude', sa.Numeric(precision=11, scale=8), nullable=True,
                                     comment='Geographic longitude coordinate'))
    op.add_column('nodes', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text("TRUE"),
                                     comment='Active status - use False to exclude from planning'))
    op.add_column('nodes', sa.Column('open_date', sa.Date(), nullable=True,
                                     comment='Site opening date'))
    op.add_column('nodes', sa.Column('end_date', sa.Date(), nullable=True,
                                     comment='Site closure date'))

    # AWS standard naming (will be primary in future, keep old names for now)
    op.add_column('nodes', sa.Column('site_type', sa.String(100), nullable=True,
                                     comment='AWS standard: same as type field'))
    op.add_column('nodes', sa.Column('description', sa.String(200), nullable=True,
                                     comment='AWS standard: same as name field'))

    # Populate new fields from existing data
    op.execute("""
        UPDATE nodes
        SET site_type = type,
            description = name
        WHERE site_type IS NULL OR description IS NULL
    """)

    # ========================================================================
    # 2. ITEMS table - Add AWS 'product' standard fields
    # ========================================================================

    op.add_column('items', sa.Column('product_group_id', sa.Integer(), nullable=True,
                                     comment='Product category reference (FK to product_hierarchy when created)'))
    op.add_column('items', sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text("FALSE"),
                                     comment='Soft delete flag - False=active, True=exclude from planning'))
    op.add_column('items', sa.Column('product_type', sa.String(50), nullable=True,
                                     comment='Product classification'))
    op.add_column('items', sa.Column('parent_product_id', sa.Integer(), nullable=True,
                                     comment='Parent product for hierarchy (FK to items.id)'))
    op.add_column('items', sa.Column('base_uom', sa.String(20), nullable=True,
                                     comment='Base unit of measure'))
    op.add_column('items', sa.Column('unit_cost', sa.Numeric(precision=10, scale=2), nullable=True,
                                     comment='Standard unit cost'))
    op.add_column('items', sa.Column('unit_price', sa.Numeric(precision=10, scale=2), nullable=True,
                                     comment='Standard unit selling price'))

    # Populate unit_cost from existing unit_cost_range (use midpoint)
    op.execute("""
        UPDATE items
        SET unit_cost = (
            JSON_EXTRACT(unit_cost_range, '$.min') +
            JSON_EXTRACT(unit_cost_range, '$.max')
        ) / 2
        WHERE unit_cost IS NULL AND unit_cost_range IS NOT NULL
    """)

    # ========================================================================
    # 3. LANES table - Add AWS 'transportation_lane' standard fields
    # ========================================================================

    # Geographic references
    op.add_column('lanes', sa.Column('from_geo_id', sa.Integer(), nullable=True,
                                     comment='Origin geography (FK to geography when created)'))
    op.add_column('lanes', sa.Column('to_geo_id', sa.Integer(), nullable=True,
                                     comment='Destination geography (FK to geography when created)'))

    # Logistics fields
    op.add_column('lanes', sa.Column('carrier_tpartner_id', sa.Integer(), nullable=True,
                                     comment='Carrier/logistics provider (FK to trading_partner when created)'))
    op.add_column('lanes', sa.Column('service_type', sa.String(50), nullable=True,
                                     comment='Service classification'))
    op.add_column('lanes', sa.Column('trans_mode', sa.String(50), nullable=True,
                                     comment='Transportation mode: truck, air, rail, ocean, etc.'))

    # Distance and emissions
    op.add_column('lanes', sa.Column('distance', sa.Numeric(precision=10, scale=2), nullable=True,
                                     comment='Distance between sites'))
    op.add_column('lanes', sa.Column('distance_uom', sa.String(20), nullable=True,
                                     comment='Distance unit of measure (km, miles, etc.)'))
    op.add_column('lanes', sa.Column('emissions_per_unit', sa.Numeric(precision=10, scale=4), nullable=True,
                                     comment='Carbon emissions per unit shipped'))
    op.add_column('lanes', sa.Column('emissions_per_weight', sa.Numeric(precision=10, scale=4), nullable=True,
                                     comment='Carbon emissions per weight unit'))

    # Cost and effective dates
    op.add_column('lanes', sa.Column('cost_per_unit', sa.Numeric(precision=10, scale=2), nullable=True,
                                     comment='Cost per unit shipped'))
    op.add_column('lanes', sa.Column('cost_currency', sa.String(3), nullable=True,
                                     comment='Currency code (USD, EUR, etc.)'))
    op.add_column('lanes', sa.Column('eff_start_date', sa.DateTime(), nullable=True,
                                     comment='Lane effective start date'))
    op.add_column('lanes', sa.Column('eff_end_date', sa.DateTime(), nullable=True,
                                     comment='Lane effective end date'))

    # Extract transit_time and time_uom from supply_lead_time JSON
    op.add_column('lanes', sa.Column('transit_time', sa.Integer(), nullable=True,
                                     comment='Transit time value (extracted from supply_lead_time)'))
    op.add_column('lanes', sa.Column('time_uom', sa.String(20), nullable=True,
                                     comment='Time unit of measure (Day, Week, Month)'))

    # Populate from existing supply_lead_time JSON
    op.execute("""
        UPDATE lanes
        SET transit_time = JSON_EXTRACT(supply_lead_time, '$.value'),
            time_uom = UPPER(JSON_EXTRACT(supply_lead_time, '$.type'))
        WHERE supply_lead_time IS NOT NULL
    """)

    # Set default time_uom if not set
    op.execute("""
        UPDATE lanes
        SET time_uom = 'DAY'
        WHERE time_uom IS NULL AND transit_time IS NOT NULL
    """)

    # ========================================================================
    # 4. ITEM_NODE_SUPPLIERS table - Add AWS 'sourcing_rules' fields
    # ========================================================================

    op.add_column('item_node_suppliers', sa.Column('sourcing_rule_type',
                                                    sa.String(20), nullable=True,
                                                    comment='Rule type: transfer, buy, manufacture'))
    op.add_column('item_node_suppliers', sa.Column('min_qty', sa.Integer(), nullable=True,
                                                    comment='Minimum order quantity'))
    op.add_column('item_node_suppliers', sa.Column('max_qty', sa.Integer(), nullable=True,
                                                    comment='Maximum order quantity'))
    op.add_column('item_node_suppliers', sa.Column('qty_multiple', sa.Integer(), nullable=True,
                                                    comment='Order quantity must be multiple of this'))
    op.add_column('item_node_suppliers', sa.Column('eff_start_date', sa.DateTime(), nullable=True,
                                                    comment='Sourcing rule effective start'))
    op.add_column('item_node_suppliers', sa.Column('eff_end_date', sa.DateTime(), nullable=True,
                                                    comment='Sourcing rule effective end'))

    # Default to 'transfer' type for existing records (internal transfers)
    op.execute("""
        UPDATE item_node_suppliers
        SET sourcing_rule_type = 'transfer'
        WHERE sourcing_rule_type IS NULL
    """)

    # ========================================================================
    # 5. Create indices for new foreign key fields (for future use)
    # ========================================================================

    op.create_index('ix_nodes_geo_id', 'nodes', ['geo_id'], unique=False)
    op.create_index('ix_items_product_group_id', 'items', ['product_group_id'], unique=False)
    op.create_index('ix_items_parent_product_id', 'items', ['parent_product_id'], unique=False)
    op.create_index('ix_lanes_from_geo_id', 'lanes', ['from_geo_id'], unique=False)
    op.create_index('ix_lanes_to_geo_id', 'lanes', ['to_geo_id'], unique=False)
    op.create_index('ix_lanes_carrier_tpartner_id', 'lanes', ['carrier_tpartner_id'], unique=False)

    # Add compound index for active nodes
    op.create_index('ix_nodes_is_active', 'nodes', ['is_active'], unique=False)

    # Add compound index for non-deleted products
    op.create_index('ix_items_is_deleted', 'items', ['is_deleted'], unique=False)


def downgrade():
    """
    Remove AWS-standard optional fields.
    """

    # Drop indices
    op.drop_index('ix_items_is_deleted', table_name='items')
    op.drop_index('ix_nodes_is_active', table_name='nodes')
    op.drop_index('ix_lanes_carrier_tpartner_id', table_name='lanes')
    op.drop_index('ix_lanes_to_geo_id', table_name='lanes')
    op.drop_index('ix_lanes_from_geo_id', table_name='lanes')
    op.drop_index('ix_items_parent_product_id', table_name='items')
    op.drop_index('ix_items_product_group_id', table_name='items')
    op.drop_index('ix_nodes_geo_id', table_name='nodes')

    # Remove columns from item_node_suppliers
    op.drop_column('item_node_suppliers', 'eff_end_date')
    op.drop_column('item_node_suppliers', 'eff_start_date')
    op.drop_column('item_node_suppliers', 'qty_multiple')
    op.drop_column('item_node_suppliers', 'max_qty')
    op.drop_column('item_node_suppliers', 'min_qty')
    op.drop_column('item_node_suppliers', 'sourcing_rule_type')

    # Remove columns from lanes
    op.drop_column('lanes', 'time_uom')
    op.drop_column('lanes', 'transit_time')
    op.drop_column('lanes', 'eff_end_date')
    op.drop_column('lanes', 'eff_start_date')
    op.drop_column('lanes', 'cost_currency')
    op.drop_column('lanes', 'cost_per_unit')
    op.drop_column('lanes', 'emissions_per_weight')
    op.drop_column('lanes', 'emissions_per_unit')
    op.drop_column('lanes', 'distance_uom')
    op.drop_column('lanes', 'distance')
    op.drop_column('lanes', 'trans_mode')
    op.drop_column('lanes', 'service_type')
    op.drop_column('lanes', 'carrier_tpartner_id')
    op.drop_column('lanes', 'to_geo_id')
    op.drop_column('lanes', 'from_geo_id')

    # Remove columns from items
    op.drop_column('items', 'unit_price')
    op.drop_column('items', 'unit_cost')
    op.drop_column('items', 'base_uom')
    op.drop_column('items', 'parent_product_id')
    op.drop_column('items', 'product_type')
    op.drop_column('items', 'is_deleted')
    op.drop_column('items', 'product_group_id')

    # Remove columns from nodes
    op.drop_column('nodes', 'description')
    op.drop_column('nodes', 'site_type')
    op.drop_column('nodes', 'end_date')
    op.drop_column('nodes', 'open_date')
    op.drop_column('nodes', 'is_active')
    op.drop_column('nodes', 'longitude')
    op.drop_column('nodes', 'latitude')
    op.drop_column('nodes', 'geo_id')
