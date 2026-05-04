"""merge_chat_and_templates

Revision ID: 99b1d0fb8f3a
Revises: 20260114_chat, 20260114_sprint4_templates
Create Date: 2026-01-14 14:34:27.092850

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '99b1d0fb8f3a'
down_revision: Union[str, None] = ('20260114_chat', '20260114_sprint4_templates')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
