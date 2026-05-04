"""training corpus build checkpoint

Revision ID: 20260404_corpus_ckpt
Revises: 20260404_training_corpus
Create Date: 2026-04-04

Adds the training_corpus_checkpoint table to support pause + resume of
corpus builds under transient DB failures (Case B in UNIFIED_TRAINING_CORPUS.md §6b).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260404_corpus_ckpt"
down_revision: Union[str, None] = "20260404_training_corpus"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "training_corpus_checkpoint",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("corpus_id", sa.Integer, nullable=False, index=True),
        sa.Column("tenant_id", sa.Integer, nullable=False, index=True),
        sa.Column("config_id", sa.Integer, nullable=False, index=True),
        sa.Column("last_scenario_completed", sa.Integer, nullable=False, default=-1),
        sa.Column("total_scenarios", sa.Integer, nullable=False),
        sa.Column("trm_decisions_written", sa.Integer, nullable=False, default=0),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            default="running",
            # running, paused, completed, failed
        ),
        sa.Column("paused_reason", sa.Text, nullable=True),
        sa.Column("failed_reason", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("corpus_id", name="uq_corpus_checkpoint_corpus_id"),
    )


def downgrade() -> None:
    op.drop_table("training_corpus_checkpoint")
