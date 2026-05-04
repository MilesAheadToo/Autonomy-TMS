"""Create customers table + tenants.customer_id (mirrors Core 0003_customer).

TMS's Alembic chain is separate from Core's. Core ships the canonical
schema in azirella_data_model/migrations/versions/0003_customer.py; we
ship a parallel migration here that produces an identical shape on the
TMS database.

Revision ID: 20260422_customer
Revises: (independent — idempotent, information_schema guarded)
Create Date: 2026-04-22

## Schema produced

- TYPE customer_status_enum (prospect, trial, active, churned)
- TABLE customers (id, name, slug UNIQUE, parent_org_name, status,
  msa_signed_at, billing_plan_tier, purchased_solutions JSONB,
  salesforce_account_id UNIQUE, account_owner_id FK users, notes,
  created_at)
- COLUMN tenants.customer_id (NOT NULL, FK customers.id ON DELETE
  RESTRICT)
- INDEX ix_tenants_customer_id
- CONSTRAINT uq_tenant_customer_mode UNIQUE (customer_id, mode)

## Backfill

Each pre-existing tenant is paired 1:1 with a freshly minted Customer
(slug = tenant.slug + '-cust'). This trivially satisfies the
(customer_id, mode) uniqueness constraint. Operators can later merge
Customers if two Tenants represent the same legal entity.

`purchased_solutions` defaults to `[]` on backfill — it is the
provisioning workflow's responsibility to set this to `["tms"]` (or the
appropriate set) so connector orchestration runs the right modules.
Until set, no ERP/APS extraction will run for these customers.

## Why we don't chain off plane_reg

`20260422_plane_registration.py` already lives at down_revision=None and
gets stitched into the chain via a future merge migration. Same pattern
here — independent and idempotent. The next merge migration will fold
both into the head.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260422_customer"
down_revision = None
branch_labels = None
depends_on = None


_STATUS_VALUES = ("prospect", "trial", "active", "churned")


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = :n AND table_schema = 'public'"
        ),
        {"n": name},
    ).scalar())


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c "
            "AND table_schema = 'public'"
        ),
        {"t": table, "c": column},
    ).scalar())


def _constraint_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text("SELECT 1 FROM pg_constraint WHERE conname = :n"),
        {"n": name},
    ).scalar())


def _enum_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :n"),
        {"n": name},
    ).scalar())


def upgrade() -> None:
    bind = op.get_bind()

    if not _enum_exists("customer_status_enum"):
        op.execute(
            "CREATE TYPE customer_status_enum AS ENUM ("
            + ", ".join(f"'{v}'" for v in _STATUS_VALUES)
            + ")"
        )

    status_col = postgresql.ENUM(
        *_STATUS_VALUES, name="customer_status_enum", create_type=False
    )

    if not _table_exists("customers"):
        op.create_table(
            "customers",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("slug", sa.String(100), nullable=False, unique=True, index=True),
            sa.Column("parent_org_name", sa.String(200), nullable=True),
            sa.Column(
                "status",
                status_col,
                nullable=False,
                server_default="active",
            ),
            sa.Column("msa_signed_at", sa.DateTime, nullable=True),
            sa.Column("billing_plan_tier", sa.String(50), nullable=True),
            sa.Column(
                "purchased_solutions",
                postgresql.JSONB,
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "salesforce_account_id",
                sa.String(50),
                nullable=True,
                unique=True,
                index=True,
            ),
            sa.Column(
                "account_owner_id",
                sa.Integer,
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("notes", sa.Text, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime,
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    if not _column_exists("tenants", "customer_id"):
        op.add_column(
            "tenants",
            sa.Column("customer_id", sa.Integer, nullable=True),
        )

    has_unlinked = bool(bind.execute(
        sa.text("SELECT 1 FROM tenants WHERE customer_id IS NULL LIMIT 1")
    ).scalar())

    if has_unlinked:
        # TMS provisioning today defaults customers to ["tms"] — but we
        # leave the backfill list empty here. Provisioning code will
        # populate it explicitly so the source of truth is the workflow,
        # not a migration default. An operator script can mass-set this
        # for existing tenants if desired.
        bind.execute(sa.text("""
            INSERT INTO customers (name, slug, status, purchased_solutions, created_at)
            SELECT
                t.name,
                t.slug || '-cust',
                'active',
                '[]'::jsonb,
                COALESCE(t.created_at, NOW())
            FROM tenants t
            WHERE t.customer_id IS NULL
        """))
        bind.execute(sa.text("""
            UPDATE tenants t
            SET customer_id = c.id
            FROM customers c
            WHERE c.slug = t.slug || '-cust'
              AND t.customer_id IS NULL
        """))

    op.alter_column("tenants", "customer_id", nullable=False)

    if not _constraint_exists("fk_tenants_customer_id"):
        op.create_foreign_key(
            "fk_tenants_customer_id",
            source_table="tenants",
            referent_table="customers",
            local_cols=["customer_id"],
            remote_cols=["id"],
            ondelete="RESTRICT",
        )

    if not _constraint_exists("uq_tenant_customer_mode"):
        op.create_unique_constraint(
            "uq_tenant_customer_mode",
            "tenants",
            ["customer_id", "mode"],
        )

    op.create_index(
        "ix_tenants_customer_id",
        "tenants",
        ["customer_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    if _constraint_exists("uq_tenant_customer_mode"):
        op.drop_constraint("uq_tenant_customer_mode", "tenants", type_="unique")
    if _constraint_exists("fk_tenants_customer_id"):
        op.drop_constraint("fk_tenants_customer_id", "tenants", type_="foreignkey")
    op.drop_index("ix_tenants_customer_id", table_name="tenants", if_exists=True)
    if _column_exists("tenants", "customer_id"):
        op.drop_column("tenants", "customer_id")
    if _table_exists("customers"):
        op.drop_table("customers")
    if _enum_exists("customer_status_enum"):
        op.execute("DROP TYPE customer_status_enum")
