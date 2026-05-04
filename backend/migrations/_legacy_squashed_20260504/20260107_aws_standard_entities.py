"""Add AWS Supply Chain standard entity tables

Revision ID: 20260107_aws_entities
Revises: 20260107_aws_optional
Create Date: 2026-01-07 15:30:00.000000

This migration creates new AWS Supply Chain Data Model standard entities:
- geography: Geographic hierarchy
- product_hierarchy: Product category hierarchy
- trading_partner: External suppliers, vendors, carriers

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20260107_aws_entities'
down_revision = '20260107_aws_optional'
branch_labels = None
depends_on = None


def upgrade():
    """
    Create AWS-standard entity tables.
    """

    # ========================================================================
    # 1. GEOGRAPHY table - Hierarchical location structure
    # ========================================================================

    op.create_table(
        'geography',
        sa.Column('id', sa.Integer(), nullable=False, comment='Geography identifier'),
        sa.Column('description', sa.String(255), nullable=False, comment='Location name'),
        sa.Column('parent_geo_id', sa.Integer(), nullable=True,
                  comment='Parent geography for hierarchy (e.g., USA-EAST → USA)'),
        sa.Column('geo_type', sa.String(50), nullable=True,
                  comment='Type: country, region, state, city, zip'),
        sa.Column('iso_code', sa.String(10), nullable=True,
                  comment='ISO country/region code'),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True,
                  server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['parent_geo_id'], ['geography.id'],
                                name='fk_geography_parent', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('ix_geography_id', 'geography', ['id'], unique=False)
    op.create_index('ix_geography_parent_geo_id', 'geography', ['parent_geo_id'], unique=False)
    op.create_index('ix_geography_geo_type', 'geography', ['geo_type'], unique=False)

    # ========================================================================
    # 2. PRODUCT_HIERARCHY table - Product category structure
    # ========================================================================

    op.create_table(
        'product_hierarchy',
        sa.Column('id', sa.Integer(), nullable=False, comment='Product group identifier'),
        sa.Column('description', sa.String(255), nullable=False,
                  comment='Category name (e.g., Dairy, Full Fat Milk, etc.)'),
        sa.Column('parent_product_group_id', sa.Integer(), nullable=True,
                  comment='Parent category for multi-level hierarchy'),
        sa.Column('level', sa.Integer(), nullable=True,
                  comment='Hierarchy level (0=root, 1=category, 2=subcategory, etc.)'),
        sa.Column('sort_order', sa.Integer(), nullable=True,
                  comment='Display order within same parent'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text("TRUE"),
                  comment='Active status'),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True,
                  server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['parent_product_group_id'], ['product_hierarchy.id'],
                                name='fk_product_hierarchy_parent', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('ix_product_hierarchy_id', 'product_hierarchy', ['id'], unique=False)
    op.create_index('ix_product_hierarchy_parent', 'product_hierarchy', ['parent_product_group_id'], unique=False)
    op.create_index('ix_product_hierarchy_level', 'product_hierarchy', ['level'], unique=False)

    # ========================================================================
    # 3. TRADING_PARTNER table - External suppliers, vendors, carriers
    # ========================================================================

    op.create_table(
        'trading_partner',
        sa.Column('id', sa.Integer(), nullable=False, comment='Trading partner identifier'),
        sa.Column('description', sa.String(255), nullable=True, comment='Partner name'),
        sa.Column('country', sa.String(100), nullable=True, comment='Operating country'),
        sa.Column('eff_start_date', sa.DateTime(), nullable=False,
                  server_default='1900-01-01 00:00:00',
                  comment='Effective start date'),
        sa.Column('eff_end_date', sa.DateTime(), nullable=False,
                  server_default='9999-12-31 23:59:59',
                  comment='Effective end date'),
        sa.Column('time_zone', sa.String(50), nullable=True, comment='Partner timezone'),
        sa.Column('is_active', sa.Boolean(), nullable=True, server_default=sa.text("TRUE"),
                  comment='Active status'),
        sa.Column('tpartner_type', sa.String(50), nullable=False,
                  server_default='SCN_RESERVED_NO_VALUE_PROVIDED',
                  comment='Partner type: supplier, vendor, carrier, 3pl, etc.'),
        sa.Column('geo_id', sa.Integer(), nullable=True,
                  comment='Geographic location (FK to geography)'),
        sa.Column('address_1', sa.String(255), nullable=True),
        sa.Column('address_2', sa.String(255), nullable=True),
        sa.Column('city', sa.String(100), nullable=True),
        sa.Column('state_prov', sa.String(100), nullable=True),
        sa.Column('postal_code', sa.String(20), nullable=True),
        sa.Column('phone_number', sa.String(50), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('website', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True,
                  server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['geo_id'], ['geography.id'],
                                name='fk_trading_partner_geo', ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    op.create_index('ix_trading_partner_id', 'trading_partner', ['id'], unique=False)
    op.create_index('ix_trading_partner_geo_id', 'trading_partner', ['geo_id'], unique=False)
    op.create_index('ix_trading_partner_type', 'trading_partner', ['tpartner_type'], unique=False)
    op.create_index('ix_trading_partner_is_active', 'trading_partner', ['is_active'], unique=False)

    # ========================================================================
    # 4. Add foreign key constraints to existing tables (now that tables exist)
    # ========================================================================

    # NOTE: These are commented out initially to avoid breaking existing data
    # Uncomment after data migration/population

    # op.create_foreign_key(
    #     'fk_nodes_geo_id', 'nodes', 'geography',
    #     ['geo_id'], ['id'], ondelete='SET NULL'
    # )

    # op.create_foreign_key(
    #     'fk_items_product_group_id', 'items', 'product_hierarchy',
    #     ['product_group_id'], ['id'], ondelete='SET NULL'
    # )

    # op.create_foreign_key(
    #     'fk_items_parent_product_id', 'items', 'items',
    #     ['parent_product_id'], ['id'], ondelete='SET NULL'
    # )

    # op.create_foreign_key(
    #     'fk_lanes_from_geo_id', 'lanes', 'geography',
    #     ['from_geo_id'], ['id'], ondelete='SET NULL'
    # )

    # op.create_foreign_key(
    #     'fk_lanes_to_geo_id', 'lanes', 'geography',
    #     ['to_geo_id'], ['id'], ondelete='SET NULL'
    # )

    # op.create_foreign_key(
    #     'fk_lanes_carrier_tpartner_id', 'lanes', 'trading_partner',
    #     ['carrier_tpartner_id'], ['id'], ondelete='SET NULL'
    # )

    # ========================================================================
    # 5. Insert default/example data
    # ========================================================================

    # Insert default geography (World → continents → countries)
    op.execute("""
        INSERT INTO geography (id, description, parent_geo_id, geo_type, iso_code)
        VALUES
            (1, 'World', NULL, 'world', NULL),
            (2, 'North America', 1, 'continent', 'NA'),
            (3, 'United States', 2, 'country', 'US'),
            (4, 'USA-EAST', 3, 'region', 'US-EAST'),
            (5, 'USA-WEST', 3, 'region', 'US-WEST'),
            (6, 'USA-CENTRAL', 3, 'region', 'US-CENTRAL')
    """)

    # Insert default product hierarchy (example categories)
    op.execute("""
        INSERT INTO product_hierarchy (id, description, parent_product_group_id, level, sort_order)
        VALUES
            (1, 'All Products', NULL, 0, 0),
            (2, 'Beverages', 1, 1, 1),
            (3, 'Food', 1, 1, 2),
            (4, 'Beer', 2, 2, 1),
            (5, 'Soft Drinks', 2, 2, 2),
            (6, 'Packaged Goods', 3, 2, 1)
    """)


def downgrade():
    """
    Remove AWS-standard entity tables.
    """

    # Drop foreign key constraints if they were added
    # (Uncomment if the constraints were created in upgrade())
    # op.drop_constraint('fk_lanes_carrier_tpartner_id', 'lanes', type_='foreignkey')
    # op.drop_constraint('fk_lanes_to_geo_id', 'lanes', type_='foreignkey')
    # op.drop_constraint('fk_lanes_from_geo_id', 'lanes', type_='foreignkey')
    # op.drop_constraint('fk_items_parent_product_id', 'items', type_='foreignkey')
    # op.drop_constraint('fk_items_product_group_id', 'items', type_='foreignkey')
    # op.drop_constraint('fk_nodes_geo_id', 'nodes', type_='foreignkey')

    # Drop tables
    op.drop_index('ix_trading_partner_is_active', table_name='trading_partner')
    op.drop_index('ix_trading_partner_type', table_name='trading_partner')
    op.drop_index('ix_trading_partner_geo_id', table_name='trading_partner')
    op.drop_index('ix_trading_partner_id', table_name='trading_partner')
    op.drop_table('trading_partner')

    op.drop_index('ix_product_hierarchy_level', table_name='product_hierarchy')
    op.drop_index('ix_product_hierarchy_parent', table_name='product_hierarchy')
    op.drop_index('ix_product_hierarchy_id', table_name='product_hierarchy')
    op.drop_table('product_hierarchy')

    op.drop_index('ix_geography_geo_type', table_name='geography')
    op.drop_index('ix_geography_parent_geo_id', table_name='geography')
    op.drop_index('ix_geography_id', table_name='geography')
    op.drop_table('geography')
