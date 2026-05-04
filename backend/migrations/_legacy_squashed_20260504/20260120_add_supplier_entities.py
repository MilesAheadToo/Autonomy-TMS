"""add supplier entities

AWS Supply Chain Compliant Supplier Entities:
- trading_partners (TradingPartner with type='vendor' for suppliers)
- vendor_products (VendorProduct for supplier-item associations)
- vendor_lead_times (VendorLeadTime for hierarchical lead time management)
- supplier_performance (Platform extension for performance analytics)

Revision ID: 20260120_add_supplier_entities
Revises: 20260120_add_capacity_plans
Create Date: 2026-01-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260120_add_supplier_entities'
down_revision = '20260120_add_capacity_plans'
branch_labels = None
depends_on = None


def upgrade():
    # ========================================================================
    # Create trading_partners table (AWS SC: trading_partner)
    # ========================================================================
    op.create_table('trading_partners',
        # AWS SC Core - Composite Primary Key (temporal tracking)
        sa.Column('id', sa.String(length=100), nullable=False),
        sa.Column('tpartner_type', sa.String(length=50), nullable=False, comment='vendor, customer, 3PL, carrier'),
        sa.Column('geo_id', sa.String(length=100), nullable=False),
        sa.Column('eff_start_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('eff_end_date', sa.DateTime(timezone=True), nullable=False),

        # AWS SC Core - Descriptive
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('company_id', sa.String(length=100), nullable=True),
        sa.Column('is_active', sa.String(length=10), nullable=False, server_default='true'),

        # AWS SC Core - Address
        sa.Column('address_1', sa.String(length=255), nullable=True),
        sa.Column('address_2', sa.String(length=255), nullable=True),
        sa.Column('address_3', sa.String(length=255), nullable=True),
        sa.Column('city', sa.String(length=100), nullable=True),
        sa.Column('state_prov', sa.String(length=100), nullable=True),
        sa.Column('postal_code', sa.String(length=50), nullable=True),
        sa.Column('country', sa.String(length=100), nullable=True),

        # AWS SC Core - Contact & Location
        sa.Column('phone_number', sa.String(length=50), nullable=True),
        sa.Column('time_zone', sa.String(length=50), nullable=True),
        sa.Column('latitude', sa.Double(), nullable=True),
        sa.Column('longitude', sa.Double(), nullable=True),

        # AWS SC Core - Source Tracking
        sa.Column('source', sa.String(length=100), nullable=True),
        sa.Column('source_event_id', sa.String(length=100), nullable=True),
        sa.Column('source_update_dttm', sa.DateTime(timezone=True), nullable=True),

        # Extension: Supplier Tier Classification
        sa.Column('tier', sa.String(length=50), nullable=True, comment='TIER_1, TIER_2, TIER_3, TIER_4'),

        # Extension: Performance Metrics (cached)
        sa.Column('on_time_delivery_rate', sa.Double(), nullable=True, comment='Percentage 0-100'),
        sa.Column('quality_rating', sa.Double(), nullable=True, comment='Score 0-100'),
        sa.Column('lead_time_reliability', sa.Double(), nullable=True, comment='Percentage 0-100'),
        sa.Column('total_spend_ytd', sa.Double(), nullable=False, server_default='0.0'),

        # Extension: Capacity Constraints
        sa.Column('production_capacity', sa.Double(), nullable=True),
        sa.Column('capacity_unit', sa.String(length=50), nullable=True),
        sa.Column('minimum_order_quantity', sa.Double(), nullable=True),
        sa.Column('maximum_order_quantity', sa.Double(), nullable=True),

        # Extension: Certifications & Compliance
        sa.Column('iso_certified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('certifications', sa.String(length=500), nullable=True),

        # Extension: Risk Assessment
        sa.Column('risk_level', sa.String(length=50), nullable=True, comment='LOW, MEDIUM, HIGH, CRITICAL'),
        sa.Column('risk_notes', sa.String(length=1000), nullable=True),

        # Extension: Financial Details
        sa.Column('tax_id', sa.String(length=50), nullable=True),
        sa.Column('duns_number', sa.String(length=20), nullable=True),
        sa.Column('payment_terms', sa.String(length=100), nullable=True),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='USD'),

        # Extension: Contact Information
        sa.Column('contact_name', sa.String(length=255), nullable=True),
        sa.Column('contact_email', sa.String(length=255), nullable=True),

        # Extension: Audit Fields
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'),

        # Extension: Notes
        sa.Column('notes', sa.String(length=2000), nullable=True),

        # Primary Key and Foreign Keys
        sa.PrimaryKeyConstraint('id', 'tpartner_type', 'geo_id', 'eff_start_date', 'eff_end_date'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    )

    # Indexes for trading_partners
    op.create_index('ix_trading_partners_tpartner_type', 'trading_partners', ['tpartner_type'])
    op.create_index('ix_trading_partners_company_id', 'trading_partners', ['company_id'])
    op.create_index('ix_trading_partners_is_active', 'trading_partners', ['is_active'])
    op.create_index('ix_trading_partners_tier', 'trading_partners', ['tier'])
    op.create_index('ix_trading_partners_country', 'trading_partners', ['country'])

    # ========================================================================
    # Create vendor_products table (AWS SC: vendor_product)
    # ========================================================================
    op.create_table('vendor_products',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),

        # AWS SC Core Fields
        sa.Column('company_id', sa.String(length=100), nullable=True),
        sa.Column('tpartner_id', sa.String(length=100), nullable=False, comment='FK to trading_partners.id'),
        sa.Column('product_id', sa.Integer(), nullable=False, comment='FK to items.id'),
        sa.Column('vendor_product_id', sa.String(length=100), nullable=True, comment='Vendor item code'),
        sa.Column('vendor_unit_cost', sa.Double(), nullable=False),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='USD'),
        sa.Column('eff_start_date', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('eff_end_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.String(length=10), nullable=False, server_default='true'),
        sa.Column('source', sa.String(length=100), nullable=True),
        sa.Column('source_event_id', sa.String(length=100), nullable=True),
        sa.Column('source_update_dttm', sa.DateTime(timezone=True), nullable=True),

        # Extension: Multi-sourcing Support
        sa.Column('priority', sa.Integer(), nullable=False, server_default=sa.text("1"), comment='1=primary, 2=secondary, etc.'),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default='false'),

        # Extension: Quantity Constraints
        sa.Column('minimum_order_quantity', sa.Double(), nullable=True),
        sa.Column('maximum_order_quantity', sa.Double(), nullable=True),
        sa.Column('order_multiple', sa.Double(), nullable=True),

        # Extension: Supplier-specific item naming
        sa.Column('vendor_item_name', sa.String(length=255), nullable=True),

        # Extension: Audit Fields
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),

        # Primary Key and Foreign Keys
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('tpartner_id', 'product_id', 'eff_start_date', name='uq_vendor_product_effective'),
    )

    # Indexes for vendor_products
    op.create_index('ix_vendor_products_id', 'vendor_products', ['id'])
    op.create_index('ix_vendor_products_tpartner_id', 'vendor_products', ['tpartner_id'])
    op.create_index('ix_vendor_products_product_id', 'vendor_products', ['product_id'])
    op.create_index('ix_vendor_products_priority', 'vendor_products', ['priority'])
    op.create_index('ix_vendor_products_is_primary', 'vendor_products', ['is_primary'])
    op.create_index('ix_vendor_products_effective_dates', 'vendor_products', ['eff_start_date', 'eff_end_date'])

    # ========================================================================
    # Create vendor_lead_times table (AWS SC: vendor_lead_time)
    # ========================================================================
    op.create_table('vendor_lead_times',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),

        # AWS SC Core Fields - Hierarchy Levels (most specific wins)
        sa.Column('company_id', sa.String(length=100), nullable=True),
        sa.Column('region_id', sa.String(length=100), nullable=True),
        sa.Column('site_id', sa.Integer(), nullable=True, comment='FK to nodes.id'),
        sa.Column('product_group_id', sa.String(length=100), nullable=True),
        sa.Column('product_id', sa.Integer(), nullable=True, comment='FK to items.id'),

        # AWS SC Core Fields - Lead Time
        sa.Column('tpartner_id', sa.String(length=100), nullable=False, comment='FK to trading_partners.id'),
        sa.Column('lead_time_days', sa.Double(), nullable=False),

        # AWS SC Core Fields - Effective Dates
        sa.Column('eff_start_date', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('eff_end_date', sa.DateTime(timezone=True), nullable=True),

        # AWS SC Core Fields - Source Tracking
        sa.Column('source', sa.String(length=100), nullable=True),
        sa.Column('source_event_id', sa.String(length=100), nullable=True),
        sa.Column('source_update_dttm', sa.DateTime(timezone=True), nullable=True),

        # Extension: Lead Time Variability
        sa.Column('lead_time_variability_days', sa.Double(), nullable=True, comment='Std dev for stochastic planning'),

        # Extension: Audit Fields
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),

        # Primary Key and Foreign Keys
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['site_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'], ondelete='CASCADE'),
    )

    # Indexes for vendor_lead_times
    op.create_index('ix_vendor_lead_times_id', 'vendor_lead_times', ['id'])
    op.create_index('ix_vendor_lead_times_tpartner_id', 'vendor_lead_times', ['tpartner_id'])
    op.create_index('ix_vendor_lead_times_product_id', 'vendor_lead_times', ['product_id'])
    op.create_index('ix_vendor_lead_times_site_id', 'vendor_lead_times', ['site_id'])
    op.create_index('ix_vendor_lead_times_effective_dates', 'vendor_lead_times', ['eff_start_date', 'eff_end_date'])

    # ========================================================================
    # Create supplier_performance table (Platform Extension)
    # ========================================================================
    op.create_table('supplier_performance',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),

        # Foreign key to TradingPartner
        sa.Column('tpartner_id', sa.String(length=100), nullable=False, comment='FK to trading_partners.id'),

        # Performance period
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_type', sa.String(length=20), nullable=False, server_default='MONTHLY', comment='WEEKLY, MONTHLY, QUARTERLY, YEARLY'),

        # Delivery metrics
        sa.Column('orders_placed', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('orders_delivered_on_time', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('orders_delivered_late', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('average_days_late', sa.Double(), nullable=True),

        # Quality metrics
        sa.Column('units_received', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('units_accepted', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('units_rejected', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column('reject_rate_percent', sa.Double(), nullable=True),

        # Lead time metrics
        sa.Column('average_lead_time_days', sa.Double(), nullable=True),
        sa.Column('std_dev_lead_time_days', sa.Double(), nullable=True),

        # Cost metrics
        sa.Column('total_spend', sa.Double(), nullable=False, server_default='0.0'),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='USD'),

        # Calculated metrics
        sa.Column('on_time_delivery_rate', sa.Double(), nullable=True, comment='Percentage 0-100'),
        sa.Column('quality_rating', sa.Double(), nullable=True, comment='Score 0-100'),
        sa.Column('overall_performance_score', sa.Double(), nullable=True, comment='Score 0-100'),

        # Audit fields
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),

        # Primary Key
        sa.PrimaryKeyConstraint('id'),
    )

    # Indexes for supplier_performance
    op.create_index('ix_supplier_performance_id', 'supplier_performance', ['id'])
    op.create_index('ix_supplier_performance_tpartner_id', 'supplier_performance', ['tpartner_id'])
    op.create_index('ix_supplier_performance_period', 'supplier_performance', ['period_start', 'period_end'])
    op.create_index('ix_supplier_performance_period_type', 'supplier_performance', ['period_type'])


def downgrade():
    # Drop tables in reverse order (respecting foreign keys)
    op.drop_index('ix_supplier_performance_period_type', table_name='supplier_performance')
    op.drop_index('ix_supplier_performance_period', table_name='supplier_performance')
    op.drop_index('ix_supplier_performance_tpartner_id', table_name='supplier_performance')
    op.drop_index('ix_supplier_performance_id', table_name='supplier_performance')
    op.drop_table('supplier_performance')

    op.drop_index('ix_vendor_lead_times_effective_dates', table_name='vendor_lead_times')
    op.drop_index('ix_vendor_lead_times_site_id', table_name='vendor_lead_times')
    op.drop_index('ix_vendor_lead_times_product_id', table_name='vendor_lead_times')
    op.drop_index('ix_vendor_lead_times_tpartner_id', table_name='vendor_lead_times')
    op.drop_index('ix_vendor_lead_times_id', table_name='vendor_lead_times')
    op.drop_table('vendor_lead_times')

    op.drop_index('ix_vendor_products_effective_dates', table_name='vendor_products')
    op.drop_index('ix_vendor_products_is_primary', table_name='vendor_products')
    op.drop_index('ix_vendor_products_priority', table_name='vendor_products')
    op.drop_index('ix_vendor_products_product_id', table_name='vendor_products')
    op.drop_index('ix_vendor_products_tpartner_id', table_name='vendor_products')
    op.drop_index('ix_vendor_products_id', table_name='vendor_products')
    op.drop_table('vendor_products')

    op.drop_index('ix_trading_partners_country', table_name='trading_partners')
    op.drop_index('ix_trading_partners_tier', table_name='trading_partners')
    op.drop_index('ix_trading_partners_is_active', table_name='trading_partners')
    op.drop_index('ix_trading_partners_company_id', table_name='trading_partners')
    op.drop_index('ix_trading_partners_tpartner_type', table_name='trading_partners')
    op.drop_table('trading_partners')
