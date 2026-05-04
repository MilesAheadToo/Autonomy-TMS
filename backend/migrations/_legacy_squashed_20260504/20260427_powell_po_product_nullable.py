"""Drop NOT NULL on powell_po_decisions.product_id (MR 1.7 partial).

Revision ID: 20260427_po_product_nullable
Revises: 20260427_widen_ad_strings
Create Date: 2026-04-27

Mirrors Core data-model migration 0008_powell_po_product_nullable.
Lets freight / service / brokerage POs persist to
``powell_po_decisions`` without a product anchor — see
MIGRATION_REGISTER 1.7. Today TMS writes only to ``freight_tender``,
so this is enabling rather than reactive; lands ahead of the freight-
side TRMs migrating to the canonical decision log.

Idempotent — guarded by an information_schema lookup.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260427_po_product_nullable"
down_revision = "20260427_widen_ad_strings"
branch_labels = None
depends_on = None


def _is_nullable(table: str, column: str) -> bool:
    conn = op.get_bind()
    val = conn.execute(
        sa.text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).scalar()
    return (val or "").upper() == "YES"


def upgrade() -> None:
    if not _is_nullable("powell_po_decisions", "product_id"):
        op.alter_column(
            "powell_po_decisions",
            "product_id",
            existing_type=sa.String(length=100),
            nullable=True,
        )


def downgrade() -> None:
    conn = op.get_bind()
    null_rows = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM powell_po_decisions WHERE product_id IS NULL"
        )
    ).scalar()
    if null_rows == 0 and _is_nullable("powell_po_decisions", "product_id"):
        op.alter_column(
            "powell_po_decisions",
            "product_id",
            existing_type=sa.String(length=100),
            nullable=False,
        )
