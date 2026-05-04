"""Add config_id to sourcing_rules table

Revision ID: 20260110_sourcing_rules_config
Revises: 20260110_inv_policy_config
Create Date: 2026-01-10

Adds config_id column to sourcing_rules table to enable filtering by supply chain configuration.
This is required for the AWS SC planning system to properly query sourcing rules.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '20260110_sourcing_rules_config'
down_revision = '20260110_inv_policy_config'
branch_labels = None
depends_on = None


def upgrade():
    # Add config_id column to sourcing_rules
    op.add_column('sourcing_rules',
                  sa.Column('config_id', sa.Integer(), nullable=True))

    # Add foreign key to supply_chain_configs
    op.create_foreign_key('fk_sourcing_rules_config', 'sourcing_rules',
                         'supply_chain_configs', ['config_id'], ['id'])

    # Add index for faster lookups
    op.create_index('idx_sourcing_rules_config', 'sourcing_rules', ['config_id'])


def downgrade():
    op.drop_index('idx_sourcing_rules_config', 'sourcing_rules')
    op.drop_constraint('fk_sourcing_rules_config', 'sourcing_rules', type_='foreignkey')
    op.drop_column('sourcing_rules', 'config_id')
