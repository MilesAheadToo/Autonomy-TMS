"""§3.47 follow-on — drop the orphaned carrier_type_enum PG type.

Revision ID: 20260503_drop_carrier_type_enum
Revises: 20260503_carrier_identity_cleanup
Create Date: 2026-05-03

Background
----------
§3.47 Phase 3 (`20260503_carrier_identity_cleanup`) ran
``ALTER TABLE carrier ALTER COLUMN carrier_type TYPE VARCHAR(32)``
to match Core's settlement schema. That left the
``carrier_type_enum`` PG type itself in place — the cleanup
migration deferred the drop because other tables might still bind
to it via ``create_type=False``.

Audit (2026-05-03): no ORM column anywhere in the codebase still
binds to ``carrier_type_enum``. The Python ``CarrierType`` enum
class in ``app/models/tms_entities.py`` is a ``str``-based
``PyEnum`` (used as a Python-side typed value for callers; values
get serialised as strings into Core's ``Carrier.carrier_type``
``String(32)`` column). The only references to
``carrier_type_enum`` are in historical Alembic files
(``20260409_tms_entities.py``) and the §3.47 cleanup file's
docstring — neither binds the type to a live table column.

This migration drops the orphan PG type. Idempotent
(``DROP TYPE IF EXISTS``).
"""
from alembic import op


revision = "20260503_drop_carrier_type_enum"
down_revision = "20260503_carrier_identity_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF EXISTS is idempotent; safe to re-run on environments where
    # the type was already dropped manually.
    op.execute("DROP TYPE IF EXISTS carrier_type_enum")


def downgrade() -> None:
    # Re-create the type with its original 9 members. Downgrade is
    # best-effort: any column that wants to bind to it again must
    # ALTER its TYPE separately (no live consumer expected).
    op.execute(
        "CREATE TYPE carrier_type_enum AS ENUM ("
        "'ASSET', 'BROKER', 'THREE_PL', 'FOUR_PL', 'OCEAN_LINE', "
        "'AIRLINE', 'RAILROAD', 'COURIER', 'DRAYAGE_CARRIER'"
        ")"
    )
