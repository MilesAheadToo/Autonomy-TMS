"""add training_corpus provisioning step columns

Revision ID: 20260405_corpus_step
Revises: 20260404_corpus_ckpt
Create Date: 2026-04-05

Wires the unified training corpus build into the provisioning pipeline as
step 1b (between warm_start and sop_graphsage). See
docs/internal/architecture/UNIFIED_TRAINING_CORPUS.md §11.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260405_corpus_step"
down_revision: Union[str, None] = "20260404_corpus_ckpt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "config_provisioning_status",
        sa.Column("training_corpus_status", sa.String(20), server_default="pending"),
    )
    op.add_column(
        "config_provisioning_status",
        sa.Column("training_corpus_at", sa.DateTime, nullable=True),
    )
    op.add_column(
        "config_provisioning_status",
        sa.Column("training_corpus_error", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("config_provisioning_status", "training_corpus_error")
    op.drop_column("config_provisioning_status", "training_corpus_at")
    op.drop_column("config_provisioning_status", "training_corpus_status")
