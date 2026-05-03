"""§3.47 Phase 3 — drop TMS-specific columns from carrier; add Core canonical columns.

Revision ID: 20260503_carrier_identity_cleanup
Revises: 20260503_carrier_tms_profile
Create Date: 2026-05-03

Background
----------
Per docs/MIGRATION_REGISTER.md §3.47, this is the second of two
Alembic migrations in the carrier-identity cutover. Phase 1
(``20260503_carrier_tms_profile``) created
``carrier_tms_profile`` and backfilled it from existing ``carrier``
columns. Phase 3 (this migration) drops those now-duplicated
columns from ``carrier`` and adds the Core canonical columns so
the ``carrier`` table matches Core's
``azirella_data_model.settlement.entities.Carrier`` shape exactly.

What the cutover does, in order, inside the upgrade()
transaction:

1. **Add Core columns** (nullable for now to allow backfill):
   ``display_name``, ``status``, ``tax_id``, ``currency``,
   ``payment_terms_days``, ``metadata``.
2. **Backfill** Core columns from existing TMS columns:
   - ``display_name`` ← ``name``
   - ``status`` ← ``CASE WHEN is_active THEN 'ACTIVE' ELSE 'INACTIVE' END``
   - ``currency`` ← ``'USD'`` (constant; TMS Carrier had no currency
     column).
   - ``payment_terms_days`` ← ``30`` (constant; ditto).
   - ``metadata`` ← ``'{}'::json`` (constant; ditto).
3. **Tighten** Core columns to NOT NULL where Core's ORM expects.
4. **Drop the TMS-specific columns** (the 18 dispatch fields plus
   ``code``, ``name``, ``is_active``, ``usdot_safety_rating``).
   Their data lives in ``carrier_tms_profile`` after Phase 1 backfill.
5. **Reshape constraints + indexes**:
   - DROP CONSTRAINT ``uq_carrier_tenant_code`` (depends on ``code``).
   - DROP INDEX ``idx_carrier_tenant_active`` (depends on ``is_active``).
   - ADD UNIQUE CONSTRAINT ``uq_carrier_tenant_scac`` (matches Core).
   - ADD INDEX ``ix_carrier_tenant_status``.
6. **Convert ``carrier_type``** from the PG enum
   ``carrier_type_enum`` to ``VARCHAR(32)`` (matches Core's
   String(32) representation). The enum type itself stays — other
   tables may reference it via ``create_type=False``; it can be
   dropped in a follow-on once nothing else is bound to it.

Pre-production safety: there are 3 test tenants and no live
customers. The user authorised the in-place column drops with no
soak window. If real production data ever shows up before this
ships, this migration must be split into "add+backfill" and "drop"
runs separated by deploy-soak-verify.

Idempotency
-----------
Each step guards against re-application. ``information_schema``
checks for column existence before adding/dropping; the
``has_column`` helper handles both directions.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260503_carrier_identity_cleanup"
down_revision = "20260503_carrier_tms_profile"
branch_labels = None
depends_on = None


def _has_column(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t "
                "AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def _has_constraint(conn, table: str, constraint: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_schema = 'public' AND table_name = :t "
                "AND constraint_name = :c"
            ),
            {"t": table, "c": constraint},
        ).scalar()
    )


def _has_index(conn, table: str, index: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM pg_indexes "
                "WHERE schemaname = 'public' "
                "AND tablename = :t AND indexname = :i"
            ),
            {"t": table, "i": index},
        ).scalar()
    )


# Columns being dropped in step 4. Listed once for symmetry between
# upgrade and downgrade (downgrade re-adds them as nullable).
_TMS_DISPATCH_COLUMNS = [
    "code", "name", "is_active", "usdot_safety_rating",
    "modes", "equipment_types", "service_regions",
    "is_hazmat_certified", "is_bonded", "insurance_limit",
    "primary_contact_name", "primary_contact_email", "primary_contact_phone",
    "dispatch_email", "dispatch_phone",
    "tracking_api_type", "tracking_api_config",
    "onboarding_status", "onboarding_date", "last_shipment_date",
    "source", "external_identifiers",
    "p44_carrier_id", "p44_identifier_type",
    "p44_account_group_code", "p44_account_code",
    "config_id",
]


def upgrade() -> None:
    conn = op.get_bind()

    # ── Step 1 — add Core canonical columns (nullable initially). ──
    if not _has_column(conn, "carrier", "display_name"):
        op.add_column("carrier", sa.Column("display_name", sa.String(255)))
    if not _has_column(conn, "carrier", "status"):
        op.add_column("carrier", sa.Column("status", sa.String(32)))
    if not _has_column(conn, "carrier", "tax_id"):
        op.add_column("carrier", sa.Column("tax_id", sa.String(64)))
    if not _has_column(conn, "carrier", "currency"):
        op.add_column(
            "carrier",
            sa.Column(
                "currency", sa.String(8),
                server_default="USD", nullable=True,
            ),
        )
    if not _has_column(conn, "carrier", "payment_terms_days"):
        op.add_column(
            "carrier",
            sa.Column(
                "payment_terms_days", sa.Integer,
                server_default="30", nullable=True,
            ),
        )
    if not _has_column(conn, "carrier", "metadata"):
        op.add_column(
            "carrier",
            sa.Column(
                "metadata", sa.JSON,
                server_default=sa.text("'{}'::json"), nullable=True,
            ),
        )

    # ── Step 2 — backfill Core columns from TMS columns. ──
    # Only run if the source columns still exist (idempotent re-run
    # protection).
    if _has_column(conn, "carrier", "name"):
        op.execute(
            "UPDATE carrier SET display_name = COALESCE(display_name, name) "
            "WHERE display_name IS NULL"
        )
    if _has_column(conn, "carrier", "is_active"):
        op.execute(
            "UPDATE carrier SET status = COALESCE("
            "  status, "
            "  CASE WHEN is_active THEN 'ACTIVE' ELSE 'INACTIVE' END"
            ") WHERE status IS NULL"
        )
    op.execute(
        "UPDATE carrier SET currency = 'USD' WHERE currency IS NULL"
    )
    op.execute(
        "UPDATE carrier SET payment_terms_days = 30 "
        "WHERE payment_terms_days IS NULL"
    )
    op.execute(
        "UPDATE carrier SET metadata = '{}'::json WHERE metadata IS NULL"
    )

    # ── Step 3 — tighten Core columns to NOT NULL. ──
    op.alter_column(
        "carrier", "display_name", nullable=False,
        existing_type=sa.String(255),
    )
    op.alter_column(
        "carrier", "status", nullable=False,
        existing_type=sa.String(32),
        server_default=sa.text("'ACTIVE'"),
    )
    op.alter_column(
        "carrier", "currency", nullable=False,
        existing_type=sa.String(8),
        server_default=sa.text("'USD'"),
    )
    op.alter_column(
        "carrier", "payment_terms_days", nullable=False,
        existing_type=sa.Integer,
        server_default=sa.text("30"),
    )
    op.alter_column(
        "carrier", "metadata", nullable=False,
        existing_type=sa.JSON,
        server_default=sa.text("'{}'::json"),
    )

    # ── Step 5 (before drop) — drop UC + index that reference cols
    # we're about to drop. ──
    if _has_constraint(conn, "carrier", "uq_carrier_tenant_code"):
        op.drop_constraint(
            "uq_carrier_tenant_code", "carrier", type_="unique",
        )
    if _has_index(conn, "carrier", "idx_carrier_tenant_active"):
        op.drop_index("idx_carrier_tenant_active", table_name="carrier")

    # ── Step 6 — convert carrier_type from PG enum to VARCHAR(32). ──
    # Use the column-type cast pattern; doesn't drop the enum type so
    # other tables still bound to ``carrier_type_enum`` keep working.
    op.execute(
        "ALTER TABLE carrier ALTER COLUMN carrier_type "
        "TYPE VARCHAR(32) USING carrier_type::text"
    )

    # ── Step 4 — drop the TMS-specific columns. ──
    for col in _TMS_DISPATCH_COLUMNS:
        if _has_column(conn, "carrier", col):
            op.drop_column("carrier", col)

    # ── Step 5 (after drop) — add Core's UC + index. ──
    if not _has_constraint(conn, "carrier", "uq_carrier_tenant_scac"):
        op.create_unique_constraint(
            "uq_carrier_tenant_scac", "carrier", ["tenant_id", "scac"],
        )
    if not _has_index(conn, "carrier", "ix_carrier_tenant_status"):
        op.create_index(
            "ix_carrier_tenant_status", "carrier",
            ["tenant_id", "status"],
        )
    if not _has_index(conn, "carrier", "ix_carrier_tenant_id"):
        op.create_index(
            "ix_carrier_tenant_id", "carrier", ["tenant_id"],
        )


def downgrade() -> None:
    """Restore TMS-specific columns. Data is NOT recovered — the
    ``carrier_tms_profile`` rows from Phase 1 have to be re-merged
    by hand if a real rollback is needed. Pre-production safety
    only.
    """
    conn = op.get_bind()

    if _has_index(conn, "carrier", "ix_carrier_tenant_status"):
        op.drop_index("ix_carrier_tenant_status", table_name="carrier")
    if _has_constraint(conn, "carrier", "uq_carrier_tenant_scac"):
        op.drop_constraint(
            "uq_carrier_tenant_scac", "carrier", type_="unique",
        )

    # Re-add the dropped columns (nullable; downgrade is best-effort).
    if not _has_column(conn, "carrier", "code"):
        op.add_column("carrier", sa.Column("code", sa.String(100)))
    if not _has_column(conn, "carrier", "name"):
        op.add_column("carrier", sa.Column("name", sa.String(200)))
    if not _has_column(conn, "carrier", "is_active"):
        op.add_column("carrier", sa.Column("is_active", sa.Boolean))
    for col in _TMS_DISPATCH_COLUMNS[3:]:  # everything after is_active
        if not _has_column(conn, "carrier", col):
            op.add_column("carrier", sa.Column(col, sa.String(255)))
    # carrier_type stays VARCHAR(32) — the PG enum type still exists
    # but the column isn't bound back to it.
    if not _has_constraint(conn, "carrier", "uq_carrier_tenant_code"):
        op.create_unique_constraint(
            "uq_carrier_tenant_code", "carrier", ["tenant_id", "code"],
        )

    # Drop the Core columns.
    for col in (
        "display_name", "status", "tax_id",
        "currency", "payment_terms_days", "metadata",
    ):
        if _has_column(conn, "carrier", col):
            op.drop_column("carrier", col)
