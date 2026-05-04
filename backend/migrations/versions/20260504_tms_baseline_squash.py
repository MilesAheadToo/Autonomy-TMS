"""TMS baseline schema squash — replaces 237 legacy migrations with one
schema snapshot at the prior chain heads (8 branched heads merged here).

Revision ID: 20260504_tms_baseline_squash
Revises: (none — new chain root after squash)
Create Date: 2026-05-04

Background
----------
Mirrors the SCP-side squash that landed 2026-05-03. The TMS legacy
migration chain (237 files, with **8 branched alembic heads**) has the
same MySQL→Postgres hybrid issue as SCP — 15 migrations contain
MySQL-only DDL (``MODIFY COLUMN``, ``CHANGE COLUMN``, ``JSON_EXTRACT``,
``ON UPDATE CURRENT_TIMESTAMP``, MySQL auto-named FK constraint
references like ``lanes_ibfk_1``). ``alembic upgrade head`` against a
fresh PostgreSQL fails repeatedly on dialect mismatches.

Production TMS databases survived because each migration was applied
incrementally as it was added; nobody ever exercised a fresh-DB
replay end-to-end. TMS hasn't yet had a SOC II audit workflow added,
so this hasn't been forced — but the same squash strategy that
unblocked SCP applies here verbatim, and is a precondition for any
fresh-DB bringup of TMS (e.g., the eventual acer-nitro deploy).

Squash design
-------------
Identical to the SCP squash:

- **Existing prod TMS DBs** are at the union of 8 head revisions
  (``20260416_plan_version``, ``20260417_term``,
  ``20260421_powell_site``, ``20260422_customer``,
  ``20260422_plane_reg``, ``20260422_period_idx``,
  ``20260424_intermodal_network``, ``20260424_policy_parameters``).
  Operator runs ``alembic stamp 20260504_tms_baseline_squash`` once
  to collapse the 8 rows in ``alembic_version`` into 1; future
  upgrades chain off the new squash revision normally.
- **Fresh DBs** (CI, new tenant provisioning, BRINGUP runbook, local
  clean ``docker compose up``) run this baseline once. It executes
  ``baseline_schema.sql`` (a pg_dump --schema-only of the TMS DB at
  this state — 346 tables, all RLS policies, all custom enum types
  and indexes) in one transaction.
- **Future migrations** chain off ``20260504_tms_baseline_squash``
  as their ``down_revision`` like a normal forward path.

The 237 legacy migration files are preserved at
``migrations/_legacy_squashed_20260504/`` for forensic / compliance
replay. Alembic does NOT scan that subdir; only ``versions/``.

Why a synthetic revision id (and not one of the 8 head revisions)
-----------------------------------------------------------------
The SCP squash reused the prior chain head as the new revision id
so existing prod DBs at that revision auto-recognised themselves as
at-head. TMS has 8 heads, no single one captures all the schema —
the dump is the *union* of all 8 branches. Picking one would leave
the other 7 looking divergent. Using a synthetic
``20260504_tms_baseline_squash`` makes it explicit: this revision
is the squash itself, and existing prod DBs need a one-time
``alembic stamp`` to collapse their 8 head rows into one.
"""
from __future__ import annotations

from pathlib import Path

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260504_tms_baseline_squash"
down_revision = None  # New chain root after squash.
branch_labels = None
depends_on = None


# Resolve relative to this file: migrations/versions/<this>.py →
# migrations/baseline_schema.sql
_BASELINE_SQL_PATH = Path(__file__).parent.parent / "baseline_schema.sql"


def upgrade() -> None:
    """Apply the baseline schema dump.

    Three steps in order (mirrors SCP squash design):

    1. Ensure ``alembic_version`` exists and is wide enough. The
       dump excludes alembic_version (--exclude-table); Alembic
       auto-creates it as ``VARCHAR(32)``, but TMS revision ids
       can exceed 32 chars (the SCP squash hit 34). Widen to 128.
    2. Apply ``baseline_schema.sql``.
    3. Reset ``search_path`` to ``public, pg_temp``. The dump
       emits ``SET search_path = ''`` near the top so its own
       schema-qualified statements are unambiguous; that empty
       search_path persists on the connection until reset, which
       would break Alembic's unqualified post-upgrade INSERT.
    """
    # 1. alembic_version table — ensure exists, wide enough.
    op.execute(
        "CREATE TABLE IF NOT EXISTS alembic_version ("
        "  version_num VARCHAR(128) NOT NULL, "
        "  CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)"
        ")"
    )
    op.execute(
        "ALTER TABLE alembic_version "
        "ALTER COLUMN version_num TYPE VARCHAR(128)"
    )

    # 2. Apply the schema dump.
    sql = _BASELINE_SQL_PATH.read_text()
    if not sql.strip():
        raise RuntimeError(
            f"Baseline schema dump is empty: {_BASELINE_SQL_PATH}. "
            "Re-run `pg_dump --schema-only --no-owner --no-acl "
            "--exclude-table=alembic_version` against a fully-migrated "
            "TMS database to regenerate it."
        )
    op.execute(sql)

    # 3. Restore search_path so Alembic's post-upgrade INSERT into
    #    alembic_version (unqualified) finds the table.
    op.execute("SET search_path TO public, pg_temp")


def downgrade() -> None:
    """No-op — see SCP equivalent for rationale.

    Cannot downgrade past a squashed baseline. To rewind, use
    ``DROP DATABASE`` + restore from backup.
    """
    raise NotImplementedError(
        "Cannot downgrade past the baseline squash. Use "
        "DROP DATABASE + restore from backup if you need to rewind."
    )
