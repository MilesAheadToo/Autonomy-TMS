"""Add persistent geocode_cache table.

Revision ID: 20260314_geocode_cache
Revises: None (standalone)
"""
from alembic import op
import sqlalchemy as sa

revision = "20260314_geocode_cache"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "geocode_cache",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("city", sa.String(200), nullable=False, server_default=""),
        sa.Column("state", sa.String(100), nullable=False, server_default=""),
        sa.Column("country", sa.String(10), nullable=False, server_default=""),
        sa.Column("postal_code", sa.String(20), nullable=False, server_default=""),
        sa.Column("latitude", sa.Float, nullable=True),
        sa.Column("longitude", sa.Float, nullable=True),
        sa.Column("source", sa.String(30), nullable=False, server_default="nominatim"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_geocode_cache_lookup",
        "geocode_cache",
        ["city", "state", "country", "postal_code"],
        unique=True,
    )


def downgrade():
    op.drop_table("geocode_cache")
