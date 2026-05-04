"""tenant virtual clock (frozen demo mode)

Revision ID: 20260405_virtual_clock
Revises: 20260404_corpus_ckpt
Create Date: 2026-04-05

Adds per-tenant virtual clock support so demo tenants can freeze "today" at a
historical reference date while production tenants continue to use the real
current date. External data can be served from a snapshot or live.

See docs/internal/VIRTUAL_CLOCK_ARCHITECTURE.md for design details.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260405_virtual_clock"
down_revision: Union[str, None] = "20260404_corpus_ckpt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Per-tenant virtual clock configuration.
    # NOTE: field named `time_mode` (not `clock_mode`) to avoid collision with
    # existing `clock_mode` enum used for Beer Game turn/timed/realtime progression.
    op.add_column(
        "tenants",
        sa.Column(
            "time_mode",
            sa.String(length=16),
            nullable=False,
            server_default="live",
            comment="live=real today, frozen=use virtual_today",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "virtual_today",
            sa.Date(),
            nullable=True,
            comment="Frozen reference date when time_mode=frozen",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "external_data_mode",
            sa.String(length=16),
            nullable=False,
            server_default="live",
            comment="live=call external APIs, snapshot=replay captured data",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "external_snapshot_id",
            sa.String(length=100),
            nullable=True,
            comment="Snapshot identifier to replay when external_data_mode=snapshot",
        ),
    )

    # Index for fast lookup by time mode (demo tenants are rare but need fast resolution)
    op.create_index("ix_tenants_time_mode", "tenants", ["time_mode"])

    # Constraint: if time_mode is frozen, virtual_today must be set
    op.create_check_constraint(
        "ck_tenants_frozen_has_virtual_today",
        "tenants",
        "time_mode != 'frozen' OR virtual_today IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tenants_frozen_has_virtual_today", "tenants", type_="check")
    op.drop_index("ix_tenants_time_mode", table_name="tenants")
    op.drop_column("tenants", "external_snapshot_id")
    op.drop_column("tenants", "external_data_mode")
    op.drop_column("tenants", "virtual_today")
    op.drop_column("tenants", "time_mode")
