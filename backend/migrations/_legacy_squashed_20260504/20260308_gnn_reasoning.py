"""Add proposed_reasoning to gnn_directive_reviews

Revision ID: 20260308_gnn_reason
Revises: 20260308_site_tgnn
Create Date: 2026-03-08

Add proposed_reasoning Text column to gnn_directive_reviews so that GNN
directive proposals include plain-English explanations alongside the
raw JSON proposed_values.
"""

revision = "20260308_gnn_reason"
down_revision = "20260308_site_tgnn"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'gnn_directive_reviews')")
    )
    if result.scalar():
        op.add_column(
            "gnn_directive_reviews",
            sa.Column("proposed_reasoning", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'gnn_directive_reviews')")
    )
    if result.scalar():
        op.drop_column("gnn_directive_reviews", "proposed_reasoning")
