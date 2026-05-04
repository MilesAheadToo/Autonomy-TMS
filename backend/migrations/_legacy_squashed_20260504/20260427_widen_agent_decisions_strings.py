"""Widen agent_decisions narrow string columns.

Pre-existing TMS schema constraint: `agent_decisions.agent_version`
is varchar(20), but several `_v1`-suffixed TRM identifiers exceed
that:

    intermodal_transfer_v1   (22 chars)
    equipment_reposition_v1  (22 chars)
    exception_management_v1  (22 chars)
    freight_procurement_v1   (21 chars)
    capacity_promise_v1      (20 chars — at the edge)

Writes from those TRMs silently failed at the agent_decisions
dual-write site (`record_trm_decision` logs ERROR + rolls back).

This migration widens three short-string columns to varchar(50):
  * agent_version          : 20 → 50  (TRM identifiers with version suffix)
  * planning_cycle         : 20 → 50  (cycle names like "weekly_2026_w17")
  * override_classification: 20 → 50  (override taxonomy can grow)

Idempotent: information_schema-guarded; only ALTERs columns that are
currently varchar(20) or shorter.

Revision ID: 20260427_widen_ad_strings
Revises: (no dependency — idempotent)
Create Date: 2026-04-27
"""
from alembic import op
import sqlalchemy as sa


revision = "20260427_widen_ad_strings"
down_revision = None
branch_labels = None
depends_on = None


_COLS_TO_WIDEN = (
    ("agent_version", 50),
    ("planning_cycle", 50),
    ("override_classification", 50),
)


def _column_max_length(column: str) -> int | None:
    conn = op.get_bind()
    return conn.execute(
        sa.text(
            "SELECT character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_name='agent_decisions' AND column_name = :c"
        ),
        {"c": column},
    ).scalar()


def upgrade() -> None:
    for col, target_len in _COLS_TO_WIDEN:
        current = _column_max_length(col)
        if current is None:
            continue  # column doesn't exist — odd but skip cleanly
        if current >= target_len:
            continue  # already wide enough
        op.execute(
            f"ALTER TABLE agent_decisions "
            f"ALTER COLUMN {col} TYPE VARCHAR({target_len})"
        )


def downgrade() -> None:
    # Narrowing risks data truncation; downgrade is a no-op by design.
    # If you genuinely need to revert, write a separate migration that
    # explicitly handles the truncation policy.
    pass
