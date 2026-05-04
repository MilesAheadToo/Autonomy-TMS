"""add node_key to players

Revision ID: 20251111090000
Revises: 20250308094500_add_supplier_node_type
Create Date: 2025-11-11 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251111090000_add_node_key_to_players"
down_revision = "20250308094500_add_supplier_node_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("players", sa.Column("node_key", sa.String(length=100), nullable=True))
    op.create_index("ix_players_node_key", "players", ["node_key"])


def downgrade() -> None:
    op.drop_index("ix_players_node_key", table_name="players")
    op.drop_column("players", "node_key")
