"""Create supply chain training artifacts table

Revision ID: 20250226091000
Revises: 20250226090500
Create Date: 2025-09-26 09:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20250226091000"
down_revision = "20250226090500"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supply_chain_training_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("dataset_name", sa.String(length=255), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["config_id"], ["supply_chain_configs.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("supply_chain_training_artifacts")
