"""merge_hierarchy_and_dm_extensions

Revision ID: 86a728afe48f
Revises: 20260326_hierarchy, 20260327_sc_dm_ext
Create Date: 2026-03-28 13:25:34.202995

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '86a728afe48f'
down_revision: Union[str, None] = ('20260326_hierarchy', '20260327_sc_dm_ext')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
