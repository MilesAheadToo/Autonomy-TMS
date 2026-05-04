"""Merge multiple heads

Revision ID: 1f1a0e541814
Revises: 20260120_inv_proj, 6f38d8e5eecd
Create Date: 2026-01-20 19:13:50.879895

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f1a0e541814'
down_revision: Union[str, None] = ('20260120_inv_proj', '6f38d8e5eecd')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
