"""Add display_identifiers column to tenant_bsc_config.

Controls whether the UI shows human-readable names or raw IDs for
products, sites, and other entities.  Default 'name' for demo-friendly
display; 'id' for experienced planners who prefer SKU codes.

Revision ID: 20260318_dispid
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa

revision = "20260318_dispid"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tenant_bsc_config",
        sa.Column(
            "display_identifiers",
            sa.String(10),
            nullable=False,
            server_default="name",
        ),
    )


def downgrade():
    op.drop_column("tenant_bsc_config", "display_identifiers")
