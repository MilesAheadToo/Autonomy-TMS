"""Powell tables: rename location_id → site_id (AWS SC DM compliance).

Mirrors Core commit 3b77cd4 (2026-04-21) which renamed the column on
PowellAllocation, PowellATPDecision, PowellPODecision, and
PowellBufferDecision. Core previously had the drift — 7 of 11 powell
decision tables used site_id (AWS SC DM canonical) and 4 used
location_id. The Core rename unifies them; TMS applies the parallel
DB migration here.

TMS-side adoption steps from Autonomy-Core/docs/TMS_ADOPTION_GUIDE_20260421.md:
  1. ALTER TABLE ... RENAME COLUMN location_id TO site_id (x4)
  2. Rename the indexes that carry the old column in their name (x4)
  3. No constraint renames needed — uq_powell_alloc_key's internal
     column list auto-updates under PostgreSQL's RENAME COLUMN; the
     constraint name itself stays unchanged (matches Core).

Revision ID: 20260421_powell_site
Revises: (no dependency — idempotent, information_schema guarded)
Create Date: 2026-04-21
"""
from alembic import op
import sqlalchemy as sa


revision = "20260421_powell_site"
down_revision = None
branch_labels = None
depends_on = None


# (table, old_column, new_column)
_COLUMN_RENAMES = [
    ("powell_allocations", "location_id", "site_id"),
    ("powell_atp_decisions", "location_id", "site_id"),
    ("powell_po_decisions", "location_id", "site_id"),
    ("powell_buffer_decisions", "location_id", "site_id"),
]

# (old_index_name, new_index_name) — column-rename updates the column
# list stored with the index automatically; only the index NAME needs
# explicit rename to match the new site_id convention.
_INDEX_RENAMES = [
    ("idx_alloc_config_product_loc", "idx_alloc_config_product_site"),
    ("idx_atp_product_loc", "idx_atp_product_site"),
    ("idx_powell_po_product_loc", "idx_powell_po_product_site"),
    ("idx_buffer_product_loc", "idx_buffer_product_site"),
]


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c AND table_schema = 'public'"
        ),
        {"t": table, "c": column},
    ).scalar())


def _index_exists(name: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes WHERE indexname = :n AND schemaname = 'public'"
        ),
        {"n": name},
    ).scalar())


def upgrade():
    for table, old, new in _COLUMN_RENAMES:
        if _column_exists(table, old) and not _column_exists(table, new):
            op.alter_column(table, old, new_column_name=new)

    for old, new in _INDEX_RENAMES:
        if _index_exists(old) and not _index_exists(new):
            op.execute(sa.text(f'ALTER INDEX "{old}" RENAME TO "{new}"'))


def downgrade():
    # Reverse rename — only if downgrade is ever run against a DB that
    # already applied the upgrade.
    for old, new in _INDEX_RENAMES:
        if _index_exists(new) and not _index_exists(old):
            op.execute(sa.text(f'ALTER INDEX "{new}" RENAME TO "{old}"'))

    for table, old, new in _COLUMN_RENAMES:
        if _column_exists(table, new) and not _column_exists(table, old):
            op.alter_column(table, new, new_column_name=old)
