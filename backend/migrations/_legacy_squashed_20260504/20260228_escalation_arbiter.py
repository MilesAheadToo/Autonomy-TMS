"""escalation_arbiter

Revision ID: 20260228_escalation_arbiter
Revises: 20260301_missing_powell
Create Date: 2026-02-28

Add powell_escalation_log table for the Escalation Arbiter service.
Tracks vertical escalation events when persistent TRM anomalies
indicate that operational or strategic replanning is needed.

See docs/ESCALATION_ARCHITECTURE.md for architecture details.
"""

revision = "20260228_escalation_arbiter"
down_revision = "20260301_missing_powell"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def upgrade():
    op.create_table(
        "powell_escalation_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("site_key", sa.String(100), nullable=False),
        sa.Column("escalation_level", sa.String(20), nullable=False),
        sa.Column("diagnosis", sa.Text(), nullable=False),
        sa.Column("urgency", sa.String(20), nullable=False),
        sa.Column("recommended_action", sa.String(50), nullable=False),
        sa.Column("affected_trm_types", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("affected_sites", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "idx_escalation_log_tenant", "powell_escalation_log", ["tenant_id"]
    )
    op.create_index(
        "idx_escalation_log_site", "powell_escalation_log", ["site_key"]
    )
    op.create_index(
        "idx_escalation_log_level", "powell_escalation_log", ["escalation_level"]
    )
    op.create_index(
        "idx_escalation_log_created", "powell_escalation_log", ["created_at"]
    )
    op.create_index(
        "idx_escalation_log_unresolved",
        "powell_escalation_log",
        ["resolved", "created_at"],
    )


def downgrade():
    op.drop_index("idx_escalation_log_unresolved", "powell_escalation_log")
    op.drop_index("idx_escalation_log_created", "powell_escalation_log")
    op.drop_index("idx_escalation_log_level", "powell_escalation_log")
    op.drop_index("idx_escalation_log_site", "powell_escalation_log")
    op.drop_index("idx_escalation_log_tenant", "powell_escalation_log")
    op.drop_table("powell_escalation_log")
