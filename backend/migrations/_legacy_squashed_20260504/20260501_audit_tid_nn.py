"""Mirror Core 0015: audit_logs tenant_id NOT NULL + sentinel System tenant.

Revision ID: 20260501_audit_tid_nn
Revises: 20260501_authorities_tid_nn
Create Date: 2026-05-01

TMS-side mirror of Core ``0015_audit_logs_tid_nn``. TMS carries its own
physical copy of ``audit_logs`` and ``tenants``, so the same DDL +
sentinel-creation must happen here.

Differences from SCP's ``20260430_audit_tid_nn``:

- TMS ``tenants`` has more NOT NULL columns than SCP's. In particular,
  ``admin_id`` is NOT NULL and FKs to ``users.id`` (no default), and
  several quota / mode columns have no DB default. The INSERT here
  enumerates every NOT NULL column explicitly.
- TMS ``audit_logs`` audit (2026-05-01) shows zero NULL ``tenant_id``
  rows in the live DB — so the SET NOT NULL is straightforward; no
  pre-existing rows need backfilling. The System tenant is still
  created so that future code emitting tenant-less audit events has a
  stable FK target (matches Core / SCP semantics).
- Creates a dedicated sentinel user
  ``system-tenant-admin@autonomy.internal`` for the System tenant's
  ``admin_id``. The ``tenants.admin_id`` UNIQUE constraint forbids
  reusing an existing admin (e.g. ``systemadmin@autonomy.ai`` already
  admins another tenant). The sentinel user has an unusable bcrypt
  hash and ``is_active=false`` so it can never log in.

Idempotent — every INSERT uses ``WHERE NOT EXISTS``; the ALTER COLUMN
is guarded by ``information_schema.is_nullable``. Re-running is a
no-op.

See ``Autonomy-Core/docs/CONSUMER_ADOPTION_LOG.md`` 2026-04-30 entry
"RLS tenant_id discipline" for the cross-product framing.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260501_audit_tid_nn"
down_revision = "20260501_authorities_tid_nn"
branch_labels = None
depends_on = None


SYSTEM_TENANT_NAME = "System"
SYSTEM_CUSTOMER_NAME = "System"
SYSTEM_TENANT_ADMIN_EMAIL = "system-tenant-admin@autonomy.internal"


def _table_exists(conn, table: str, schema: str = "public") -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :s AND table_name = :t"
            ),
            {"s": schema, "t": table},
        ).scalar()
    )


def _is_nullable(conn, table: str, column: str = "tenant_id", schema: str = "public") -> bool:
    return (
        conn.execute(
            sa.text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
            ),
            {"s": schema, "t": table, "c": column},
        ).scalar()
        == "YES"
    )


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "audit_logs") or not _table_exists(conn, "tenants"):
        return

    # Step 1a — ensure the System customer exists.
    if _table_exists(conn, "customers"):
        op.execute(
            f"""
            INSERT INTO customers (name, slug, status, purchased_solutions)
            SELECT '{SYSTEM_CUSTOMER_NAME}', 'system',
                   'active'::customer_status_enum, '[]'::jsonb
            WHERE NOT EXISTS (
                SELECT 1 FROM customers WHERE name = '{SYSTEM_CUSTOMER_NAME}'
            )
            """
        )

    customer_id = conn.execute(
        sa.text("SELECT id FROM customers WHERE name = :n LIMIT 1"),
        {"n": SYSTEM_CUSTOMER_NAME},
    ).scalar()
    if customer_id is None:
        raise RuntimeError(
            f"System customer ({SYSTEM_CUSTOMER_NAME!r}) was not created."
        )

    # Step 1b — ensure the System-tenant-admin sentinel user exists.
    # ``tenants.admin_id`` is a UNIQUE NOT NULL FK; reusing another
    # tenant's admin would violate the constraint. So we provision a
    # dedicated user that exists solely to satisfy this FK.
    op.execute(
        sa.text(
            """
            INSERT INTO users (
                email, hashed_password,
                last_password_change, failed_login_attempts,
                mfa_enabled, is_active, is_superuser,
                created_at, updated_at, user_type
            )
            SELECT
                :email, '!disabled-sentinel!',
                now(), 0,
                FALSE, FALSE, FALSE,
                now(), now(),
                'USER'::user_type_enum
            WHERE NOT EXISTS (
                SELECT 1 FROM users WHERE email = :email
            )
            """
        ).bindparams(email=SYSTEM_TENANT_ADMIN_EMAIL)
    )

    admin_user_id = conn.execute(
        sa.text("SELECT id FROM users WHERE email = :e LIMIT 1"),
        {"e": SYSTEM_TENANT_ADMIN_EMAIL},
    ).scalar()
    if admin_user_id is None:
        raise RuntimeError(
            "System-tenant-admin sentinel user was not created."
        )

    # Step 1c — ensure the System tenant row exists. Enumerates every
    # NOT NULL column without a DB default.
    op.execute(
        sa.text(
            """
            INSERT INTO tenants (
                name, slug, subdomain, admin_id, status, billing_plan,
                max_users, max_storage_mb,
                current_user_count, current_storage_mb,
                mode, is_demo, session_timeout_minutes, customer_id
            )
            SELECT
                :name, 'system', 'system', :admin_id,
                'active', 'enterprise',
                0, 0, 0, 0,
                'production'::tenant_mode_enum,
                FALSE, 30, :customer_id
            WHERE NOT EXISTS (
                SELECT 1 FROM tenants WHERE name = :name
            )
            """
        ).bindparams(
            name=SYSTEM_TENANT_NAME,
            admin_id=int(admin_user_id),
            customer_id=int(customer_id),
        )
    )

    # Step 2 — resolve System tenant ID and backfill any straggler
    # audit_logs rows. TMS DB audit (2026-05-01) shows zero NULL rows,
    # but the UPDATE is harmless and keeps the migration safe in
    # other environments.
    system_tenant_id = conn.execute(
        sa.text("SELECT id FROM tenants WHERE name = :n LIMIT 1"),
        {"n": SYSTEM_TENANT_NAME},
    ).scalar()
    if system_tenant_id is None:
        raise RuntimeError(
            f"System tenant ({SYSTEM_TENANT_NAME!r}) was not created."
        )

    conn.execute(
        sa.text(
            "UPDATE audit_logs SET tenant_id = :tid WHERE tenant_id IS NULL"
        ),
        {"tid": int(system_tenant_id)},
    )

    # Step 3 — tighten.
    if _is_nullable(conn, "audit_logs"):
        op.alter_column(
            "audit_logs", "tenant_id", schema="public", nullable=False
        )


def downgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "audit_logs"):
        return
    if not _is_nullable(conn, "audit_logs"):
        op.alter_column(
            "audit_logs", "tenant_id", schema="public", nullable=True
        )
