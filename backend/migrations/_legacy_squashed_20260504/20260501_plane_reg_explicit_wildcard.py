"""Plane-registration explicit wildcard hardening (mirrors Core 0009).

Revision ID: 20260501_plane_reg_wildcard
Revises: 20260430_plane_demand_tier
Create Date: 2026-05-01

Mirrors Autonomy-Core migration ``0009_plane_registration_explicit_wildcard``
(SOC II hardening — MIGRATION_REGISTER §1.15).

Replaces the implicit nullable-config_id-as-wildcard pattern with an
explicit ``applies_to_all_configs`` boolean plus a CHECK constraint.
A NULL config_id was previously indistinguishable from a
writer-forgot-to-set-it bug, every consumer query had to include
``OR config_id IS NULL`` predicates, and accidental config_id drops
silently created tenant-wide wildcard registrations.

Migration:
1. ADD COLUMN ``applies_to_all_configs BOOLEAN NOT NULL DEFAULT false``.
2. Backfill: existing rows with ``config_id IS NULL`` are tenant
   wildcards by definition → flip their boolean to true.
3. ADD CHECK constraint ``applies_to_all_configs = TRUE OR
   config_id IS NOT NULL``. After backfill every row passes; future
   INSERTs with neither set fail loudly instead of silently becoming
   wildcards.

Idempotent — column add and CHECK use ``information_schema`` /
``pg_constraint`` guards. Re-running is a no-op.

Why TMS needs this:
- TMS pin bump past Core ``18db6a2`` brings in the ORM with
  ``applies_to_all_configs`` declared (line 108 of
  ``planes/registry.py``). Without this column in TMS DB, every
  ``SELECT plane_registration.*`` from the new ORM raises
  ``UndefinedColumnError``. Surfaced 2026-05-01 while wiring the §3.10
  cross_plane_intersection follow-up.
- TMS issue #2 (broader tenant_id-discipline sweep) covers more Core
  drift but is deliberately deferred. This is the narrow piece needed
  to make plane_registration reads work after the next rebuild.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260501_plane_reg_wildcard"
down_revision = "20260430_plane_demand_tier"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    return bool(
        conn.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = :t
                  AND column_name = :c
                """
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def _constraint_exists(table: str, constraint: str) -> bool:
    conn = op.get_bind()
    return bool(
        conn.execute(
            sa.text(
                """
                SELECT 1
                FROM pg_constraint
                WHERE conname = :n
                  AND conrelid = (:t::regclass)
                """
            ),
            {"n": constraint, "t": table},
        ).scalar()
    )


def upgrade() -> None:
    # 1. Add applies_to_all_configs column with default FALSE.
    if not _column_exists("plane_registration", "applies_to_all_configs"):
        op.add_column(
            "plane_registration",
            sa.Column(
                "applies_to_all_configs",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    # 2. Backfill: existing rows with config_id IS NULL are wildcards.
    op.execute(
        """
        UPDATE plane_registration
           SET applies_to_all_configs = TRUE
         WHERE config_id IS NULL
           AND applies_to_all_configs = FALSE
        """
    )

    # 3. Add CHECK constraint.
    if not _constraint_exists(
        "plane_registration", "plane_registration_wildcard_or_config_chk"
    ):
        op.create_check_constraint(
            "plane_registration_wildcard_or_config_chk",
            "plane_registration",
            "applies_to_all_configs = TRUE OR config_id IS NOT NULL",
        )


def downgrade() -> None:
    if _constraint_exists(
        "plane_registration", "plane_registration_wildcard_or_config_chk"
    ):
        op.drop_constraint(
            "plane_registration_wildcard_or_config_chk",
            "plane_registration",
        )
    if _column_exists("plane_registration", "applies_to_all_configs"):
        op.drop_column("plane_registration", "applies_to_all_configs")
