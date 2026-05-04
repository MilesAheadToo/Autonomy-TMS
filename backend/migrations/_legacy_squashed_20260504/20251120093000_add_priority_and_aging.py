"""Add priority and order aging fields

Revision ID: 20251120093000
Revises: 20251113094500
Create Date: 2025-11-20 09:30:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20251120093000"
down_revision = "20251113094500"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nodes", sa.Column("priority", sa.Integer(), nullable=True))
    op.add_column("nodes", sa.Column("order_aging", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("nodes", sa.Column("lost_sale_cost", sa.Float(), nullable=True))
    op.add_column("items", sa.Column("priority", sa.Integer(), nullable=True))
    op.execute("UPDATE nodes SET order_aging = 0 WHERE order_aging IS NULL")


def downgrade():
    op.drop_column("items", "priority")
    op.drop_column("nodes", "lost_sale_cost")
    op.drop_column("nodes", "order_aging")
    op.drop_column("nodes", "priority")
