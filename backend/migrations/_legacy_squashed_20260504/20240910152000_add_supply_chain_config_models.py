"""Add supply chain configuration models

Revision ID: 20240910152000
Revises: 
Create Date: 2025-09-10 15:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20240910152000'
down_revision = '20240900120000'
branch_labels = None
depends_on = None


def upgrade():
    # Create supply_chain_configs table
    op.create_table(
        'supply_chain_configs',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP'), server_onupdate=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create items table
    op.create_table(
        'items',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('unit_cost_range', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', 'config_id', name='_item_name_config_uc')
    )
    
    # Create nodes table
    op.create_table(
        'nodes',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('type', sa.Enum('RETAILER', 'WHOLESALER', 'DISTRIBUTOR', 'MANUFACTURER', name='nodetype'), nullable=False),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', 'config_id', name='_node_name_config_uc')
    )
    
    # Create lanes table
    op.create_table(
        'lanes',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('upstream_node_id', sa.Integer(), nullable=False),
        sa.Column('downstream_node_id', sa.Integer(), nullable=False),
        sa.Column('capacity', sa.Integer(), nullable=False),
        sa.Column('lead_time_days', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['upstream_node_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['downstream_node_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('upstream_node_id', 'downstream_node_id', 'config_id', name='_node_connection_uc')
    )
    
    # Create item_node_configs table
    op.create_table(
        'item_node_configs',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('node_id', sa.Integer(), nullable=False),
        sa.Column('inventory_target_range', sa.JSON(), nullable=True),
        sa.Column('initial_inventory_range', sa.JSON(), nullable=True),
        sa.Column('holding_cost_range', sa.JSON(), nullable=True),
        sa.Column('backlog_cost_range', sa.JSON(), nullable=True),
        sa.Column('selling_price_range', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('item_id', 'node_id', name='_item_node_uc')
    )
    
    # Create market_demands table
    op.create_table(
        'market_demands',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('retailer_id', sa.Integer(), nullable=False),
        sa.Column('demand_pattern', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['retailer_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('item_id', 'retailer_id', 'config_id', name='_market_demand_uc')
    )


def downgrade():
    op.drop_table('market_demands')
    op.drop_table('item_node_configs')
    op.drop_table('lanes')
    op.drop_table('nodes')
    op.drop_table('items')
    op.drop_table('supply_chain_configs')
    op.execute('DROP TYPE IF EXISTS nodetype')
