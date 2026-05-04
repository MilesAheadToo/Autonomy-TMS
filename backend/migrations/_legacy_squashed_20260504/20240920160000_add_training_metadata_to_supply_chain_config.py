"""Add training metadata columns to supply chain configs

Revision ID: 20240920160000
Revises: 20240915120000
Create Date: 2025-09-20 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20240920160000'
down_revision = '20240915120000'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('supply_chain_configs', sa.Column('needs_training', sa.Boolean(), nullable=False, server_default=sa.text("TRUE")))
    op.add_column('supply_chain_configs', sa.Column('training_status', sa.String(length=50), nullable=False, server_default='pending'))
    op.add_column('supply_chain_configs', sa.Column('trained_at', sa.DateTime(), nullable=True))
    op.add_column('supply_chain_configs', sa.Column('trained_model_path', sa.String(length=255), nullable=True))
    op.add_column('supply_chain_configs', sa.Column('last_trained_config_hash', sa.String(length=128), nullable=True))

    # Remove server defaults now that existing rows have been initialised
    op.alter_column('supply_chain_configs', 'needs_training', server_default=None)
    op.alter_column('supply_chain_configs', 'training_status', server_default=None)


def downgrade():
    op.drop_column('supply_chain_configs', 'last_trained_config_hash')
    op.drop_column('supply_chain_configs', 'trained_model_path')
    op.drop_column('supply_chain_configs', 'trained_at')
    op.drop_column('supply_chain_configs', 'training_status')
    op.drop_column('supply_chain_configs', 'needs_training')
