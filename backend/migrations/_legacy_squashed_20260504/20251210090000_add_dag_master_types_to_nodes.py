"""Add dag_type and master_type columns to nodes.

Revision ID: 20251210090000
Revises: 20251120093000
Create Date: 2025-12-10 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20251210090000"
down_revision = "20251120093000"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nodes", sa.Column("dag_type", sa.String(length=100), nullable=True))
    op.add_column("nodes", sa.Column("master_type", sa.String(length=100), nullable=True))


def downgrade():
    op.drop_column("nodes", "master_type")
    op.drop_column("nodes", "dag_type")
