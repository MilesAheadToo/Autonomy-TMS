"""Add backtest_evaluation provisioning step columns

Revision ID: 20260329_backtest_eval
Revises: 86a728afe48f
Create Date: 2026-03-29 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260329_backtest_eval'
down_revision: Union[str, None] = '86a728afe48f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'config_provisioning_status',
        sa.Column('backtest_evaluation_status', sa.String(20), server_default='pending'),
    )
    op.add_column(
        'config_provisioning_status',
        sa.Column('backtest_evaluation_at', sa.DateTime(), nullable=True),
    )
    op.add_column(
        'config_provisioning_status',
        sa.Column('backtest_evaluation_error', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('config_provisioning_status', 'backtest_evaluation_error')
    op.drop_column('config_provisioning_status', 'backtest_evaluation_at')
    op.drop_column('config_provisioning_status', 'backtest_evaluation_status')
