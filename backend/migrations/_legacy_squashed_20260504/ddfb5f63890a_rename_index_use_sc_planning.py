"""rename_index_use_sc_planning

Revision ID: ddfb5f63890a
Revises: 20260119_add_mps_permissions
Create Date: 2026-01-19 06:29:19.463895

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ddfb5f63890a'
down_revision: Union[str, None] = '20260119_add_mps_permissions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename the index from ix_games_use_aws_sc_planning to ix_games_use_sc_planning
    op.execute('ALTER INDEX ix_games_use_aws_sc_planning RENAME TO ix_games_use_sc_planning')


def downgrade() -> None:
    # Rename the index back
    op.execute('ALTER INDEX ix_games_use_sc_planning RENAME TO ix_games_use_aws_sc_planning')
