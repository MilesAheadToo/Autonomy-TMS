"""Remove cost weight columns from tenant_bsc_config and erp fields from tenants.

- Drop holding_cost_weight, backlog_cost_weight, customer_weight,
  operational_weight, strategic_weight, autonomy_threshold from tenant_bsc_config
- Drop erp_vendor, erp_variant from tenants (now in erp_connections table)
- Backfill TenantBscConfig rows for tenants that don't have one

Revision ID: 20260318_tenant_config_cleanup
Revises: 20260326_tenant_bsc_config
Create Date: 2026-03-18
"""

from alembic import op
import sqlalchemy as sa

revision = "20260318_tenant_config_cleanup"
down_revision = "20260326_tenant_bsc_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Backfill: create TenantBscConfig for tenants that don't have one ──
    op.execute("""
        INSERT INTO tenant_bsc_config (tenant_id, urgency_threshold, likelihood_threshold,
                                        benefit_threshold, display_identifiers, updated_at)
        SELECT t.id, 0.65, 0.70, 0.0, 'name', now()
        FROM tenants t
        LEFT JOIN tenant_bsc_config c ON c.tenant_id = t.id
        WHERE c.id IS NULL
    """)

    # ── Backfill: create erp_connections from tenant.erp_vendor/erp_variant ──
    # Only if erp_connections table exists and columns still present
    try:
        op.execute("""
            INSERT INTO erp_connections (tenant_id, name, erp_type, erp_version,
                                          connection_method, is_active, created_at, updated_at)
            SELECT t.id,
                   COALESCE(t.erp_vendor, 'SAP') || ' Connection',
                   LOWER(COALESCE(t.erp_vendor, 'sap')),
                   COALESCE(t.erp_variant, 'S4HANA'),
                   'csv',
                   true,
                   now(),
                   now()
            FROM tenants t
            WHERE t.erp_vendor IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM erp_connections e WHERE e.tenant_id = t.id
            )
        """)
    except Exception:
        pass  # erp_connections table may not exist yet or columns already dropped

    # ── Drop removed columns from tenant_bsc_config ──
    for col in [
        "holding_cost_weight", "backlog_cost_weight",
        "customer_weight", "operational_weight", "strategic_weight",
        "autonomy_threshold",
    ]:
        try:
            op.drop_column("tenant_bsc_config", col)
        except Exception:
            pass  # Column may not exist

    # ── Drop removed columns from tenants ──
    for col in ["erp_vendor", "erp_variant"]:
        try:
            op.drop_column("tenants", col)
        except Exception:
            pass  # Column may not exist

    # ── Ensure display_identifiers column exists ──
    try:
        op.add_column(
            "tenant_bsc_config",
            sa.Column("display_identifiers", sa.String(10), nullable=False,
                      server_default="name"),
        )
    except Exception:
        pass  # Column may already exist

    # ── Ensure benefit_threshold column exists ──
    try:
        op.add_column(
            "tenant_bsc_config",
            sa.Column("benefit_threshold", sa.Float(), nullable=False,
                      server_default="0.0"),
        )
    except Exception:
        pass  # Column may already exist


def downgrade() -> None:
    # Re-add removed columns with defaults
    op.add_column("tenant_bsc_config",
                  sa.Column("holding_cost_weight", sa.Float(), server_default="0.5", nullable=False))
    op.add_column("tenant_bsc_config",
                  sa.Column("backlog_cost_weight", sa.Float(), server_default="0.5", nullable=False))
    op.add_column("tenant_bsc_config",
                  sa.Column("customer_weight", sa.Float(), server_default="0.0", nullable=False))
    op.add_column("tenant_bsc_config",
                  sa.Column("operational_weight", sa.Float(), server_default="0.0", nullable=False))
    op.add_column("tenant_bsc_config",
                  sa.Column("strategic_weight", sa.Float(), server_default="0.0", nullable=False))
    op.add_column("tenant_bsc_config",
                  sa.Column("autonomy_threshold", sa.Float(), server_default="0.5", nullable=False))
    op.add_column("tenants",
                  sa.Column("erp_vendor", sa.String(30), nullable=True))
    op.add_column("tenants",
                  sa.Column("erp_variant", sa.String(30), nullable=True))
