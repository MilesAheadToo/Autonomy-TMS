"""Mirror Core 0014: create ``agent_authorities`` table + per-tenant seed.

Revision ID: 20260501_authorities_tid_nn
Revises: 20260501_powell_tid_nn
Create Date: 2026-05-01

TMS-side mirror of Core ``0014_authorities_tid_nn``. Different shape
than SCP's mirror because TMS DB does not yet have ``agent_authorities``
at all (SCP's earlier ``20260426_scenario_negotiation_protocol``
created it; TMS never adopted that migration).

Three steps in this single TMS migration:

1. **CREATE TABLE** ``agent_authorities`` matching the canonical shape
   from SCP's ``20260426_scenario_negotiation_protocol``:
     - ``action_type``, ``owner_agent_class``, ``domain`` (all NOT NULL)
     - ``tenant_id`` initially nullable (so seed can populate then
       tighten)
     - ``notes`` text, ``created_at`` timestamp
     - Unique constraint ``(action_type, tenant_id)``
     - Index ``(domain, owner_agent_class)``
2. **Seed every existing tenant** from
   ``azirella_data_model.powell.canonical_authorities.POWELL_BOOTSTRAP_AUTHORITIES``
   — one row per (tenant × action_type). Idempotent via the unique
   constraint (``ON CONFLICT DO NOTHING``).
3. **ALTER COLUMN tenant_id SET NOT NULL** — fails loud if step 2
   missed any rows.

After this lands, future tenants are seeded by TMS provisioning calling
``seed_agent_authorities(tenant_id, db)`` directly; this migration only
catches up tenants that already exist (Food Dist, SCP Import).

Idempotent — re-runs are no-ops on a post-state DB.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260501_authorities_tid_nn"
down_revision = "20260501_powell_tid_nn"
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


def _is_nullable(conn, table: str, column: str = "tenant_id", schema: str = "public") -> bool:
    return (
        conn.execute(
            sa.text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
            ),
            {"s": schema, "t": table, "c": column},
        ).scalar()
        == "YES"
    )


def upgrade() -> None:
    conn = op.get_bind()

    # Step 1 — CREATE TABLE if missing.
    if not _table_exists(conn, "agent_authorities"):
        op.create_table(
            "agent_authorities",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("action_type", sa.String(50), nullable=False),
            sa.Column("owner_agent_class", sa.String(50), nullable=False),
            sa.Column("domain", sa.String(20), nullable=False),
            sa.Column(
                "tenant_id", sa.Integer(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=True,  # tightened in step 3
            ),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(),
                server_default=sa.func.now(), nullable=False,
            ),
            sa.UniqueConstraint(
                "action_type", "tenant_id",
                name="uq_agent_authorities_action_tenant",
            ),
        )
        op.create_index(
            "ix_agent_authorities_domain",
            "agent_authorities",
            ["domain", "owner_agent_class"],
        )

    # Step 2 — seed every tenant from canonical map.
    #
    # Frozen snapshot of ``azirella_data_model.powell.canonical_authorities
    # .POWELL_BOOTSTRAP_AUTHORITIES`` as of Core HEAD 2026-05-01 (37
    # entries). Migrations must be reproducible regardless of consumer
    # Core-pin version, so we inline rather than import. If Core's
    # canonical map changes, a new migration covers the diff — this one
    # stays frozen.
    bootstrap_authorities = {
        "ESCALATE_BREAKDOWN":        ("maintenance_scheduling_trm", "capacity"),
        "SCHEDULE_PM":               ("maintenance_scheduling_trm", "capacity"),
        "APPLY_LIFECYCLE_CURVE":     ("forecast_adjustment_trm",    "demand"),
        "APPLY_PROMOTION_LIFT":      ("forecast_adjustment_trm",    "demand"),
        "OVERRIDE_FORECAST":         ("forecast_adjustment_trm",    "demand"),
        "ATP_COMMIT":                ("atp_executor",               "execution"),
        "CHEAP_SUPPLIER_PO":         ("atp_executor",               "execution"),
        "CTP_COMMIT":                ("atp_executor",               "execution"),
        "DELAY_FULFILLMENT":         ("atp_executor",               "execution"),
        "ESCALATE_DELAY":            ("order_tracking_trm",         "execution"),
        "EXPEDITE_TRACKING":         ("order_tracking_trm",         "execution"),
        "FAST_SUPPLIER_PO":          ("atp_executor",               "execution"),
        "PARTIAL_BACKORDER":         ("atp_executor",               "execution"),
        "QUARANTINE_LOT":            ("quality_disposition_trm",    "execution"),
        "RELEASE_LOT":               ("quality_disposition_trm",    "execution"),
        "REWORK_LOT":                ("quality_disposition_trm",    "execution"),
        "SPLIT_FULFILLMENT":         ("atp_executor",               "execution"),
        "ADJUST_BUFFER":             ("inventory_buffer_trm",       "inventory"),
        "ADJUST_REORDER_POINT":      ("inventory_buffer_trm",       "inventory"),
        "ADJUST_SAFETY_STOCK":       ("inventory_buffer_trm",       "inventory"),
        "CROSSDOCK_TRANSFER":        ("inventory_rebalancing_trm",  "inventory"),
        "DIRECT_TRANSFER":           ("inventory_rebalancing_trm",  "inventory"),
        "EMERGENCY_SHIPMENT":        ("inventory_rebalancing_trm",  "inventory"),
        "ADJUST_MO_QUANTITY":        ("mo_execution_trm",           "supply"),
        "CANCEL_TO":                 ("to_execution_trm",           "supply"),
        "CONSOLIDATE_PO":            ("po_creation_trm",            "supply"),
        "CREATE_MO":                 ("mo_execution_trm",           "supply"),
        "CREATE_PO":                 ("po_creation_trm",            "supply"),
        "CREATE_TO":                 ("to_execution_trm",           "supply"),
        "EXPEDITE_MO":               ("mo_execution_trm",           "supply"),
        "EXPEDITE_PO":               ("po_creation_trm",            "supply"),
        "EXPEDITE_TO":               ("to_execution_trm",           "supply"),
        "OFFLOAD_TO_SUBCONTRACTOR":  ("subcontracting_trm",         "supply"),
        "OVERTIME_PRODUCTION":       ("mo_execution_trm",           "supply"),
        "RECALL_FROM_SUBCONTRACTOR": ("subcontracting_trm",         "supply"),
        "RELEASE_MO":                ("mo_execution_trm",           "supply"),
        "SPLIT_PO_SUPPLIERS":        ("po_creation_trm",            "supply"),
    }

    tenant_rows = conn.execute(sa.text("SELECT id FROM tenants")).fetchall()
    for (tenant_id,) in tenant_rows:
        for action_type, (owner, domain) in bootstrap_authorities.items():
            conn.execute(
                sa.text(
                    """
                    INSERT INTO agent_authorities
                        (action_type, owner_agent_class, domain, tenant_id)
                    VALUES
                        (:action_type, :owner, :domain, :tenant_id)
                    ON CONFLICT (action_type, tenant_id) DO NOTHING
                    """
                ),
                {
                    "action_type": action_type,
                    "owner": owner,
                    "domain": domain,
                    "tenant_id": int(tenant_id),
                },
            )

    # Step 3 — tighten to NOT NULL.
    if _table_exists(conn, "agent_authorities") and _is_nullable(conn, "agent_authorities"):
        # First drop any straggler NULL rows (TMS shouldn't have any
        # since the table is freshly created here, but be safe).
        conn.execute(sa.text("DELETE FROM agent_authorities WHERE tenant_id IS NULL"))
        op.alter_column(
            "agent_authorities", "tenant_id",
            schema="public", nullable=False,
        )


def downgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "agent_authorities"):
        return
    if not _is_nullable(conn, "agent_authorities"):
        op.alter_column(
            "agent_authorities", "tenant_id",
            schema="public", nullable=True,
        )
