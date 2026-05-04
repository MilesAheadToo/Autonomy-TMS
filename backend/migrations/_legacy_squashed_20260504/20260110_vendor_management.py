"""Add vendor management entities and FK references

Revision ID: 20260110_vendor_mgmt
Revises: 20260110_policy_types
Create Date: 2026-01-10

Adds AWS SC standard vendor management entities:
- trading_partner: Vendors/suppliers with contact and payment info
- vendor_product: Vendor-specific product pricing and lead times

Adds FK references to sourcing_rules:
- tpartner_id: Links to trading_partner for 'buy' type rules
- transportation_lane_id: For 'transfer' type rules
- production_process_id: For 'manufacture' type rules (FK already exists, add column if missing)

Reference: https://docs.aws.amazon.com/aws-supply-chain/latest/userguide/non-transactional.html
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DECIMAL
from sqlalchemy import inspect, text

# revision identifiers
revision = '20260110_vendor_mgmt'
down_revision = '20260110_policy_types'
branch_labels = None
depends_on = None


def column_exists(table_name, column_name):
    """Check if a column exists in a table"""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def table_exists(table_name):
    """Check if a table exists"""
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = inspector.get_table_names()
    return table_name in tables


def index_exists(table_name, index_name):
    """Check if an index exists"""
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = [idx['name'] for idx in inspector.get_indexes(table_name)]
    return index_name in indexes


def upgrade():
    # ===================================================================
    # 1. Skip trading_partner table creation
    # ===================================================================
    # NOTE: trading_partner already exists from 20260107_aws_standard_entities.py
    # with INT id (not STRING). We'll use the existing table.

    # ===================================================================
    # 2. Create vendor_product table
    # ===================================================================
    if not table_exists('vendor_product'):
        op.create_table(
            'vendor_product',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('tpartner_id', sa.Integer(), sa.ForeignKey('trading_partner.id'), nullable=False),  # INT to match trading_partner.id
            sa.Column('product_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
            sa.Column('vendor_product_id', sa.String(100)),
            sa.Column('unit_cost', DECIMAL(10, 2)),
            sa.Column('currency_code', sa.String(10)),
            sa.Column('lead_time_days', sa.Integer()),
            sa.Column('min_order_qty', DECIMAL(10, 2)),
            sa.Column('order_multiple', DECIMAL(10, 2)),
            sa.Column('max_order_qty', DECIMAL(10, 2)),
            sa.Column('is_preferred', sa.String(10), server_default='false'),
            sa.Column('is_active', sa.String(10), server_default='true'),
            sa.Column('eff_start_date', sa.DateTime(), server_default='1900-01-01 00:00:00'),
            sa.Column('eff_end_date', sa.DateTime(), server_default='9999-12-31 23:59:59'),
            sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
            sa.Column('created_at', sa.DateTime(), server_default=text('CURRENT_TIMESTAMP')),
            sa.Column('updated_at', sa.DateTime(), server_default=text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'))
        )

        # Create indexes
        op.create_index('idx_vendor_product_lookup', 'vendor_product', ['tpartner_id', 'product_id'])
        op.create_index('idx_vendor_product_config', 'vendor_product', ['config_id'])

    # ===================================================================
    # 3. Add FK fields to sourcing_rules table
    # ===================================================================

    # Add tpartner_id (for 'buy' type rules)
    if not column_exists('sourcing_rules', 'tpartner_id'):
        op.add_column('sourcing_rules',
            sa.Column('tpartner_id', sa.Integer(), nullable=True))  # INT to match trading_partner.id

        # Add FK constraint if trading_partner table exists
        if table_exists('trading_partner'):
            op.create_foreign_key(
                'fk_sourcing_rules_tpartner',
                'sourcing_rules', 'trading_partner',
                ['tpartner_id'], ['id']
            )

    # Add transportation_lane_id (for 'transfer' type rules)
    if not column_exists('sourcing_rules', 'transportation_lane_id'):
        op.add_column('sourcing_rules',
            sa.Column('transportation_lane_id', sa.String(100), nullable=True))

    # Add production_process_id (for 'manufacture' type rules) if it doesn't exist
    # Note: This field may already exist from earlier migrations
    if not column_exists('sourcing_rules', 'production_process_id'):
        op.add_column('sourcing_rules',
            sa.Column('production_process_id', sa.String(100), nullable=True))

        # Add FK constraint if production_process table exists
        if table_exists('production_process'):
            op.create_foreign_key(
                'fk_sourcing_rules_prod_process',
                'sourcing_rules', 'production_process',
                ['production_process_id'], ['id']
            )

    # Create indexes for FK lookups
    if not index_exists('sourcing_rules', 'idx_sourcing_tpartner'):
        op.create_index('idx_sourcing_tpartner', 'sourcing_rules', ['tpartner_id'])

    if not index_exists('sourcing_rules', 'idx_sourcing_trans_lane'):
        op.create_index('idx_sourcing_trans_lane', 'sourcing_rules', ['transportation_lane_id'])


def downgrade():
    # Drop indexes from sourcing_rules
    if index_exists('sourcing_rules', 'idx_sourcing_trans_lane'):
        op.drop_index('idx_sourcing_trans_lane', 'sourcing_rules')

    if index_exists('sourcing_rules', 'idx_sourcing_tpartner'):
        op.drop_index('idx_sourcing_tpartner', 'sourcing_rules')

    # Drop FK constraints and columns from sourcing_rules
    try:
        op.drop_constraint('fk_sourcing_rules_prod_process', 'sourcing_rules', type_='foreignkey')
    except:
        pass

    try:
        op.drop_constraint('fk_sourcing_rules_tpartner', 'sourcing_rules', type_='foreignkey')
    except:
        pass

    if column_exists('sourcing_rules', 'production_process_id'):
        op.drop_column('sourcing_rules', 'production_process_id')

    if column_exists('sourcing_rules', 'transportation_lane_id'):
        op.drop_column('sourcing_rules', 'transportation_lane_id')

    if column_exists('sourcing_rules', 'tpartner_id'):
        op.drop_column('sourcing_rules', 'tpartner_id')

    # Drop vendor_product table
    if table_exists('vendor_product'):
        op.drop_index('idx_vendor_product_config', 'vendor_product')
        op.drop_index('idx_vendor_product_lookup', 'vendor_product')
        op.drop_table('vendor_product')

    # Skip dropping trading_partner table (it existed before this migration)
