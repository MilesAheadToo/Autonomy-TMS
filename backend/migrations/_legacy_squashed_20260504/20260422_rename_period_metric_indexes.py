"""Rename period_metric indexes idx_round_metric_* → idx_period_metric_*

Finishes the rename-cascade started by 20260417_terminology_rename, which
renamed the `round_metric` TABLE to `period_metric` but left its indexes
with their old `idx_round_metric_*` names. The model declares
`idx_period_metric_*`; this migration aligns the DB with the model.

Three indexes are renamed:

- idx_round_metric_scenario       → idx_period_metric_scenario
- idx_round_metric_site           → idx_period_metric_site
- idx_round_metric_scenario_user  → idx_period_metric_scenario_user

The fourth model-declared index (`idx_period_metric_period`) was already
correctly named and needs no change.

The `20260127_order_tracking` migration created two additional indexes
(`idx_round_metric_order`, `idx_round_metric_phase`) that are NOT
declared on the current model — those are intentionally left alone.
Removing them is out of scope for this migration.

Idempotent via pg_indexes lookup — safe on:
- Fresh DBs that created indexes directly from the new model names.
- Existing DBs with the old names.
- Re-runs after partial renames.

Revision ID: 20260422_period_idx
Revises: (no dependency — idempotent, information_schema guarded)
Create Date: 2026-04-22

Companion to Core commit c3f275d (MIGRATION_REGISTER 1.12) and TMS
commit b5b05337 (Period rename adoption).
"""
from alembic import op
import sqlalchemy as sa


# ── Alembic identifiers ───────────────────────────────────────────────────
revision = "20260422_period_idx"
down_revision = None
branch_labels = None
depends_on = None


_RENAMES = [
    ("idx_round_metric_scenario",      "idx_period_metric_scenario"),
    ("idx_round_metric_site",          "idx_period_metric_site"),
    ("idx_round_metric_scenario_user", "idx_period_metric_scenario_user"),
]

_TABLE = "period_metric"


def _table_exists(bind, table: str) -> bool:
    return bool(bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = :t"
        ),
        {"t": table},
    ).scalar())


def _index_exists(bind, table: str, index: str) -> bool:
    return bool(bind.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname = current_schema() "
            "  AND tablename = :t AND indexname = :i"
        ),
        {"t": table, "i": index},
    ).scalar())


def _rename_index_if_needed(old: str, new: str) -> None:
    """Rename `old` → `new` iff `old` exists and `new` does not.

    Silent no-op when the index is already renamed, both exist
    (anomaly — leave the old alone), or neither exists (fresh DB
    where create_all() will materialise the new name directly).
    """
    bind = op.get_bind()
    if not _table_exists(bind, _TABLE):
        return
    if _index_exists(bind, _TABLE, old) and not _index_exists(bind, _TABLE, new):
        op.execute(f'ALTER INDEX "{old}" RENAME TO "{new}"')


def upgrade():
    for old, new in _RENAMES:
        _rename_index_if_needed(old, new)


def downgrade():
    for old, new in _RENAMES:
        # Reverse: rename new → old under the same guard.
        _rename_index_if_needed(new, old)
