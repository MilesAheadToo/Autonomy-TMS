"""SC data model extensions for 15 unprocessed SAP tables

Creates 9 new extension tables and adds 13 columns to 4 existing tables
to support SAP tables: VBEP, MARM, CRHD, MBEW, KAKO, VBUK, VBUP,
CDHDR, CDPOS, PLAF, AFKO/AFVC, EKET.

Revision ID: 20260327_sc_dm_ext
Revises: a7630db18e62
Create Date: 2026-03-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260327_sc_dm_ext'
down_revision: Union[str, None] = 'a7630db18e62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. outbound_order_line_schedule (SAP VBEP)
    # ------------------------------------------------------------------
    op.create_table(
        'outbound_order_line_schedule',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_id', sa.String(100), sa.ForeignKey('outbound_order.id', ondelete='CASCADE'), nullable=False),
        sa.Column('line_number', sa.Integer(), nullable=False),
        sa.Column('schedule_number', sa.Integer(), nullable=False),
        sa.Column('requested_date', sa.DateTime(), nullable=True),
        sa.Column('ordered_qty', sa.Float(), nullable=True),
        sa.Column('confirmed_qty', sa.Float(), nullable=True),
        sa.Column('shipped_qty', sa.Float(), nullable=True),
        sa.Column('uom', sa.String(20), nullable=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_obl_sched_order_line', 'outbound_order_line_schedule', ['order_id', 'line_number'])
    op.create_index('idx_obl_sched_date', 'outbound_order_line_schedule', ['requested_date'])
    op.create_index('idx_obl_sched_config', 'outbound_order_line_schedule', ['config_id'])

    # ------------------------------------------------------------------
    # 2. product_uom_conversion (SAP MARM)
    # ------------------------------------------------------------------
    op.create_table(
        'product_uom_conversion',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('product_id', sa.String(100), sa.ForeignKey('product.id', ondelete='CASCADE'), nullable=False),
        sa.Column('alternate_uom', sa.String(20), nullable=False),
        sa.Column('numerator', sa.Integer(), nullable=True),
        sa.Column('denominator', sa.Integer(), nullable=True),
        sa.Column('gross_weight', sa.Float(), nullable=True),
        sa.Column('volume', sa.Float(), nullable=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('product_id', 'alternate_uom', 'config_id', name='uq_product_uom_config'),
    )
    op.create_index('idx_product_uom_product', 'product_uom_conversion', ['product_id'])
    op.create_index('idx_product_uom_config', 'product_uom_conversion', ['config_id'])

    # ------------------------------------------------------------------
    # 3. work_center_master (SAP CRHD)
    # ------------------------------------------------------------------
    op.create_table(
        'work_center_master',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('work_center_code', sa.String(100), nullable=False),
        sa.Column('name', sa.String(200), nullable=True),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('site.id', ondelete='CASCADE'), nullable=True),
        sa.Column('usage_category', sa.String(50), nullable=True),
        sa.Column('object_type', sa.String(50), nullable=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('work_center_code', 'config_id', name='uq_wc_code_config'),
    )
    op.create_index('idx_wc_site', 'work_center_master', ['site_id'])
    op.create_index('idx_wc_config', 'work_center_master', ['config_id'])

    # ------------------------------------------------------------------
    # 4. material_valuation (SAP MBEW)
    # ------------------------------------------------------------------
    op.create_table(
        'material_valuation',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('product_id', sa.String(100), sa.ForeignKey('product.id', ondelete='CASCADE'), nullable=False),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('site.id', ondelete='CASCADE'), nullable=True),
        sa.Column('valuation_class', sa.String(50), nullable=True),
        sa.Column('price_control', sa.String(10), nullable=True),
        sa.Column('standard_price', sa.Float(), nullable=True),
        sa.Column('moving_avg_price', sa.Float(), nullable=True),
        sa.Column('price_unit', sa.Integer(), nullable=True),
        sa.Column('cumulative_quantity', sa.Float(), nullable=True),
        sa.Column('total_value', sa.Float(), nullable=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('product_id', 'site_id', 'config_id', name='uq_mat_val_product_site_config'),
    )
    op.create_index('idx_mat_val_product', 'material_valuation', ['product_id'])
    op.create_index('idx_mat_val_site', 'material_valuation', ['site_id'])
    op.create_index('idx_mat_val_config', 'material_valuation', ['config_id'])

    # ------------------------------------------------------------------
    # 5. capacity_resource_detail (SAP KAKO)
    # ------------------------------------------------------------------
    op.create_table(
        'capacity_resource_detail',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('capacity_id', sa.String(100), nullable=False),
        sa.Column('name', sa.String(200), nullable=True),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('site.id', ondelete='CASCADE'), nullable=True),
        sa.Column('work_center_id', sa.Integer(), sa.ForeignKey('work_center_master.id', ondelete='SET NULL'), nullable=True),
        sa.Column('max_parallel_ops', sa.Integer(), nullable=True),
        sa.Column('standard_parallel_ops', sa.Integer(), nullable=True),
        sa.Column('base_net_time', sa.Float(), nullable=True),
        sa.Column('uom', sa.String(20), nullable=True),
        sa.Column('planner_id', sa.String(50), nullable=True),
        sa.Column('sap_params', sa.JSON(), nullable=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('capacity_id', 'config_id', name='uq_cap_res_detail_config'),
    )
    op.create_index('idx_cap_res_detail_site', 'capacity_resource_detail', ['site_id'])
    op.create_index('idx_cap_res_detail_wc', 'capacity_resource_detail', ['work_center_id'])
    op.create_index('idx_cap_res_detail_config', 'capacity_resource_detail', ['config_id'])

    # ------------------------------------------------------------------
    # 6. outbound_order_status (SAP VBUK)
    # ------------------------------------------------------------------
    op.create_table(
        'outbound_order_status',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_id', sa.String(100), sa.ForeignKey('outbound_order.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('delivery_status', sa.String(10), nullable=True),
        sa.Column('billing_status', sa.String(10), nullable=True),
        sa.Column('invoice_status', sa.String(10), nullable=True),
        sa.Column('goods_issue_status', sa.String(10), nullable=True),
        sa.Column('rejection_status', sa.String(10), nullable=True),
        sa.Column('return_status', sa.String(10), nullable=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_ob_status_config', 'outbound_order_status', ['config_id'])

    # ------------------------------------------------------------------
    # 7. outbound_order_line_status (SAP VBUP)
    # ------------------------------------------------------------------
    op.create_table(
        'outbound_order_line_status',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_id', sa.String(100), sa.ForeignKey('outbound_order.id', ondelete='CASCADE'), nullable=False),
        sa.Column('line_number', sa.Integer(), nullable=False),
        sa.Column('delivery_status', sa.String(10), nullable=True),
        sa.Column('billing_status', sa.String(10), nullable=True),
        sa.Column('invoice_status', sa.String(10), nullable=True),
        sa.Column('goods_issue_status', sa.String(10), nullable=True),
        sa.Column('rejection_status', sa.String(10), nullable=True),
        sa.Column('return_status', sa.String(10), nullable=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_id', 'line_number', name='uq_obl_status_order_line'),
    )
    op.create_index('idx_obl_status_order', 'outbound_order_line_status', ['order_id'])
    op.create_index('idx_obl_status_config', 'outbound_order_line_status', ['config_id'])

    # ------------------------------------------------------------------
    # 8. sap_change_log (SAP CDHDR)
    # ------------------------------------------------------------------
    op.create_table(
        'sap_change_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('change_number', sa.String(100), nullable=False, unique=True),
        sa.Column('object_class', sa.String(50), nullable=True),
        sa.Column('object_id', sa.String(100), nullable=True),
        sa.Column('changed_by', sa.String(100), nullable=True),
        sa.Column('changed_at', sa.DateTime(), nullable=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_sap_clog_object', 'sap_change_log', ['object_class', 'object_id'])
    op.create_index('idx_sap_clog_changed_at', 'sap_change_log', ['changed_at'])
    op.create_index('idx_sap_clog_tenant', 'sap_change_log', ['tenant_id'])

    # ------------------------------------------------------------------
    # 9. sap_change_log_detail (SAP CDPOS)
    # ------------------------------------------------------------------
    op.create_table(
        'sap_change_log_detail',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('change_log_id', sa.Integer(), sa.ForeignKey('sap_change_log.id', ondelete='CASCADE'), nullable=False),
        sa.Column('table_name', sa.String(100), nullable=True),
        sa.Column('field_name', sa.String(100), nullable=True),
        sa.Column('old_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_sap_clog_detail_parent', 'sap_change_log_detail', ['change_log_id'])
    op.create_index('idx_sap_clog_detail_table', 'sap_change_log_detail', ['table_name', 'field_name'])

    # ------------------------------------------------------------------
    # 10. Column additions: supply_plan (SAP PLAF fields)
    # ------------------------------------------------------------------
    op.add_column('supply_plan', sa.Column('sap_planned_order_id', sa.String(50), nullable=True))
    op.add_column('supply_plan', sa.Column('uom', sa.String(20), nullable=True))
    op.add_column('supply_plan', sa.Column('planning_strategy', sa.String(50), nullable=True))
    op.add_column('supply_plan', sa.Column('routing_type', sa.String(50), nullable=True))

    # ------------------------------------------------------------------
    # 11. Column additions: production_orders (SAP AFKO/AUFK fields)
    # ------------------------------------------------------------------
    op.add_column('production_orders', sa.Column('order_type', sa.String(50), nullable=True))
    op.add_column('production_orders', sa.Column('logical_type', sa.String(50), nullable=True))
    op.add_column('production_orders', sa.Column('currency', sa.String(3), nullable=True))
    op.add_column('production_orders', sa.Column('sap_objnr', sa.String(50), nullable=True))

    # ------------------------------------------------------------------
    # 12. Column additions: process_operation (SAP AFVC fields)
    # ------------------------------------------------------------------
    op.add_column('process_operation', sa.Column('cost_center_id', sa.String(50), nullable=True))
    op.add_column('process_operation', sa.Column('activity_type', sa.String(50), nullable=True))
    op.add_column('process_operation', sa.Column('cost_rate', sa.Float(), nullable=True))
    op.add_column('process_operation', sa.Column('currency', sa.String(3), nullable=True))

    # ------------------------------------------------------------------
    # 13. Column additions: inbound_order_line_schedule (SAP EKET fields)
    # ------------------------------------------------------------------
    op.add_column('inbound_order_line_schedule', sa.Column('movement_type', sa.String(10), nullable=True))
    op.add_column('inbound_order_line_schedule', sa.Column('valuation_type', sa.String(20), nullable=True))
    op.add_column('inbound_order_line_schedule', sa.Column('amount', sa.Float(), nullable=True))


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Remove column additions (reverse order)
    # ------------------------------------------------------------------
    # 13. inbound_order_line_schedule
    op.drop_column('inbound_order_line_schedule', 'amount')
    op.drop_column('inbound_order_line_schedule', 'valuation_type')
    op.drop_column('inbound_order_line_schedule', 'movement_type')

    # 12. process_operation
    op.drop_column('process_operation', 'currency')
    op.drop_column('process_operation', 'cost_rate')
    op.drop_column('process_operation', 'activity_type')
    op.drop_column('process_operation', 'cost_center_id')

    # 11. production_orders
    op.drop_column('production_orders', 'sap_objnr')
    op.drop_column('production_orders', 'currency')
    op.drop_column('production_orders', 'logical_type')
    op.drop_column('production_orders', 'order_type')

    # 10. supply_plan
    op.drop_column('supply_plan', 'routing_type')
    op.drop_column('supply_plan', 'planning_strategy')
    op.drop_column('supply_plan', 'uom')
    op.drop_column('supply_plan', 'sap_planned_order_id')

    # ------------------------------------------------------------------
    # Drop new tables (reverse dependency order)
    # ------------------------------------------------------------------
    op.drop_table('sap_change_log_detail')
    op.drop_table('sap_change_log')
    op.drop_table('outbound_order_line_status')
    op.drop_table('outbound_order_status')
    op.drop_table('capacity_resource_detail')
    op.drop_table('material_valuation')
    op.drop_table('work_center_master')
    op.drop_table('product_uom_conversion')
    op.drop_table('outbound_order_line_schedule')
