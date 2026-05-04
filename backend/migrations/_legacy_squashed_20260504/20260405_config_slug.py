"""supply_chain_configs: add human-readable slug column

Revision ID: 20260405_config_slug
Revises: 20260405_corpus_step
Create Date: 2026-04-05

Adds a `slug` column to supply_chain_configs. Format:
`{tenant_slug}-{created_at:%Y%m%dT%H%M%SZ}`, e.g. `food-dist-20260328T143022Z`.

The integer primary key is unchanged. The slug is a secondary identifier
used for UI display, log messages, URL paths, and sorting by creation time.
"""
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260405_config_slug"
down_revision: Union[str, None] = "20260405_corpus_step"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "supply_chain_configs",
        sa.Column("slug", sa.String(80), nullable=True),
    )
    op.create_index(
        "ix_supply_chain_configs_slug",
        "supply_chain_configs",
        ["slug"],
        unique=True,
    )
    # Backfill existing rows. PostgreSQL regexp_replace is used to sanitize
    # the tenant name into a slug-safe form.
    op.execute("""
        UPDATE supply_chain_configs sc
        SET slug = lower(regexp_replace(
            COALESCE(t.name, 'tenant-' || sc.tenant_id::text),
            '[^A-Za-z0-9]+', '-', 'g'
        )) || '-' || to_char(sc.created_at AT TIME ZONE 'UTC', 'YYYYMMDD"T"HH24MISS"Z"') || '-c' || sc.id::text
        FROM tenants t
        WHERE sc.tenant_id = t.id
          AND sc.slug IS NULL
    """)
    # Any rows without a matching tenant (shouldn't happen, but be safe)
    op.execute("""
        UPDATE supply_chain_configs
        SET slug = 'config-' || id::text || '-'
                   || to_char(created_at AT TIME ZONE 'UTC', 'YYYYMMDD"T"HH24MISS"Z"')
        WHERE slug IS NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_supply_chain_configs_slug", table_name="supply_chain_configs")
    op.drop_column("supply_chain_configs", "slug")
