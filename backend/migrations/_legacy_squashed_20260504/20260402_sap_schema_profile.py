"""add schema_profile to sap_connections

Revision ID: 20260402_schema_profile
Revises: a7630db18e62
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


# revision identifiers, used by Alembic.
revision: str = '20260402_schema_profile'
down_revision: Union[str, None] = 'a7630db18e62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'sap_connections',
        sa.Column('schema_profile', JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column('sap_connections', 'schema_profile')
