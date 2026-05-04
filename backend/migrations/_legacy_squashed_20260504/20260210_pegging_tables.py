"""Full-level pegging and AATP consumption tables

Revision ID: 20260210_pegging
Revises: None (standalone)
Create Date: 2026-02-10

Creates tables for Kinaxis-style supply-demand pegging:
- supply_demand_pegging: Links demand records to supply records with quantities
- aatp_consumption_record: Persists AATP consumption decisions

Also adds demand source tracing columns to mrp_requirement.
"""
from alembic import op
import sqlalchemy as sa

revision = '20260210_pegging'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create enum types
    demand_type_enum = sa.Enum(
        'customer_order', 'forecast', 'inter_site_order', 'safety_stock',
        name='demand_type_enum'
    )
    demand_type_enum.create(op.get_bind(), checkfirst=True)

    supply_type_enum = sa.Enum(
        'on_hand', 'purchase_order', 'transfer_order',
        'manufacturing_order', 'planned_order', 'in_transit',
        name='supply_type_enum'
    )
    supply_type_enum.create(op.get_bind(), checkfirst=True)

    pegging_status_enum = sa.Enum(
        'firm', 'planned', 'tentative',
        name='pegging_status_enum'
    )
    pegging_status_enum.create(op.get_bind(), checkfirst=True)

    # 1. supply_demand_pegging
    op.create_table(
        'supply_demand_pegging',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),

        # Ownership
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id'), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id'), nullable=False),

        # Product & site
        sa.Column('product_id', sa.String(100), nullable=False),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('site.id'), nullable=False),

        # Demand side
        sa.Column('demand_type', demand_type_enum, nullable=False),
        sa.Column('demand_id', sa.String(100), nullable=False),
        sa.Column('demand_line_id', sa.Integer(), nullable=True),
        sa.Column('demand_priority', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('demand_quantity', sa.Double(), nullable=False),

        # Supply side
        sa.Column('supply_type', supply_type_enum, nullable=False),
        sa.Column('supply_id', sa.String(100), nullable=False),
        sa.Column('supply_site_id', sa.Integer(), sa.ForeignKey('site.id'), nullable=True),

        # Pegging
        sa.Column('pegged_quantity', sa.Double(), nullable=False),
        sa.Column('pegging_date', sa.Date(), nullable=False),
        sa.Column('pegging_status', pegging_status_enum, nullable=False, server_default='planned'),

        # Chain tracking
        sa.Column('upstream_pegging_id', sa.Integer(),
                  sa.ForeignKey('supply_demand_pegging.id'), nullable=True),
        sa.Column('chain_id', sa.String(64), nullable=False),
        sa.Column('chain_depth', sa.Integer(), nullable=False, server_default='0'),

        # Audit
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', sa.String(50), nullable=True),
        sa.Column('superseded_by', sa.Integer(),
                  sa.ForeignKey('supply_demand_pegging.id'), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
    )

    # Indexes
    op.create_index('ix_pegging_chain', 'supply_demand_pegging', ['chain_id'])
    op.create_index('ix_pegging_demand', 'supply_demand_pegging', ['demand_type', 'demand_id'])
    op.create_index('ix_pegging_supply', 'supply_demand_pegging', ['supply_type', 'supply_id'])
    op.create_index('ix_pegging_product_site', 'supply_demand_pegging', ['product_id', 'site_id'])
    op.create_index('ix_pegging_config_active', 'supply_demand_pegging', ['config_id', 'is_active'])
    op.create_index('ix_pegging_group', 'supply_demand_pegging', ['customer_id'])

    # 2. aatp_consumption_record
    op.create_table(
        'aatp_consumption_record',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('order_id', sa.String(100), nullable=False),
        sa.Column('product_id', sa.String(100), nullable=False),
        sa.Column('location_id', sa.String(100), nullable=False),
        sa.Column('customer_id', sa.String(100), nullable=True),
        sa.Column('requested_qty', sa.Double(), nullable=False),
        sa.Column('fulfilled_qty', sa.Double(), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('consumption_detail', sa.JSON(), nullable=True),
        sa.Column('pegging_id', sa.Integer(),
                  sa.ForeignKey('supply_demand_pegging.id'), nullable=True),
        sa.Column('config_id', sa.Integer(),
                  sa.ForeignKey('supply_chain_configs.id'), nullable=True),
        sa.Column('customer_id', sa.Integer(),
                  sa.ForeignKey('groups.id'), nullable=True),
        sa.Column('consumed_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_index('ix_aatp_order', 'aatp_consumption_record', ['order_id'])
    op.create_index('ix_aatp_product_location', 'aatp_consumption_record',
                    ['product_id', 'location_id'])
    op.create_index('ix_aatp_consumed_at', 'aatp_consumption_record', ['consumed_at'])

    # 3. Add demand source columns to mrp_requirement
    op.add_column('mrp_requirement',
                  sa.Column('demand_source_type', sa.String(50), nullable=True))
    op.add_column('mrp_requirement',
                  sa.Column('demand_source_id', sa.String(100), nullable=True))
    op.add_column('mrp_requirement',
                  sa.Column('demand_chain_id', sa.String(64), nullable=True))


def downgrade():
    # Remove mrp_requirement columns
    op.drop_column('mrp_requirement', 'demand_chain_id')
    op.drop_column('mrp_requirement', 'demand_source_id')
    op.drop_column('mrp_requirement', 'demand_source_type')

    # Drop tables
    op.drop_table('aatp_consumption_record')
    op.drop_table('supply_demand_pegging')

    # Drop enums
    sa.Enum(name='pegging_status_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='supply_type_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='demand_type_enum').drop(op.get_bind(), checkfirst=True)
