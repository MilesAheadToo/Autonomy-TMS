"""Add config_id to inv_policy table

Revision ID: 20260110_inv_policy_config
Revises: 20260110_planning
Create Date: 2026-01-10

Adds config_id column to inv_policy table to enable filtering by supply chain configuration.
This is required for the AWS SC planning system to properly query inventory policies.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '20260110_inv_policy_config'
down_revision = '20260110_planning'
branch_labels = None
depends_on = None


def upgrade():
    # Add config_id column to inv_policy
    op.add_column('inv_policy',
                  sa.Column('config_id', sa.Integer(), nullable=True))

    # Add foreign key to supply_chain_configs
    op.create_foreign_key('fk_inv_policy_config', 'inv_policy',
                         'supply_chain_configs', ['config_id'], ['id'])

    # Add index for faster lookups
    op.create_index('idx_inv_policy_config', 'inv_policy', ['config_id'])


def downgrade():
    op.drop_index('idx_inv_policy_config', 'inv_policy')
    op.drop_constraint('fk_inv_policy_config', 'inv_policy', type_='foreignkey')
    op.drop_column('inv_policy', 'config_id')
