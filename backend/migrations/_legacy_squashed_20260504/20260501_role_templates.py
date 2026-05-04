"""Mirror Core 0016: create ``role_templates`` + seed from NULL-tenant ``roles``.

Revision ID: 20260501_role_templates
Revises: 20260501_audit_tid_nn
Create Date: 2026-05-01

TMS-side mirror of Core ``0016_role_templates``. TMS carries its own
physical copy of ``roles``, so the same DDL + data copy must be
applied here.

Phase 1 only — creates the table and seeds it. Does NOT touch the
existing ``roles`` table or the FK chain
(``role_permission_grants``, ``role_permissions``,
``user_role_assignments``, ``user_roles``). Phase 2 (separate issue)
handles the FK migration + RBAC service update.

See Core migration for full rationale.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260501_role_templates"
down_revision = "20260501_audit_tid_nn"
branch_labels = None
depends_on = None


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


def upgrade() -> None:
    conn = op.get_bind()

    if not _table_exists(conn, "role_templates"):
        op.create_table(
            "role_templates",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("name", sa.String(length=100), nullable=False, index=True),
            sa.Column("slug", sa.String(length=100), nullable=False, unique=True, index=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("name", name="uq_role_template_name"),
        )

    if _table_exists(conn, "roles"):
        op.execute(
            """
            INSERT INTO role_templates
                (name, slug, description, is_system, created_at)
            SELECT
                r.name,
                r.slug,
                r.description,
                COALESCE(r.is_system, true),
                COALESCE(r.created_at, now())
            FROM roles r
            WHERE r.tenant_id IS NULL
            ON CONFLICT (slug) DO NOTHING
            """
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "role_templates"):
        op.drop_table("role_templates")
