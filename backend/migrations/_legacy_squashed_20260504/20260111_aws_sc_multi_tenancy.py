"""Add customer_id to AWS SC planning tables for multi-tenancy

Revision ID: 20260111_aws_sc_multi_tenancy
Revises: 20260110_advanced_features
Create Date: 2026-01-11

This migration enables multi-tenant support for AWS Supply Chain planning entities
by adding customer_id foreign keys to all planning tables. This allows multiple games
and digital twin simulations to share the same supply chain configurations while
maintaining proper data isolation by customer.

Architecture Vision:
- Make The Beer Game a special case of AWS SC Data Model
- Enable digital twin simulations for agent testing
- Support multiple games sharing the same AWS SC configuration
- Maintain data isolation across groups/organizations

Tables Modified:
1. forecast - Demand forecasts
2. supply_plan - Supply plan recommendations (PO/TO/MO)
3. product_bom - Bill of materials
4. production_process - Manufacturing process definitions
5. sourcing_rules - Sourcing rules with priority and allocation
6. inv_policy - Inventory policies
7. reservation - Inventory reservations
8. outbound_order_line - Customer orders
9. vendor_lead_time - Vendor lead times with hierarchical override
10. supply_planning_parameters - Supply planning configuration
11. vendor_product - Vendor-specific product information
12. sourcing_schedule - Sourcing schedule configuration
13. sourcing_schedule_details - Sourcing schedule time details
14. inv_level - Inventory level snapshots
15. trading_partner - Trading partners (vendors/suppliers)

Note: All tables except inv_level and trading_partner already have config_id.
      This migration adds customer_id to all tables for complete multi-tenancy support.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '20260111_aws_sc_multi_tenancy'
down_revision = '20260110_advanced_feat'
branch_labels = None
depends_on = None


def upgrade():
    """Add customer_id and missing config_id columns to AWS SC planning tables"""

    # ========================================================================
    # TABLES THAT ALREADY HAVE config_id - Add customer_id only
    # ========================================================================

    tables_with_config_id = [
        'forecast',
        'supply_plan',
        'product_bom',
        'production_process',
        'sourcing_rules',
        'inv_policy',
        'reservation',
        'outbound_order_line',
        'vendor_lead_time',
        'supply_planning_parameters',
        'vendor_product',
        'sourcing_schedule',
        'sourcing_schedule_details',
    ]

    for table_name in tables_with_config_id:
        # Add customer_id column (nullable initially for backwards compatibility)
        op.add_column(
            table_name,
            sa.Column('customer_id', sa.Integer(), nullable=True)
        )

        # Create foreign key to customers table
        op.create_foreign_key(
            f'fk_{table_name}_group',
            table_name,
            'groups',
            ['customer_id'],
            ['id'],
            ondelete='CASCADE'
        )

        # Create composite index on (customer_id, config_id) for fast lookups
        op.create_index(
            f'idx_{table_name}_group_config',
            table_name,
            ['customer_id', 'config_id']
        )

    # ========================================================================
    # inv_level - Add both customer_id and config_id
    # ========================================================================

    # Add config_id to inv_level
    op.add_column(
        'inv_level',
        sa.Column('config_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_inv_level_config',
        'inv_level',
        'supply_chain_configs',
        ['config_id'],
        ['id']
    )

    # Add customer_id to inv_level
    op.add_column(
        'inv_level',
        sa.Column('customer_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_inv_level_group',
        'inv_level',
        'groups',
        ['customer_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # Create composite index
    op.create_index(
        'idx_inv_level_group_config',
        'inv_level',
        ['customer_id', 'config_id']
    )

    # ========================================================================
    # trading_partner - Add both customer_id and config_id
    # ========================================================================

    # Add config_id to trading_partner
    op.add_column(
        'trading_partner',
        sa.Column('config_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_trading_partner_config',
        'trading_partner',
        'supply_chain_configs',
        ['config_id'],
        ['id']
    )

    # Add customer_id to trading_partner
    op.add_column(
        'trading_partner',
        sa.Column('customer_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_trading_partner_group',
        'trading_partner',
        'groups',
        ['customer_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # Create composite index
    op.create_index(
        'idx_trading_partner_group_config',
        'trading_partner',
        ['customer_id', 'config_id']
    )


def downgrade():
    """Remove customer_id and config_id columns"""

    # ========================================================================
    # Reverse all changes
    # ========================================================================

    tables_with_config_id = [
        'forecast',
        'supply_plan',
        'product_bom',
        'production_process',
        'sourcing_rules',
        'inv_policy',
        'reservation',
        'outbound_order_line',
        'vendor_lead_time',
        'supply_planning_parameters',
        'vendor_product',
        'sourcing_schedule',
        'sourcing_schedule_details',
    ]

    # Remove customer_id from tables that already had config_id
    for table_name in tables_with_config_id:
        op.drop_index(f'idx_{table_name}_group_config', table_name)
        op.drop_constraint(f'fk_{table_name}_group', table_name, type_='foreignkey')
        op.drop_column(table_name, 'customer_id')

    # Remove from inv_level
    op.drop_index('idx_inv_level_group_config', 'inv_level')
    op.drop_constraint('fk_inv_level_group', 'inv_level', type_='foreignkey')
    op.drop_column('inv_level', 'customer_id')
    op.drop_constraint('fk_inv_level_config', 'inv_level', type_='foreignkey')
    op.drop_column('inv_level', 'config_id')

    # Remove from trading_partner
    op.drop_index('idx_trading_partner_group_config', 'trading_partner')
    op.drop_constraint('fk_trading_partner_group', 'trading_partner', type_='foreignkey')
    op.drop_column('trading_partner', 'customer_id')
    op.drop_constraint('fk_trading_partner_config', 'trading_partner', type_='foreignkey')
    op.drop_column('trading_partner', 'config_id')
