"""add shipment table

Revision ID: 20260123_shipment
Revises: 20260123_risk_tables
Create Date: 2026-01-23 16:00:00.000000

Sprint 2: Material Visibility
Adds shipment tracking table for in-transit inventory management
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260123_shipment'
down_revision = '20260123_risk_tables'
branch_labels = None
depends_on = None


def upgrade():
    # Create shipment table
    op.create_table(
        'shipment',
        sa.Column('id', sa.String(100), primary_key=True),
        sa.Column('description', sa.String(500), nullable=True),
        sa.Column('company_id', sa.String(100), nullable=True),

        # Order references
        sa.Column('order_id', sa.String(100), nullable=False),
        sa.Column('order_line_number', sa.Integer(), nullable=True),

        # Product and quantity
        sa.Column('product_id', sa.String(100), nullable=False),
        sa.Column('quantity', sa.Double(), nullable=False),
        sa.Column('uom', sa.String(20), nullable=True),

        # Sites
        sa.Column('from_site_id', sa.String(100), nullable=False),
        sa.Column('to_site_id', sa.String(100), nullable=False),

        # Transportation
        sa.Column('transportation_lane_id', sa.String(100), nullable=True),
        sa.Column('carrier_id', sa.String(100), nullable=True),
        sa.Column('carrier_name', sa.String(200), nullable=True),
        sa.Column('tracking_number', sa.String(100), nullable=True),

        # Status and dates
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('ship_date', sa.DateTime(), nullable=True),
        sa.Column('expected_delivery_date', sa.DateTime(), nullable=True),
        sa.Column('actual_delivery_date', sa.DateTime(), nullable=True),

        # Location tracking
        sa.Column('current_location', sa.String(200), nullable=True),
        sa.Column('current_location_lat', sa.Double(), nullable=True),
        sa.Column('current_location_lon', sa.Double(), nullable=True),
        sa.Column('last_tracking_update', sa.DateTime(), nullable=True),

        # Risk assessment
        sa.Column('delivery_risk_score', sa.Double(), nullable=True),
        sa.Column('risk_level', sa.String(20), nullable=True),
        sa.Column('risk_factors', postgresql.JSON(astext_type=sa.Text()), nullable=True),

        # Event history
        sa.Column('tracking_events', postgresql.JSON(astext_type=sa.Text()), nullable=True),

        # Mitigation
        sa.Column('recommended_actions', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('mitigation_status', sa.String(20), nullable=True),

        # Standard metadata
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('source_event_id', sa.String(100), nullable=True),
        sa.Column('source_update_dttm', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),

        # Foreign keys
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ),
        sa.ForeignKeyConstraint(['from_site_id'], ['site.id'], ),
        sa.ForeignKeyConstraint(['to_site_id'], ['site.id'], ),
        sa.ForeignKeyConstraint(['transportation_lane_id'], ['transportation_lane.id'], ),
    )

    # Create indexes
    op.create_index('idx_shipment_order', 'shipment', ['order_id'])
    op.create_index('idx_shipment_product', 'shipment', ['product_id'])
    op.create_index('idx_shipment_tracking', 'shipment', ['tracking_number', 'carrier_id'])
    op.create_index('idx_shipment_status', 'shipment', ['status', 'expected_delivery_date'])
    op.create_index('idx_shipment_risk', 'shipment', ['risk_level', 'status'])
    op.create_index('idx_shipment_expected', 'shipment', ['expected_delivery_date'])


def downgrade():
    # Drop indexes
    op.drop_index('idx_shipment_expected', table_name='shipment')
    op.drop_index('idx_shipment_risk', table_name='shipment')
    op.drop_index('idx_shipment_status', table_name='shipment')
    op.drop_index('idx_shipment_tracking', table_name='shipment')
    op.drop_index('idx_shipment_product', table_name='shipment')
    op.drop_index('idx_shipment_order', table_name='shipment')

    # Drop table
    op.drop_table('shipment')
