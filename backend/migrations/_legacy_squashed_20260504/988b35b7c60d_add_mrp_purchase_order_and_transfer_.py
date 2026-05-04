"""Add MRP, Purchase Order, and Transfer Order tables

Revision ID: 988b35b7c60d
Revises: 1f1a0e541814
Create Date: 2026-01-20 19:13:53.915870

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '988b35b7c60d'
down_revision: Union[str, None] = '1f1a0e541814'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create MRP Run table
    op.create_table(
        'mrp_run',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.String(length=100), nullable=False),
        sa.Column('mps_plan_id', sa.Integer(), nullable=True),
        sa.Column('config_id', sa.Integer(), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('explode_bom_levels', sa.Integer(), nullable=True),
        sa.Column('total_components', sa.Integer(), nullable=True),
        sa.Column('total_planned_orders', sa.Integer(), nullable=True),
        sa.Column('total_exceptions', sa.Integer(), nullable=True),
        sa.Column('exceptions_by_severity', sa.JSON(), nullable=True),
        sa.Column('orders_by_type', sa.JSON(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['mps_plan_id'], ['mps_plans.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_mrp_run_id', 'mrp_run', ['run_id'], unique=True)
    op.create_index('idx_mrp_mps_plan', 'mrp_run', ['mps_plan_id'], unique=False)
    op.create_index('idx_mrp_status', 'mrp_run', ['status'], unique=False)
    op.create_index('idx_mrp_group', 'mrp_run', ['customer_id'], unique=False)
    op.create_index('idx_mrp_created_at', 'mrp_run', ['created_at'], unique=False)

    # Create MRP Requirement table
    op.create_table(
        'mrp_requirement',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mrp_run_id', sa.Integer(), nullable=False),
        sa.Column('component_id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=True),
        sa.Column('bom_level', sa.Integer(), nullable=False),
        sa.Column('period_index', sa.Integer(), nullable=False),
        sa.Column('period_start_date', sa.Date(), nullable=False),
        sa.Column('gross_requirement', sa.Double(), nullable=False),
        sa.Column('scheduled_receipts', sa.Double(), nullable=True),
        sa.Column('projected_available', sa.Double(), nullable=True),
        sa.Column('net_requirement', sa.Double(), nullable=False),
        sa.Column('planned_order_receipt', sa.Double(), nullable=True),
        sa.Column('planned_order_release', sa.Double(), nullable=True),
        sa.Column('source_type', sa.String(length=20), nullable=True),
        sa.Column('source_site_id', sa.Integer(), nullable=True),
        sa.Column('lead_time_days', sa.Integer(), nullable=True),
        sa.Column('safety_stock', sa.Double(), nullable=True),
        sa.Column('lot_size', sa.Double(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['component_id'], ['items.id'], ),
        sa.ForeignKeyConstraint(['mrp_run_id'], ['mrp_run.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['site_id'], ['nodes.id'], ),
        sa.ForeignKeyConstraint(['source_site_id'], ['nodes.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_mrp_req_run', 'mrp_requirement', ['mrp_run_id'], unique=False)
    op.create_index('idx_mrp_req_component', 'mrp_requirement', ['component_id'], unique=False)
    op.create_index('idx_mrp_req_site', 'mrp_requirement', ['site_id'], unique=False)
    op.create_index('idx_mrp_req_period', 'mrp_requirement', ['mrp_run_id', 'period_index'], unique=False)

    # Create MRP Exception table
    op.create_table(
        'mrp_exception',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mrp_run_id', sa.Integer(), nullable=False),
        sa.Column('exception_type', sa.String(length=50), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=False),
        sa.Column('component_id', sa.Integer(), nullable=True),
        sa.Column('site_id', sa.Integer(), nullable=True),
        sa.Column('period_index', sa.Integer(), nullable=True),
        sa.Column('period_start_date', sa.Date(), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('quantity', sa.Double(), nullable=True),
        sa.Column('related_order_id', sa.String(length=100), nullable=True),
        sa.Column('is_resolved', sa.Boolean(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_by_id', sa.Integer(), nullable=True),
        sa.Column('resolution_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['component_id'], ['items.id'], ),
        sa.ForeignKeyConstraint(['mrp_run_id'], ['mrp_run.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['resolved_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['site_id'], ['nodes.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_mrp_exc_run', 'mrp_exception', ['mrp_run_id'], unique=False)
    op.create_index('idx_mrp_exc_type', 'mrp_exception', ['exception_type'], unique=False)
    op.create_index('idx_mrp_exc_severity', 'mrp_exception', ['severity'], unique=False)
    op.create_index('idx_mrp_exc_resolved', 'mrp_exception', ['is_resolved'], unique=False)

    # Create Purchase Order table
    op.create_table(
        'purchase_order',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('po_number', sa.String(length=100), nullable=False),
        sa.Column('vendor_id', sa.String(length=100), nullable=True),
        sa.Column('supplier_site_id', sa.Integer(), nullable=True),
        sa.Column('destination_site_id', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('order_date', sa.Date(), nullable=False),
        sa.Column('requested_delivery_date', sa.Date(), nullable=False),
        sa.Column('promised_delivery_date', sa.Date(), nullable=True),
        sa.Column('actual_delivery_date', sa.Date(), nullable=True),
        sa.Column('total_amount', sa.Double(), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=True),
        sa.Column('payment_terms', sa.String(length=50), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('mrp_run_id', sa.String(length=100), nullable=True),
        sa.Column('planning_run_id', sa.String(length=100), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('approved_by_id', sa.Integer(), nullable=True),
        sa.Column('received_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('received_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['approved_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['destination_site_id'], ['nodes.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['received_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['supplier_site_id'], ['nodes.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_po_number', 'purchase_order', ['po_number'], unique=True)
    op.create_index('idx_po_vendor', 'purchase_order', ['vendor_id'], unique=False)
    op.create_index('idx_po_dest_site', 'purchase_order', ['destination_site_id'], unique=False)
    op.create_index('idx_po_status', 'purchase_order', ['status'], unique=False)
    op.create_index('idx_po_order_date', 'purchase_order', ['order_date'], unique=False)
    op.create_index('idx_po_config', 'purchase_order', ['config_id'], unique=False)
    op.create_index('idx_po_group', 'purchase_order', ['customer_id'], unique=False)
    op.create_index('idx_po_mrp_run', 'purchase_order', ['mrp_run_id'], unique=False)

    # Create Purchase Order Line Item table
    op.create_table(
        'purchase_order_line_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('po_id', sa.Integer(), nullable=False),
        sa.Column('line_number', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Double(), nullable=False),
        sa.Column('received_quantity', sa.Double(), nullable=True),
        sa.Column('rejected_quantity', sa.Double(), nullable=True),
        sa.Column('unit_price', sa.Double(), nullable=True),
        sa.Column('line_amount', sa.Double(), nullable=True),
        sa.Column('requested_delivery_date', sa.Date(), nullable=False),
        sa.Column('promised_delivery_date', sa.Date(), nullable=True),
        sa.Column('actual_delivery_date', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['po_id'], ['purchase_order.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_po_line_po', 'purchase_order_line_item', ['po_id'], unique=False)
    op.create_index('idx_po_line_product', 'purchase_order_line_item', ['product_id'], unique=False)
    op.create_index('idx_po_line_number', 'purchase_order_line_item', ['po_id', 'line_number'], unique=False)

    # Create Transfer Order table
    op.create_table(
        'transfer_order',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('to_number', sa.String(length=100), nullable=False),
        sa.Column('source_site_id', sa.Integer(), nullable=False),
        sa.Column('destination_site_id', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('shipment_date', sa.Date(), nullable=False),
        sa.Column('estimated_delivery_date', sa.Date(), nullable=False),
        sa.Column('actual_ship_date', sa.Date(), nullable=True),
        sa.Column('actual_delivery_date', sa.Date(), nullable=True),
        sa.Column('transportation_mode', sa.String(length=50), nullable=True),
        sa.Column('carrier', sa.String(length=100), nullable=True),
        sa.Column('tracking_number', sa.String(length=100), nullable=True),
        sa.Column('transportation_lane_id', sa.String(length=100), nullable=True),
        sa.Column('transportation_cost', sa.Double(), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('mrp_run_id', sa.String(length=100), nullable=True),
        sa.Column('planning_run_id', sa.String(length=100), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('released_by_id', sa.Integer(), nullable=True),
        sa.Column('picked_by_id', sa.Integer(), nullable=True),
        sa.Column('shipped_by_id', sa.Integer(), nullable=True),
        sa.Column('received_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('released_at', sa.DateTime(), nullable=True),
        sa.Column('picked_at', sa.DateTime(), nullable=True),
        sa.Column('shipped_at', sa.DateTime(), nullable=True),
        sa.Column('received_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['destination_site_id'], ['nodes.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['picked_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['received_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['released_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['shipped_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['source_site_id'], ['nodes.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_to_number', 'transfer_order', ['to_number'], unique=True)
    op.create_index('idx_to_source_site', 'transfer_order', ['source_site_id'], unique=False)
    op.create_index('idx_to_dest_site', 'transfer_order', ['destination_site_id'], unique=False)
    op.create_index('idx_to_status', 'transfer_order', ['status'], unique=False)
    op.create_index('idx_to_shipment_date', 'transfer_order', ['shipment_date'], unique=False)
    op.create_index('idx_to_config', 'transfer_order', ['config_id'], unique=False)
    op.create_index('idx_to_group', 'transfer_order', ['customer_id'], unique=False)
    op.create_index('idx_to_mrp_run', 'transfer_order', ['mrp_run_id'], unique=False)
    op.create_index('idx_to_lane', 'transfer_order', ['source_site_id', 'destination_site_id'], unique=False)

    # Create Transfer Order Line Item table
    op.create_table(
        'transfer_order_line_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('to_id', sa.Integer(), nullable=False),
        sa.Column('line_number', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Double(), nullable=False),
        sa.Column('picked_quantity', sa.Double(), nullable=True),
        sa.Column('shipped_quantity', sa.Double(), nullable=True),
        sa.Column('received_quantity', sa.Double(), nullable=True),
        sa.Column('damaged_quantity', sa.Double(), nullable=True),
        sa.Column('requested_ship_date', sa.Date(), nullable=False),
        sa.Column('requested_delivery_date', sa.Date(), nullable=False),
        sa.Column('actual_ship_date', sa.Date(), nullable=True),
        sa.Column('actual_delivery_date', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'], ),
        sa.ForeignKeyConstraint(['to_id'], ['transfer_order.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_to_line_to', 'transfer_order_line_item', ['to_id'], unique=False)
    op.create_index('idx_to_line_product', 'transfer_order_line_item', ['product_id'], unique=False)
    op.create_index('idx_to_line_number', 'transfer_order_line_item', ['to_id', 'line_number'], unique=False)


def downgrade() -> None:
    # Drop tables in reverse order (child tables first)
    op.drop_index('idx_to_line_number', table_name='transfer_order_line_item')
    op.drop_index('idx_to_line_product', table_name='transfer_order_line_item')
    op.drop_index('idx_to_line_to', table_name='transfer_order_line_item')
    op.drop_table('transfer_order_line_item')

    op.drop_index('idx_to_lane', table_name='transfer_order')
    op.drop_index('idx_to_mrp_run', table_name='transfer_order')
    op.drop_index('idx_to_group', table_name='transfer_order')
    op.drop_index('idx_to_config', table_name='transfer_order')
    op.drop_index('idx_to_shipment_date', table_name='transfer_order')
    op.drop_index('idx_to_status', table_name='transfer_order')
    op.drop_index('idx_to_dest_site', table_name='transfer_order')
    op.drop_index('idx_to_source_site', table_name='transfer_order')
    op.drop_index('idx_to_number', table_name='transfer_order')
    op.drop_table('transfer_order')

    op.drop_index('idx_po_line_number', table_name='purchase_order_line_item')
    op.drop_index('idx_po_line_product', table_name='purchase_order_line_item')
    op.drop_index('idx_po_line_po', table_name='purchase_order_line_item')
    op.drop_table('purchase_order_line_item')

    op.drop_index('idx_po_mrp_run', table_name='purchase_order')
    op.drop_index('idx_po_group', table_name='purchase_order')
    op.drop_index('idx_po_config', table_name='purchase_order')
    op.drop_index('idx_po_order_date', table_name='purchase_order')
    op.drop_index('idx_po_status', table_name='purchase_order')
    op.drop_index('idx_po_dest_site', table_name='purchase_order')
    op.drop_index('idx_po_vendor', table_name='purchase_order')
    op.drop_index('idx_po_number', table_name='purchase_order')
    op.drop_table('purchase_order')

    op.drop_index('idx_mrp_exc_resolved', table_name='mrp_exception')
    op.drop_index('idx_mrp_exc_severity', table_name='mrp_exception')
    op.drop_index('idx_mrp_exc_type', table_name='mrp_exception')
    op.drop_index('idx_mrp_exc_run', table_name='mrp_exception')
    op.drop_table('mrp_exception')

    op.drop_index('idx_mrp_req_period', table_name='mrp_requirement')
    op.drop_index('idx_mrp_req_site', table_name='mrp_requirement')
    op.drop_index('idx_mrp_req_component', table_name='mrp_requirement')
    op.drop_index('idx_mrp_req_run', table_name='mrp_requirement')
    op.drop_table('mrp_requirement')

    op.drop_index('idx_mrp_created_at', table_name='mrp_run')
    op.drop_index('idx_mrp_group', table_name='mrp_run')
    op.drop_index('idx_mrp_status', table_name='mrp_run')
    op.drop_index('idx_mrp_mps_plan', table_name='mrp_run')
    op.drop_index('idx_mrp_run_id', table_name='mrp_run')
    op.drop_table('mrp_run')
