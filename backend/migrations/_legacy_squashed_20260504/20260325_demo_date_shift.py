"""Add demo_date_shift_log table.

Tracks date shift operations for demo tenants so that demo data stays fresh.
Each row records when dates were last shifted and by how many cumulative days.

Revision ID: 20260325_demo_date_shift
Revises: 20260326_tenant_bsc_config
Create Date: 2026-03-25
"""

from alembic import op
import sqlalchemy as sa

revision = "20260325_demo_date_shift"
down_revision = "20260326_tenant_bsc_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demo_date_shift_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("config_id", sa.Integer, nullable=False),
        sa.Column("last_shifted_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("total_shift_days", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "config_id", name="uq_demo_date_shift_tenant_config"),
    )

    # Seed the Food Dist demo entry so the scheduler picks it up immediately
    op.execute(
        "INSERT INTO demo_date_shift_log (tenant_id, config_id, last_shifted_at, total_shift_days) "
        "VALUES (3, 22, NOW(), 0)"
    )


def downgrade() -> None:
    op.drop_table("demo_date_shift_log")
