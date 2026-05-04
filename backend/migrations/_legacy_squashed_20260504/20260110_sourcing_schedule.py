"""Add sourcing schedule tables for periodic ordering

Revision ID: 20260110_sourcing_sched
Revises: 20260110_vendor_mgmt
Create Date: 2026-01-10

Adds AWS SC standard sourcing schedule entities:
- sourcing_schedule: Defines periodic ordering schedules (weekly, monthly, etc.)
- sourcing_schedule_details: Specific timing details (day of week, week of month, dates)

Supports:
- Periodic review inventory systems (vs continuous review)
- Scheduled ordering days (e.g., weekly on Mondays, monthly on 1st)
- Hierarchical schedules (company, product_group, product levels)
- Multiple schedule types (daily, weekly, monthly, custom)

Reference: https://docs.aws.amazon.com/aws-supply-chain/latest/userguide/non-transactional.html
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DECIMAL
from sqlalchemy import inspect, text

# revision identifiers
revision = '20260110_sourcing_sched'
down_revision = '20260110_vendor_mgmt'
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
    # 1. Create sourcing_schedule table
    # ===================================================================
    if not table_exists('sourcing_schedule'):
        op.create_table(
            'sourcing_schedule',
            sa.Column('id', sa.String(100), primary_key=True),
            sa.Column('description', sa.String(255)),
            sa.Column('to_site_id', sa.Integer(), sa.ForeignKey('nodes.id'), nullable=False),
            sa.Column('tpartner_id', sa.Integer(), sa.ForeignKey('trading_partner.id')),
            sa.Column('from_site_id', sa.Integer(), sa.ForeignKey('nodes.id')),
            sa.Column('schedule_type', sa.String(50)),  # daily, weekly, monthly, custom
            sa.Column('is_active', sa.String(10), server_default='true'),
            sa.Column('eff_start_date', sa.DateTime(), server_default='1900-01-01 00:00:00'),
            sa.Column('eff_end_date', sa.DateTime(), server_default='9999-12-31 23:59:59'),
            sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
            sa.Column('created_at', sa.DateTime(), server_default=text('CURRENT_TIMESTAMP')),
            sa.Column('updated_at', sa.DateTime(), server_default=text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'))
        )

        # Create indexes
        op.create_index('idx_sourcing_schedule_site', 'sourcing_schedule', ['to_site_id'])
        op.create_index('idx_sourcing_schedule_config', 'sourcing_schedule', ['config_id'])

    # ===================================================================
    # 2. Create sourcing_schedule_details table
    # ===================================================================
    if not table_exists('sourcing_schedule_details'):
        op.create_table(
            'sourcing_schedule_details',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('sourcing_schedule_id', sa.String(100), sa.ForeignKey('sourcing_schedule.id'), nullable=False),

            # Hierarchical override fields
            sa.Column('company_id', sa.String(100)),
            sa.Column('product_group_id', sa.String(100)),
            sa.Column('product_id', sa.Integer(), sa.ForeignKey('items.id')),

            # Scheduling fields
            sa.Column('schedule_date', sa.Date()),  # Specific date for custom schedules
            sa.Column('day_of_week', sa.Integer()),  # 0=Sun, 1=Mon, ..., 6=Sat
            sa.Column('week_of_month', sa.Integer()),  # 1-5

            sa.Column('is_active', sa.String(10), server_default='true'),
            sa.Column('eff_start_date', sa.DateTime(), server_default='1900-01-01 00:00:00'),
            sa.Column('eff_end_date', sa.DateTime(), server_default='9999-12-31 23:59:59'),
            sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id')),
            sa.Column('created_at', sa.DateTime(), server_default=text('CURRENT_TIMESTAMP')),
            sa.Column('updated_at', sa.DateTime(), server_default=text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'))
        )

        # Create indexes
        op.create_index('idx_sourcing_schedule_details_schedule', 'sourcing_schedule_details', ['sourcing_schedule_id'])
        op.create_index('idx_sourcing_schedule_details_product', 'sourcing_schedule_details', ['product_id'])
        op.create_index('idx_sourcing_schedule_details_config', 'sourcing_schedule_details', ['config_id'])


def downgrade():
    # Drop sourcing_schedule_details table
    if table_exists('sourcing_schedule_details'):
        op.drop_index('idx_sourcing_schedule_details_config', 'sourcing_schedule_details')
        op.drop_index('idx_sourcing_schedule_details_product', 'sourcing_schedule_details')
        op.drop_index('idx_sourcing_schedule_details_schedule', 'sourcing_schedule_details')
        op.drop_table('sourcing_schedule_details')

    # Drop sourcing_schedule table
    if table_exists('sourcing_schedule'):
        op.drop_index('idx_sourcing_schedule_config', 'sourcing_schedule')
        op.drop_index('idx_sourcing_schedule_site', 'sourcing_schedule')
        op.drop_table('sourcing_schedule')
