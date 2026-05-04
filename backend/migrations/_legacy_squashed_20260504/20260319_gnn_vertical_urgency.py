"""Add vertical urgency propagation columns to gnn_directive_reviews

Revision ID: 20260319_gnn_vert_urg
Revises: 20260318_d365_odoo_stg
Create Date: 2026-03-19
"""
from alembic import op
import sqlalchemy as sa

revision = "20260319_gnn_vert_urg"
down_revision = "20260318_d365_odoo_stg"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("gnn_directive_reviews",
                  sa.Column("decision_level", sa.String(20), nullable=True))
    op.add_column("gnn_directive_reviews",
                  sa.Column("propagated_urgency", sa.Float(), nullable=True))
    op.add_column("gnn_directive_reviews",
                  sa.Column("escalation_id", sa.Integer(),
                            sa.ForeignKey("powell_escalation_log.id", ondelete="SET NULL"),
                            nullable=True))
    op.add_column("gnn_directive_reviews",
                  sa.Column("source_signals", sa.JSON(), nullable=True))
    op.add_column("gnn_directive_reviews",
                  sa.Column("local_resolution_attempted", sa.Boolean(), server_default="false"))
    op.add_column("gnn_directive_reviews",
                  sa.Column("local_resolution_blocked_by", sa.String(200), nullable=True))
    op.add_column("gnn_directive_reviews",
                  sa.Column("revenue_at_risk", sa.Float(), nullable=True))
    op.add_column("gnn_directive_reviews",
                  sa.Column("cost_of_delay_per_day", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("gnn_directive_reviews", "cost_of_delay_per_day")
    op.drop_column("gnn_directive_reviews", "revenue_at_risk")
    op.drop_column("gnn_directive_reviews", "local_resolution_blocked_by")
    op.drop_column("gnn_directive_reviews", "local_resolution_attempted")
    op.drop_column("gnn_directive_reviews", "source_signals")
    op.drop_column("gnn_directive_reviews", "escalation_id")
    op.drop_column("gnn_directive_reviews", "propagated_urgency")
    op.drop_column("gnn_directive_reviews", "decision_level")
