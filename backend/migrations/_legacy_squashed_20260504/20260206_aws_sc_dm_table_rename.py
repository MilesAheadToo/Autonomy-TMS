"""AWS SC DM: Rename nodes->site, lanes->transportation_lane

This migration renames the legacy Beer Game tables to AWS Supply Chain Data Model
compliant names:
- nodes -> site
- lanes -> transportation_lane

Revision ID: 20260206_aws_sc_dm
Revises: 20260204_add_product_hierarchy_fields
Create Date: 2026-02-06
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260206_aws_sc_dm'
down_revision = '20260204_add_product_hierarchy_fields'
branch_labels = None
depends_on = None


def upgrade():
    """Rename nodes to site and lanes to transportation_lane for AWS SC DM compliance."""

    # Drop existing AWS SC DM tables (empty/test data only)
    op.execute("DROP TABLE IF EXISTS transportation_lane CASCADE")
    op.execute("DROP TABLE IF EXISTS site CASCADE")

    # Get all FK constraints referencing 'nodes' table
    # We need to drop them, rename the table, and recreate them

    # =========================================================================
    # Step 1: Drop all FK constraints referencing 'nodes'
    # =========================================================================
    fk_constraints_nodes = [
        ('aggregated_order', 'aggregated_order_to_site_id_fkey'),
        ('aggregated_order', 'aggregated_order_from_site_id_fkey'),
        ('atp_projection', 'atp_projection_site_id_fkey'),
        ('capacity_resources', 'capacity_resources_site_id_fkey'),
        ('ctp_projection', 'ctp_projection_site_id_fkey'),
        ('inbound_order_line', 'inbound_order_line_from_site_id_fkey'),
        ('inbound_order_line', 'inbound_order_line_to_site_id_fkey'),
        ('inv_projection', 'inv_projection_site_id_fkey'),
        ('item_node_configs', 'item_node_configs_site_id_fkey'),
        ('item_node_suppliers', 'item_node_suppliers_supplier_site_id_fkey'),
        ('lanes', 'lanes_from_site_id_fkey'),
        ('lanes', 'lanes_to_site_id_fkey'),
        ('mps_capacity_checks', 'mps_capacity_checks_site_id_fkey'),
        ('mps_key_material_requirements', 'mps_key_material_requirements_key_material_site_id_fkey'),
        ('mps_plan_items', 'mps_plan_items_site_id_fkey'),
        ('mrp_exception', 'mrp_exception_site_id_fkey'),
        ('mrp_requirement', 'mrp_requirement_source_site_id_fkey'),
        ('mrp_requirement', 'mrp_requirement_site_id_fkey'),
        ('order_aggregation_policy', 'order_aggregation_policy_to_site_id_fkey'),
        ('order_aggregation_policy', 'order_aggregation_policy_from_site_id_fkey'),
        ('order_promise', 'order_promise_site_id_fkey'),
        ('outbound_order_line', 'outbound_order_line_site_id_fkey'),
        ('production_capacity', 'production_capacity_site_id_fkey'),
        ('purchase_order', 'purchase_order_destination_site_id_fkey'),
        ('purchase_order', 'purchase_order_supplier_site_id_fkey'),
        ('reservation', 'reservation_site_id_fkey'),
        ('sourcing_schedule', 'sourcing_schedule_from_site_id_fkey'),
        ('sourcing_schedule', 'sourcing_schedule_to_site_id_fkey'),
        ('transfer_order', 'transfer_order_destination_site_id_fkey'),
        ('transfer_order', 'transfer_order_source_site_id_fkey'),
        ('vendor_lead_times', 'vendor_lead_times_site_id_fkey'),
    ]

    for table_name, constraint_name in fk_constraints_nodes:
        op.execute(f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {constraint_name}")

    # =========================================================================
    # Step 2: Drop unique constraint on lanes table before rename
    # =========================================================================
    op.execute("ALTER TABLE lanes DROP CONSTRAINT IF EXISTS _site_connection_uc")

    # =========================================================================
    # Step 3: Rename tables
    # =========================================================================
    op.rename_table('nodes', 'site')
    op.rename_table('lanes', 'transportation_lane')

    # =========================================================================
    # Step 4: Recreate FK constraints pointing to 'site'
    # =========================================================================
    # Note: The lanes table is now transportation_lane, so we need to update that reference
    fk_constraints_new = [
        ('aggregated_order', 'to_site_id', 'site', 'id', 'aggregated_order_to_site_id_fkey'),
        ('aggregated_order', 'from_site_id', 'site', 'id', 'aggregated_order_from_site_id_fkey'),
        ('atp_projection', 'site_id', 'site', 'id', 'atp_projection_site_id_fkey'),
        ('capacity_resources', 'site_id', 'site', 'id', 'capacity_resources_site_id_fkey'),
        ('ctp_projection', 'site_id', 'site', 'id', 'ctp_projection_site_id_fkey'),
        ('inbound_order_line', 'from_site_id', 'site', 'id', 'inbound_order_line_from_site_id_fkey'),
        ('inbound_order_line', 'to_site_id', 'site', 'id', 'inbound_order_line_to_site_id_fkey'),
        ('inv_projection', 'site_id', 'site', 'id', 'inv_projection_site_id_fkey'),
        ('item_node_configs', 'site_id', 'site', 'id', 'item_node_configs_site_id_fkey'),
        ('item_node_suppliers', 'supplier_site_id', 'site', 'id', 'item_node_suppliers_supplier_site_id_fkey'),
        ('transportation_lane', 'from_site_id', 'site', 'id', 'transportation_lane_from_site_id_fkey'),
        ('transportation_lane', 'to_site_id', 'site', 'id', 'transportation_lane_to_site_id_fkey'),
        ('mps_capacity_checks', 'site_id', 'site', 'id', 'mps_capacity_checks_site_id_fkey'),
        ('mps_key_material_requirements', 'key_material_site_id', 'site', 'id', 'mps_key_material_requirements_key_material_site_id_fkey'),
        ('mps_plan_items', 'site_id', 'site', 'id', 'mps_plan_items_site_id_fkey'),
        ('mrp_exception', 'site_id', 'site', 'id', 'mrp_exception_site_id_fkey'),
        ('mrp_requirement', 'source_site_id', 'site', 'id', 'mrp_requirement_source_site_id_fkey'),
        ('mrp_requirement', 'site_id', 'site', 'id', 'mrp_requirement_site_id_fkey'),
        ('order_aggregation_policy', 'to_site_id', 'site', 'id', 'order_aggregation_policy_to_site_id_fkey'),
        ('order_aggregation_policy', 'from_site_id', 'site', 'id', 'order_aggregation_policy_from_site_id_fkey'),
        ('order_promise', 'site_id', 'site', 'id', 'order_promise_site_id_fkey'),
        ('outbound_order_line', 'site_id', 'site', 'id', 'outbound_order_line_site_id_fkey'),
        ('production_capacity', 'site_id', 'site', 'id', 'production_capacity_site_id_fkey'),
        ('purchase_order', 'destination_site_id', 'site', 'id', 'purchase_order_destination_site_id_fkey'),
        ('purchase_order', 'supplier_site_id', 'site', 'id', 'purchase_order_supplier_site_id_fkey'),
        ('reservation', 'site_id', 'site', 'id', 'reservation_site_id_fkey'),
        ('sourcing_schedule', 'from_site_id', 'site', 'id', 'sourcing_schedule_from_site_id_fkey'),
        ('sourcing_schedule', 'to_site_id', 'site', 'id', 'sourcing_schedule_to_site_id_fkey'),
        ('transfer_order', 'destination_site_id', 'site', 'id', 'transfer_order_destination_site_id_fkey'),
        ('transfer_order', 'source_site_id', 'site', 'id', 'transfer_order_source_site_id_fkey'),
        ('vendor_lead_times', 'site_id', 'site', 'id', 'vendor_lead_times_site_id_fkey'),
    ]

    for table_name, column_name, ref_table, ref_column, constraint_name in fk_constraints_new:
        op.create_foreign_key(
            constraint_name,
            table_name,
            ref_table,
            [column_name],
            [ref_column]
        )

    # =========================================================================
    # Step 5: Recreate unique constraint on transportation_lane
    # =========================================================================
    op.create_unique_constraint(
        '_site_connection_uc',
        'transportation_lane',
        ['from_site_id', 'to_site_id']
    )


def downgrade():
    """Revert: Rename site back to nodes and transportation_lane back to lanes."""

    # Drop FK constraints
    fk_constraints = [
        ('aggregated_order', 'aggregated_order_to_site_id_fkey'),
        ('aggregated_order', 'aggregated_order_from_site_id_fkey'),
        ('atp_projection', 'atp_projection_site_id_fkey'),
        ('capacity_resources', 'capacity_resources_site_id_fkey'),
        ('ctp_projection', 'ctp_projection_site_id_fkey'),
        ('inbound_order_line', 'inbound_order_line_from_site_id_fkey'),
        ('inbound_order_line', 'inbound_order_line_to_site_id_fkey'),
        ('inv_projection', 'inv_projection_site_id_fkey'),
        ('item_node_configs', 'item_node_configs_site_id_fkey'),
        ('item_node_suppliers', 'item_node_suppliers_supplier_site_id_fkey'),
        ('transportation_lane', 'transportation_lane_from_site_id_fkey'),
        ('transportation_lane', 'transportation_lane_to_site_id_fkey'),
        ('mps_capacity_checks', 'mps_capacity_checks_site_id_fkey'),
        ('mps_key_material_requirements', 'mps_key_material_requirements_key_material_site_id_fkey'),
        ('mps_plan_items', 'mps_plan_items_site_id_fkey'),
        ('mrp_exception', 'mrp_exception_site_id_fkey'),
        ('mrp_requirement', 'mrp_requirement_source_site_id_fkey'),
        ('mrp_requirement', 'mrp_requirement_site_id_fkey'),
        ('order_aggregation_policy', 'order_aggregation_policy_to_site_id_fkey'),
        ('order_aggregation_policy', 'order_aggregation_policy_from_site_id_fkey'),
        ('order_promise', 'order_promise_site_id_fkey'),
        ('outbound_order_line', 'outbound_order_line_site_id_fkey'),
        ('production_capacity', 'production_capacity_site_id_fkey'),
        ('purchase_order', 'purchase_order_destination_site_id_fkey'),
        ('purchase_order', 'purchase_order_supplier_site_id_fkey'),
        ('reservation', 'reservation_site_id_fkey'),
        ('sourcing_schedule', 'sourcing_schedule_from_site_id_fkey'),
        ('sourcing_schedule', 'sourcing_schedule_to_site_id_fkey'),
        ('transfer_order', 'transfer_order_destination_site_id_fkey'),
        ('transfer_order', 'transfer_order_source_site_id_fkey'),
        ('vendor_lead_times', 'vendor_lead_times_site_id_fkey'),
    ]

    for table_name, constraint_name in fk_constraints:
        op.execute(f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {constraint_name}")

    # Drop unique constraint
    op.execute("ALTER TABLE transportation_lane DROP CONSTRAINT IF EXISTS _site_connection_uc")

    # Rename tables back
    op.rename_table('site', 'nodes')
    op.rename_table('transportation_lane', 'lanes')

    # Recreate original FK constraints
    fk_constraints_original = [
        ('aggregated_order', 'to_site_id', 'nodes', 'id', 'aggregated_order_to_site_id_fkey'),
        ('aggregated_order', 'from_site_id', 'nodes', 'id', 'aggregated_order_from_site_id_fkey'),
        ('atp_projection', 'site_id', 'nodes', 'id', 'atp_projection_site_id_fkey'),
        ('capacity_resources', 'site_id', 'nodes', 'id', 'capacity_resources_site_id_fkey'),
        ('ctp_projection', 'site_id', 'nodes', 'id', 'ctp_projection_site_id_fkey'),
        ('inbound_order_line', 'from_site_id', 'nodes', 'id', 'inbound_order_line_from_site_id_fkey'),
        ('inbound_order_line', 'to_site_id', 'nodes', 'id', 'inbound_order_line_to_site_id_fkey'),
        ('inv_projection', 'site_id', 'nodes', 'id', 'inv_projection_site_id_fkey'),
        ('item_node_configs', 'site_id', 'nodes', 'id', 'item_node_configs_site_id_fkey'),
        ('item_node_suppliers', 'supplier_site_id', 'nodes', 'id', 'item_node_suppliers_supplier_site_id_fkey'),
        ('lanes', 'from_site_id', 'nodes', 'id', 'lanes_from_site_id_fkey'),
        ('lanes', 'to_site_id', 'nodes', 'id', 'lanes_to_site_id_fkey'),
        ('mps_capacity_checks', 'site_id', 'nodes', 'id', 'mps_capacity_checks_site_id_fkey'),
        ('mps_key_material_requirements', 'key_material_site_id', 'nodes', 'id', 'mps_key_material_requirements_key_material_site_id_fkey'),
        ('mps_plan_items', 'site_id', 'nodes', 'id', 'mps_plan_items_site_id_fkey'),
        ('mrp_exception', 'site_id', 'nodes', 'id', 'mrp_exception_site_id_fkey'),
        ('mrp_requirement', 'source_site_id', 'nodes', 'id', 'mrp_requirement_source_site_id_fkey'),
        ('mrp_requirement', 'site_id', 'nodes', 'id', 'mrp_requirement_site_id_fkey'),
        ('order_aggregation_policy', 'to_site_id', 'nodes', 'id', 'order_aggregation_policy_to_site_id_fkey'),
        ('order_aggregation_policy', 'from_site_id', 'nodes', 'id', 'order_aggregation_policy_from_site_id_fkey'),
        ('order_promise', 'site_id', 'nodes', 'id', 'order_promise_site_id_fkey'),
        ('outbound_order_line', 'site_id', 'nodes', 'id', 'outbound_order_line_site_id_fkey'),
        ('production_capacity', 'site_id', 'nodes', 'id', 'production_capacity_site_id_fkey'),
        ('purchase_order', 'destination_site_id', 'nodes', 'id', 'purchase_order_destination_site_id_fkey'),
        ('purchase_order', 'supplier_site_id', 'nodes', 'id', 'purchase_order_supplier_site_id_fkey'),
        ('reservation', 'site_id', 'nodes', 'id', 'reservation_site_id_fkey'),
        ('sourcing_schedule', 'from_site_id', 'nodes', 'id', 'sourcing_schedule_from_site_id_fkey'),
        ('sourcing_schedule', 'to_site_id', 'nodes', 'id', 'sourcing_schedule_to_site_id_fkey'),
        ('transfer_order', 'destination_site_id', 'nodes', 'id', 'transfer_order_destination_site_id_fkey'),
        ('transfer_order', 'source_site_id', 'nodes', 'id', 'transfer_order_source_site_id_fkey'),
        ('vendor_lead_times', 'site_id', 'nodes', 'id', 'vendor_lead_times_site_id_fkey'),
    ]

    for table_name, column_name, ref_table, ref_column, constraint_name in fk_constraints_original:
        op.create_foreign_key(
            constraint_name,
            table_name,
            ref_table,
            [column_name],
            [ref_column]
        )

    # Recreate unique constraint
    op.create_unique_constraint(
        '_site_connection_uc',
        'lanes',
        ['from_site_id', 'to_site_id']
    )
