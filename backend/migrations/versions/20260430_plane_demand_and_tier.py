"""Add ``DEMAND`` to ``plane_enum`` + ``tier`` / add-on columns to ``plane_registration``.

Revision ID: 20260430_plane_demand_tier
Revises: 20260422_plane_reg
Create Date: 2026-04-30

Mirrors Core's ``azirella_data_model`` migration ``0011_plane_demand_and_tier``
(see ``Autonomy-Core/docs/CONSUMER_ADOPTION_LOG.md`` 2026-04-30 entry).

Two MIGRATION_REGISTER items shipped together because they touch the same
table:

1. **§3.6 follow-up** — adds ``DEMAND`` to TMS's ``plane_enum``. TMS does
   NOT implement the DP plane (SCP is first impl per §3.6) — TMS reads
   demand artifacts from SCP via MCP per the 2026-04-29 cross-plane rule.
   The enum addition is purely so that ``PlaneRegistry`` reads on this
   side can return ``Plane.DEMAND`` when SCP-side has it registered for
   a shared tenant. ``DEMAND_SHAPING`` stays in place — still used by
   intersection-contract migrations 20260427_intersection_supply_transport.
2. **§3.10** — adds ``plane_tier_enum`` and three columns to
   ``plane_registration``: ``tier`` (default ``T1_EXECUTION``),
   ``cross_plane_intersection`` (default FALSE), ``skills_narration``
   (default FALSE). All existing TMS rows backfill to T1 + FALSE.

Idempotent — guarded by ``information_schema`` / ``pg_type`` lookups.

After this lands:
- Bump ``azirella-data-model`` pin in ``backend/requirements.txt`` to
  Core's ``f82092a`` (data-model 0.9.0).
- TMS does NOT register ``Plane.DEMAND`` on tenant creation. Plane
  registration in TMS continues to register ``Plane.TRANSPORT`` only.
- No backfill script — TMS doesn't own DP rows.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260430_plane_demand_tier"
down_revision = "20260422_plane_reg"
branch_labels = None
depends_on = None


_TIER_VALUES = (
    "T1_EXECUTION",
    "T2_TACTICAL",
    "T3_STRATEGIC",
)


def _enum_value_exists(enum_name: str, value: str) -> bool:
    conn = op.get_bind()
    return bool(
        conn.execute(
            sa.text(
                """
                SELECT 1
                FROM pg_type t
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE t.typname = :enum_name AND e.enumlabel = :value
                """
            ),
            {"enum_name": enum_name, "value": value},
        ).scalar()
    )


def _enum_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM pg_type WHERE typname = :n"),
            {"n": name},
        ).scalar()
    )


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


def upgrade() -> None:
    # 1. Add DEMAND to plane_enum (additive — DEMAND_SHAPING stays).
    if not _enum_value_exists("plane_enum", "DEMAND"):
        # ALTER TYPE ADD VALUE cannot run inside a transaction block in
        # older PG versions; Alembic's autocommit_block makes it safe.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE plane_enum ADD VALUE 'DEMAND'")

    # 2. Create plane_tier_enum.
    if not _enum_exists("plane_tier_enum"):
        op.execute(
            "CREATE TYPE plane_tier_enum AS ENUM ("
            + ", ".join(f"'{v}'" for v in _TIER_VALUES)
            + ")"
        )

    # 3. Add tier column with backfill default T1.
    if not _column_exists("plane_registration", "tier"):
        op.add_column(
            "plane_registration",
            sa.Column(
                "tier",
                sa.Enum(
                    *_TIER_VALUES, name="plane_tier_enum", create_type=False
                ),
                nullable=False,
                server_default="T1_EXECUTION",
            ),
        )

    # 4. Add cross_plane_intersection add-on flag.
    if not _column_exists("plane_registration", "cross_plane_intersection"):
        op.add_column(
            "plane_registration",
            sa.Column(
                "cross_plane_intersection",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    # 5. Add skills_narration add-on flag.
    if not _column_exists("plane_registration", "skills_narration"):
        op.add_column(
            "plane_registration",
            sa.Column(
                "skills_narration",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    # Note: PostgreSQL does not support removing a value from an enum
    # type. The DEMAND value, once added, stays. Downgrade only reverses
    # the column + tier-enum additions.
    if _column_exists("plane_registration", "skills_narration"):
        op.drop_column("plane_registration", "skills_narration")
    if _column_exists("plane_registration", "cross_plane_intersection"):
        op.drop_column("plane_registration", "cross_plane_intersection")
    if _column_exists("plane_registration", "tier"):
        op.drop_column("plane_registration", "tier")
    if _enum_exists("plane_tier_enum"):
        op.execute("DROP TYPE plane_tier_enum")
