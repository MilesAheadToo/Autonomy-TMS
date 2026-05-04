"""Add inbound_order_line table for AWS SC execution

Revision ID: 20260112_inbound_order_line
Revises: 20260112_add_aws_sc_planning_flag
Create Date: 2026-01-12

This migration adds the inbound_order_line table which is the primary execution
entity for Beer Game work orders in AWS Supply Chain mode.

Key Concepts:
- InboundOrderLine tracks orders FROM suppliers/upstream sites TO destination sites
- Supports PO (Purchase), TO (Transfer), and MO (Manufacturing) order types
- Tracks execution lifecycle: submitted → confirmed → received
- Used for Beer Game order execution (not planning)

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = '20260112_inbound_order_line'
down_revision = '20260112_aws_sc_flag'
branch_labels = None
depends_on = None


def upgrade():
    # Create inbound_order_line table
    op.create_table(
        'inbound_order_line',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_id', sa.String(length=100), nullable=False),
        sa.Column('line_number', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),

        # Site relationships
        sa.Column('to_site_id', sa.Integer(), nullable=False),
        sa.Column('from_site_id', sa.Integer(), nullable=True),
        sa.Column('tpartner_id', sa.Integer(), nullable=True),

        # Order type
        sa.Column('order_type', sa.String(length=20), nullable=False),

        # Quantities
        sa.Column('quantity_submitted', mysql.DOUBLE(), nullable=False),
        sa.Column('quantity_confirmed', mysql.DOUBLE(), nullable=True),
        sa.Column('quantity_received', mysql.DOUBLE(), nullable=True),
        sa.Column('quantity_uom', sa.String(length=20), nullable=True),

        # Dates
        sa.Column('submitted_date', sa.Date(), nullable=True),
        sa.Column('expected_delivery_date', sa.Date(), nullable=True),
        sa.Column('earliest_delivery_date', sa.Date(), nullable=True),
        sa.Column('latest_delivery_date', sa.Date(), nullable=True),
        sa.Column('confirmation_date', sa.Date(), nullable=True),
        sa.Column('order_receive_date', sa.Date(), nullable=True),

        # Status and costs
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('vendor_status', sa.String(length=50), nullable=True),
        sa.Column('cost', mysql.DOUBLE(), nullable=True),
        sa.Column('submitted_cost', mysql.DOUBLE(), nullable=True),
        sa.Column('shipping_cost', mysql.DOUBLE(), nullable=True),
        sa.Column('tax_cost', mysql.DOUBLE(), nullable=True),

        # Lead time
        sa.Column('lead_time_days', sa.Integer(), nullable=True),

        # Multi-tenancy and game context
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('config_id', sa.Integer(), nullable=True),
        sa.Column('game_id', sa.Integer(), nullable=True),
        sa.Column('round_number', sa.Integer(), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'), nullable=True),

        # Primary key
        sa.PrimaryKeyConstraint('id'),

        # Foreign keys
        sa.ForeignKeyConstraint(['product_id'], ['items.id'], ),
        sa.ForeignKeyConstraint(['to_site_id'], ['nodes.id'], ),
        sa.ForeignKeyConstraint(['from_site_id'], ['nodes.id'], ),
        sa.ForeignKeyConstraint(['tpartner_id'], ['trading_partner.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    )

    # Create indexes
    op.create_index('idx_inbound_order_lookup', 'inbound_order_line', ['product_id', 'to_site_id', 'expected_delivery_date'])
    op.create_index('idx_inbound_order_group_config', 'inbound_order_line', ['customer_id', 'config_id'])
    op.create_index('idx_inbound_order_config', 'inbound_order_line', ['config_id'])
    op.create_index('idx_inbound_order_game_round', 'inbound_order_line', ['game_id', 'round_number'])
    op.create_index('idx_inbound_order_status', 'inbound_order_line', ['status'])
    op.create_index('idx_inbound_order_type', 'inbound_order_line', ['order_type'])

    # Update outbound_order_line table to add new execution fields
    op.add_column('outbound_order_line', sa.Column('init_quantity_requested', mysql.DOUBLE(), nullable=True))
    op.add_column('outbound_order_line', sa.Column('quantity_promised', mysql.DOUBLE(), nullable=True))
    op.add_column('outbound_order_line', sa.Column('quantity_delivered', mysql.DOUBLE(), nullable=True))
    op.add_column('outbound_order_line', sa.Column('promised_delivery_date', sa.Date(), nullable=True))
    op.add_column('outbound_order_line', sa.Column('actual_delivery_date', sa.Date(), nullable=True))
    op.add_column('outbound_order_line', sa.Column('status', sa.String(length=50), nullable=True))
    op.add_column('outbound_order_line', sa.Column('ship_from_site_id', sa.Integer(), nullable=True))
    op.add_column('outbound_order_line', sa.Column('ship_to_site_id', sa.Integer(), nullable=True))
    op.add_column('outbound_order_line', sa.Column('round_number', sa.Integer(), nullable=True))

    # Rename ordered_quantity to final_quantity_requested for clarity
    op.alter_column('outbound_order_line', 'ordered_quantity',
                    new_column_name='final_quantity_requested',
                    existing_type=mysql.DOUBLE(),
                    nullable=False)

    # Add foreign keys for new outbound_order_line fields
    op.create_foreign_key(None, 'outbound_order_line', 'nodes', ['ship_from_site_id'], ['id'])
    op.create_foreign_key(None, 'outbound_order_line', 'nodes', ['ship_to_site_id'], ['id'])

    # Add index for game_round on outbound_order_line
    op.create_index('idx_outbound_order_game_round', 'outbound_order_line', ['game_id', 'round_number'])


def downgrade():
    # Drop indexes
    op.drop_index('idx_inbound_order_type', table_name='inbound_order_line')
    op.drop_index('idx_inbound_order_status', table_name='inbound_order_line')
    op.drop_index('idx_inbound_order_game_round', table_name='inbound_order_line')
    op.drop_index('idx_inbound_order_config', table_name='inbound_order_line')
    op.drop_index('idx_inbound_order_group_config', table_name='inbound_order_line')
    op.drop_index('idx_inbound_order_lookup', table_name='inbound_order_line')

    # Drop table
    op.drop_table('inbound_order_line')

    # Revert outbound_order_line changes
    op.drop_index('idx_outbound_order_game_round', table_name='outbound_order_line')
    op.drop_constraint(None, 'outbound_order_line', type_='foreignkey')  # ship_to_site_id FK
    op.drop_constraint(None, 'outbound_order_line', type_='foreignkey')  # ship_from_site_id FK

    op.alter_column('outbound_order_line', 'final_quantity_requested',
                    new_column_name='ordered_quantity',
                    existing_type=mysql.DOUBLE(),
                    nullable=False)

    op.drop_column('outbound_order_line', 'round_number')
    op.drop_column('outbound_order_line', 'ship_to_site_id')
    op.drop_column('outbound_order_line', 'ship_from_site_id')
    op.drop_column('outbound_order_line', 'status')
    op.drop_column('outbound_order_line', 'actual_delivery_date')
    op.drop_column('outbound_order_line', 'promised_delivery_date')
    op.drop_column('outbound_order_line', 'quantity_delivered')
    op.drop_column('outbound_order_line', 'quantity_promised')
    op.drop_column('outbound_order_line', 'init_quantity_requested')
