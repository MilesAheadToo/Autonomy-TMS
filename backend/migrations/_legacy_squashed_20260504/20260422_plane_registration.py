"""Create plane_registration table (adopts Core MIGRATION_REGISTER 1.9).

Mirrors Core migration 0002_plane_registry (shipped in Core 18db6a2 /
v0.4.0). TMS's alembic chain is separate from Core's, so we ship a
parallel migration here rather than chaining across packages.

Revision ID: 20260422_plane_registration
Revises: (no dependency — idempotent, information_schema guarded)
Create Date: 2026-04-22

Creates:
- plane_enum TYPE with 6 values (SUPPLY, TRANSPORT, PORTFOLIO,
  DEMAND_SHAPING, PRODUCTION, WAREHOUSE)
- plane_registration TABLE with (tenant_id FK, nullable config_id FK,
  plane, registered_at, deregistered_at, plane_metadata JSONB)
- Two indexes: idx_plane_reg_active + idx_plane_reg_tenant

Once SCP adopts the same rename, Core can reclaim authoritative
ownership of this table; for now, each product creates its own copy
via its own alembic chain.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260422_plane_reg"
down_revision = None
branch_labels = None
depends_on = None


_PLANE_VALUES = (
    "SUPPLY",
    "TRANSPORT",
    "PORTFOLIO",
    "DEMAND_SHAPING",
    "PRODUCTION",
    "WAREHOUSE",
)


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = :n AND table_schema = 'public'"
        ),
        {"n": name},
    ).scalar())


def _enum_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :n"),
        {"n": name},
    ).scalar())


def upgrade() -> None:
    # Create the enum type explicitly, then reference it with
    # create_type=False in the column. Alembic's op.create_table path
    # can double-emit CREATE TYPE when the Enum column carries a
    # create-able type, so we make the type-creation and column-referencing
    # phases disjoint.
    plane_enum = postgresql.ENUM(*_PLANE_VALUES, name="plane_enum")
    if not _enum_exists("plane_enum"):
        plane_enum.create(op.get_bind(), checkfirst=False)

    plane_enum_col = postgresql.ENUM(
        *_PLANE_VALUES, name="plane_enum", create_type=False
    )

    if not _table_exists("plane_registration"):
        op.create_table(
            "plane_registration",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id",
                sa.Integer,
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "config_id",
                sa.Integer,
                sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("plane", plane_enum_col, nullable=False),
            sa.Column(
                "registered_at",
                sa.DateTime,
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("deregistered_at", sa.DateTime, nullable=True),
            sa.Column("plane_metadata", sa.JSON, nullable=True),
        )
        op.create_index(
            "idx_plane_reg_active",
            "plane_registration",
            ["tenant_id", "config_id", "plane", "deregistered_at"],
        )
        op.create_index(
            "idx_plane_reg_tenant",
            "plane_registration",
            ["tenant_id"],
        )


def downgrade() -> None:
    if _table_exists("plane_registration"):
        op.drop_index("idx_plane_reg_tenant", table_name="plane_registration")
        op.drop_index("idx_plane_reg_active", table_name="plane_registration")
        op.drop_table("plane_registration")
    if _enum_exists("plane_enum"):
        op.execute("DROP TYPE plane_enum")
