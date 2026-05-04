"""Add enable_site_tgnn to site_agent_configs

Revision ID: 20260308_site_tgnn
Revises: 20260308_reasoning_scope
Create Date: 2026-03-08

Add enable_site_tgnn boolean column (default False) to site_agent_configs
for feature-flagging the Site tGNN (Layer 1.5) intra-site cross-TRM
coordination model.
"""

revision = "20260308_site_tgnn"
down_revision = "20260308_reasoning_scope"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column already exists (idempotent migration)."""
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c"
    ), {"t": table_name, "c": column_name})
    return result.fetchone() is not None


def upgrade():
    if not _column_exists("site_agent_configs", "enable_site_tgnn"):
        op.add_column(
            "site_agent_configs",
            sa.Column(
                "enable_site_tgnn",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade():
    if _column_exists("site_agent_configs", "enable_site_tgnn"):
        op.drop_column("site_agent_configs", "enable_site_tgnn")
