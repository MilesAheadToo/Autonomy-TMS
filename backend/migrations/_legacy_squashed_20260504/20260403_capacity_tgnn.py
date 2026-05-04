"""add capacity_tgnn columns to config_provisioning_status

Revision ID: 20260403_capacity_tgnn
Revises: a7630db18e62
Create Date: 2026-04-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260403_capacity_tgnn'
down_revision: Union[str, None] = 'a7630db18e62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('config_provisioning_status',
                  sa.Column('capacity_tgnn_status', sa.String(20), server_default='pending'))
    op.add_column('config_provisioning_status',
                  sa.Column('capacity_tgnn_at', sa.DateTime(), nullable=True))
    op.add_column('config_provisioning_status',
                  sa.Column('capacity_tgnn_error', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('config_provisioning_status', 'capacity_tgnn_error')
    op.drop_column('config_provisioning_status', 'capacity_tgnn_at')
    op.drop_column('config_provisioning_status', 'capacity_tgnn_status')
