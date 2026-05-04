"""Add layer_license table for modular cascade selling

Revision ID: 20260209_layer_license
Revises: None (standalone)
Create Date: 2026-02-09
"""
from alembic import op
import sqlalchemy as sa

revision = '20260209_layer_license'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'layer_license',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id'), nullable=False),
        sa.Column('layer', sa.Enum('sop', 'mps', 'supply_agent', 'allocation_agent', 'execution',
                                    name='layername'), nullable=False),
        sa.Column('mode', sa.Enum('active', 'input', 'disabled', name='layermode'),
                  nullable=False, server_default='input'),
        sa.Column('activated_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('package_tier', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
    )
    op.create_index('ix_layer_license_group_layer', 'layer_license',
                     ['customer_id', 'layer'], unique=True)
    op.create_index('ix_layer_license_group_id', 'layer_license', ['customer_id'])


def downgrade():
    op.drop_index('ix_layer_license_group_layer', table_name='layer_license')
    op.drop_index('ix_layer_license_group_id', table_name='layer_license')
    op.drop_table('layer_license')
    op.execute("DROP TYPE IF EXISTS layername")
    op.execute("DROP TYPE IF EXISTS layermode")
