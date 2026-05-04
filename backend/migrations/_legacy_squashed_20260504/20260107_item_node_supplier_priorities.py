"""Add ItemNodeSupplier table and remove Lane priority

Revision ID: 20260107_item_node_supplier
Revises: 20260107_add_lane_priority
Create Date: 2026-01-07 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20260107_item_node_supplier'
down_revision = '20260322093000'
branch_labels = None
depends_on = None


def upgrade():
    # Create item_node_suppliers table
    op.create_table(
        'item_node_suppliers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('item_node_config_id', sa.Integer(), nullable=False),
        sa.Column('supplier_node_id', sa.Integer(), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(['item_node_config_id'], ['item_node_configs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['supplier_node_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('item_node_config_id', 'supplier_node_id', name='_item_node_supplier_uc')
    )
    op.create_index(op.f('ix_item_node_suppliers_id'), 'item_node_suppliers', ['id'], unique=False)

    # Remove priority column from lanes table if it exists (it may have been added in previous session)
    # Use batch mode to handle if column doesn't exist
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = [col['name'] for col in inspector.get_columns('lanes')]
    if 'priority' in columns:
        op.drop_column('lanes', 'priority')


def downgrade():
    # Add back priority column to lanes
    op.add_column('lanes', sa.Column('priority', sa.Integer(), nullable=False, server_default=sa.text("0")))

    # Drop item_node_suppliers table
    op.drop_index(op.f('ix_item_node_suppliers_id'), table_name='item_node_suppliers')
    op.drop_table('item_node_suppliers')
