"""Add hierarchical override fields for AWS SC compliance (safe version)

Revision ID: 20260110_hierarchical_safe
Revises: 20260110_sourcing_rules_config
Create Date: 2026-01-10

Safely adds hierarchical override fields, checking for existing columns.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers
revision = '20260110_hierarchical_safe'
down_revision = '20260110_sourcing_rules_config'
branch_labels = None
depends_on = None


def column_exists(table_name, column_name):
    """Check if a column exists in a table"""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def index_exists(table_name, index_name):
    """Check if an index exists"""
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = [idx['name'] for idx in inspector.get_indexes(table_name)]
    return index_name in indexes


def upgrade():
    # ==================================================================
    # 1. Add hierarchy fields to nodes table
    # ==================================================================
    if not column_exists('nodes', 'segment_id'):
        op.add_column('nodes', sa.Column('segment_id', sa.String(100), nullable=True))

    if not column_exists('nodes', 'company_id'):
        op.add_column('nodes', sa.Column('company_id', sa.String(100), nullable=True))

    # Add indexes
    if not index_exists('nodes', 'idx_nodes_segment'):
        op.create_index('idx_nodes_segment', 'nodes', ['segment_id'])

    if not index_exists('nodes', 'idx_nodes_company'):
        op.create_index('idx_nodes_company', 'nodes', ['company_id'])

    # ===================================================================
    # 2. Add product_group_id to items table
    # ===================================================================
    if not column_exists('items', 'product_group_id'):
        op.add_column('items', sa.Column('product_group_id', sa.String(100), nullable=True))

    if not index_exists('items', 'idx_items_product_group'):
        op.create_index('idx_items_product_group', 'items', ['product_group_id'])

    # ===================================================================
    # 3. Add hierarchy fields to inv_policy table
    # ===================================================================
    if not column_exists('inv_policy', 'product_group_id'):
        op.add_column('inv_policy', sa.Column('product_group_id', sa.String(100), nullable=True))

    if not column_exists('inv_policy', 'dest_geo_id'):
        op.add_column('inv_policy', sa.Column('dest_geo_id', sa.String(100), nullable=True))

    if not column_exists('inv_policy', 'segment_id'):
        op.add_column('inv_policy', sa.Column('segment_id', sa.String(100), nullable=True))

    if not column_exists('inv_policy', 'company_id'):
        op.add_column('inv_policy', sa.Column('company_id', sa.String(100), nullable=True))

    # Add composite indexes
    if not index_exists('inv_policy', 'idx_inv_policy_prod_group_site'):
        op.create_index('idx_inv_policy_prod_group_site', 'inv_policy', ['product_group_id', 'site_id'])

    if not index_exists('inv_policy', 'idx_inv_policy_prod_geo'):
        op.create_index('idx_inv_policy_prod_geo', 'inv_policy', ['product_id', 'dest_geo_id'])

    if not index_exists('inv_policy', 'idx_inv_policy_prod_group_geo'):
        op.create_index('idx_inv_policy_prod_group_geo', 'inv_policy', ['product_group_id', 'dest_geo_id'])

    if not index_exists('inv_policy', 'idx_inv_policy_company'):
        op.create_index('idx_inv_policy_company', 'inv_policy', ['company_id'])

    # ===================================================================
    # 4. Add hierarchy fields to sourcing_rules table
    # ===================================================================
    if not column_exists('sourcing_rules', 'product_group_id'):
        op.add_column('sourcing_rules', sa.Column('product_group_id', sa.String(100), nullable=True))

    if not column_exists('sourcing_rules', 'company_id'):
        op.add_column('sourcing_rules', sa.Column('company_id', sa.String(100), nullable=True))

    # Add indexes
    if not index_exists('sourcing_rules', 'idx_sourcing_prod_group_site'):
        op.create_index('idx_sourcing_prod_group_site', 'sourcing_rules', ['product_group_id', 'site_id'])

    if not index_exists('sourcing_rules', 'idx_sourcing_company_site'):
        op.create_index('idx_sourcing_company_site', 'sourcing_rules', ['company_id', 'site_id'])

    # ===================================================================
    # 5. Add hierarchy fields to vendor_lead_time table (if table exists)
    # ===================================================================
    tables = inspect(op.get_bind()).get_table_names()
    if 'vendor_lead_time' in tables:
        if not column_exists('vendor_lead_time', 'segment_id'):
            op.add_column('vendor_lead_time', sa.Column('segment_id', sa.String(100), nullable=True))

        # Add composite indexes
        if not index_exists('vendor_lead_time', 'idx_vlt_prod_geo'):
            op.create_index('idx_vlt_prod_geo', 'vendor_lead_time', ['product_id', 'geo_id'])

        if not index_exists('vendor_lead_time', 'idx_vlt_prod_group_site'):
            op.create_index('idx_vlt_prod_group_site', 'vendor_lead_time', ['product_group_id', 'site_id'])

        if not index_exists('vendor_lead_time', 'idx_vlt_prod_group_geo'):
            op.create_index('idx_vlt_prod_group_geo', 'vendor_lead_time', ['product_group_id', 'geo_id'])

        if not index_exists('vendor_lead_time', 'idx_vlt_company'):
            op.create_index('idx_vlt_company', 'vendor_lead_time', ['company_id'])


def downgrade():
    # Drop indexes and columns in reverse order
    tables = inspect(op.get_bind()).get_table_names()

    # vendor_lead_time
    if 'vendor_lead_time' in tables:
        if index_exists('vendor_lead_time', 'idx_vlt_company'):
            op.drop_index('idx_vlt_company', 'vendor_lead_time')
        if index_exists('vendor_lead_time', 'idx_vlt_prod_group_geo'):
            op.drop_index('idx_vlt_prod_group_geo', 'vendor_lead_time')
        if index_exists('vendor_lead_time', 'idx_vlt_prod_group_site'):
            op.drop_index('idx_vlt_prod_group_site', 'vendor_lead_time')
        if index_exists('vendor_lead_time', 'idx_vlt_prod_geo'):
            op.drop_index('idx_vlt_prod_geo', 'vendor_lead_time')
        if column_exists('vendor_lead_time', 'segment_id'):
            op.drop_column('vendor_lead_time', 'segment_id')

    # sourcing_rules
    if index_exists('sourcing_rules', 'idx_sourcing_company_site'):
        op.drop_index('idx_sourcing_company_site', 'sourcing_rules')
    if index_exists('sourcing_rules', 'idx_sourcing_prod_group_site'):
        op.drop_index('idx_sourcing_prod_group_site', 'sourcing_rules')
    if column_exists('sourcing_rules', 'company_id'):
        op.drop_column('sourcing_rules', 'company_id')
    if column_exists('sourcing_rules', 'product_group_id'):
        op.drop_column('sourcing_rules', 'product_group_id')

    # inv_policy
    if index_exists('inv_policy', 'idx_inv_policy_company'):
        op.drop_index('idx_inv_policy_company', 'inv_policy')
    if index_exists('inv_policy', 'idx_inv_policy_prod_group_geo'):
        op.drop_index('idx_inv_policy_prod_group_geo', 'inv_policy')
    if index_exists('inv_policy', 'idx_inv_policy_prod_geo'):
        op.drop_index('idx_inv_policy_prod_geo', 'inv_policy')
    if index_exists('inv_policy', 'idx_inv_policy_prod_group_site'):
        op.drop_index('idx_inv_policy_prod_group_site', 'inv_policy')
    if column_exists('inv_policy', 'company_id'):
        op.drop_column('inv_policy', 'company_id')
    if column_exists('inv_policy', 'segment_id'):
        op.drop_column('inv_policy', 'segment_id')
    if column_exists('inv_policy', 'dest_geo_id'):
        op.drop_column('inv_policy', 'dest_geo_id')
    if column_exists('inv_policy', 'product_group_id'):
        op.drop_column('inv_policy', 'product_group_id')

    # items
    if index_exists('items', 'idx_items_product_group'):
        op.drop_index('idx_items_product_group', 'items')
    if column_exists('items', 'product_group_id'):
        op.drop_column('items', 'product_group_id')

    # nodes
    if index_exists('nodes', 'idx_nodes_company'):
        op.drop_index('idx_nodes_company', 'nodes')
    if index_exists('nodes', 'idx_nodes_segment'):
        op.drop_index('idx_nodes_segment', 'nodes')
    if column_exists('nodes', 'company_id'):
        op.drop_column('nodes', 'company_id')
    if column_exists('nodes', 'segment_id'):
        op.drop_column('nodes', 'segment_id')
