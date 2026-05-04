"""§3.47 Phase 1 — carrier_tms_profile substrate (additive, no drops).

Revision ID: 20260503_carrier_tms_profile
Revises: 20260501_role_templates
Create Date: 2026-05-03

Background
----------
Per docs/MIGRATION_REGISTER.md §3.47, this migration creates the
``carrier_tms_profile`` table — the TMS-side dispatch / capacity /
onboarding extension to Core's ``Carrier`` identity. Phase 1 is
additive only: creates the new table and backfills it from existing
``carrier`` rows. **No columns are dropped from ``carrier``** — the
TMS-side ``Carrier`` ORM keeps its current shape; downstream code
continues to read dispatch fields off ``carrier`` rows.

Phase 2 (TMS code cutover, separate commit) retargets the 4 known
TMS callsites that read dispatch fields to query
``carrier_tms_profile`` instead. Phase 3 (separate commit + Alembic
migration) drops the duplicate columns from ``carrier``, deletes
the TMS-side ``Carrier`` ORM, and retargets all imports to Core's
``azirella_data_model.settlement.entities.Carrier``.

Schema parity check (column-by-column) is in the §3.47 register
entry. The 18 fields moving to ``carrier_tms_profile`` are:

  code, usdot_safety_rating, modes, equipment_types, service_regions,
  is_hazmat_certified, is_bonded, insurance_limit,
  primary_contact_name, primary_contact_email, primary_contact_phone,
  dispatch_email, dispatch_phone, tracking_api_type, tracking_api_config,
  onboarding_status, onboarding_date, last_shipment_date,
  source, external_identifiers,
  p44_carrier_id, p44_identifier_type, p44_account_group_code,
  p44_account_code, config_id

Plus carrier_id FK + tenant_id + standard timestamps.

Idempotency
-----------
Guarded by ``information_schema.tables`` (matches the established
TMS migration pattern). Re-running on an environment that already
has ``carrier_tms_profile`` is a no-op.

Backfill
--------
``INSERT INTO carrier_tms_profile (...) SELECT (...) FROM carrier``
runs unconditionally inside the upgrade — but the FK target
(``carrier.id``) must exist; `ON CONFLICT (carrier_id) DO NOTHING`
makes the backfill safe to retry.

Downgrade
---------
Drops the table + indexes. ``carrier`` rows keep all their data
(Phase 1 hasn't dropped anything from ``carrier``).
"""
from alembic import op
import sqlalchemy as sa


revision = "20260503_carrier_tms_profile"
down_revision = "20260501_role_templates"
branch_labels = None
depends_on = None


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()

    if _table_exists(conn, "carrier_tms_profile"):
        return

    op.create_table(
        "carrier_tms_profile",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "carrier_id", sa.Integer,
            sa.ForeignKey("carrier.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column(
            "tenant_id", sa.Integer,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(100)),
        sa.Column("usdot_safety_rating", sa.String(20)),
        sa.Column("modes", sa.JSON),
        sa.Column("equipment_types", sa.JSON),
        sa.Column("service_regions", sa.JSON),
        sa.Column("is_hazmat_certified", sa.Boolean, server_default="false"),
        sa.Column("is_bonded", sa.Boolean, server_default="false"),
        sa.Column("insurance_limit", sa.Double),
        sa.Column("primary_contact_name", sa.String(200)),
        sa.Column("primary_contact_email", sa.String(200)),
        sa.Column("primary_contact_phone", sa.String(50)),
        sa.Column("dispatch_email", sa.String(200)),
        sa.Column("dispatch_phone", sa.String(50)),
        sa.Column("tracking_api_type", sa.String(50)),
        sa.Column("tracking_api_config", sa.JSON),
        sa.Column("onboarding_status", sa.String(20), server_default="PENDING"),
        sa.Column("onboarding_date", sa.Date),
        sa.Column("last_shipment_date", sa.Date),
        sa.Column("source", sa.String(100)),
        sa.Column("external_identifiers", sa.JSON),
        sa.Column("p44_carrier_id", sa.String(100)),
        sa.Column("p44_identifier_type", sa.String(20)),
        sa.Column("p44_account_group_code", sa.String(100)),
        sa.Column("p44_account_code", sa.String(100)),
        sa.Column(
            "config_id", sa.Integer,
            sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "created_at", sa.DateTime,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("updated_at", sa.DateTime),
        sa.UniqueConstraint(
            "tenant_id", "code",
            name="uq_carrier_tms_profile_tenant_code",
        ),
    )
    op.create_index(
        "idx_carrier_tms_profile_tenant_onboarding",
        "carrier_tms_profile",
        ["tenant_id", "onboarding_status"],
    )

    # Backfill from existing `carrier` rows. Safe to retry: the
    # carrier_id UNIQUE constraint + ON CONFLICT DO NOTHING make
    # repeat invocations idempotent. The column list mirrors the
    # 18 dispatch fields documented at the top of this file plus
    # carrier_id and timestamps.
    op.execute(
        """
        INSERT INTO carrier_tms_profile (
            carrier_id, tenant_id, code, usdot_safety_rating,
            modes, equipment_types, service_regions,
            is_hazmat_certified, is_bonded, insurance_limit,
            primary_contact_name, primary_contact_email, primary_contact_phone,
            dispatch_email, dispatch_phone,
            tracking_api_type, tracking_api_config,
            onboarding_status, onboarding_date, last_shipment_date,
            source, external_identifiers,
            p44_carrier_id, p44_identifier_type,
            p44_account_group_code, p44_account_code,
            config_id, created_at, updated_at
        )
        SELECT
            c.id, c.tenant_id, c.code, c.usdot_safety_rating,
            c.modes, c.equipment_types, c.service_regions,
            c.is_hazmat_certified, c.is_bonded, c.insurance_limit,
            c.primary_contact_name, c.primary_contact_email, c.primary_contact_phone,
            c.dispatch_email, c.dispatch_phone,
            c.tracking_api_type, c.tracking_api_config,
            c.onboarding_status, c.onboarding_date, c.last_shipment_date,
            c.source, c.external_identifiers,
            c.p44_carrier_id, c.p44_identifier_type,
            c.p44_account_group_code, c.p44_account_code,
            c.config_id, c.created_at, c.updated_at
        FROM carrier c
        ON CONFLICT (carrier_id) DO NOTHING
        """
    )


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "carrier_tms_profile"):
        op.drop_index(
            "idx_carrier_tms_profile_tenant_onboarding",
            table_name="carrier_tms_profile",
        )
        op.drop_table("carrier_tms_profile")
