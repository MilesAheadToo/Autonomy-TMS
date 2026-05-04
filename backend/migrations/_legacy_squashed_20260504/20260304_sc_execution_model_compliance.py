"""Add simulation extension columns missing from SC execution layer.

Adds:
  purchase_order.arrival_round  — round when PO shipment arrives (simulation)
  inv_level.backorder_qty       — unfulfilled demand carried forward (simulation)
  inv_level.safety_stock_qty    — safety stock level at the time of snapshot (simulation)

These are AWS SC data-model extensions (simulation namespace) that the SC
execution layer (sc_execution/) needs to track inventory dynamics correctly.

Revision ID: 20260304_sc_execution_model_compliance
Revises: 20260304_belief_state_tenant_id
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa

revision = "20260304_sc_execution_model_compliance"
down_revision = "20260304_belief_state_tenant_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── purchase_order ─────────────────────────────────────────────────────────
    op.add_column(
        "purchase_order",
        sa.Column("arrival_round", sa.Integer(), nullable=True,
                  comment="Simulation: round when this PO is due to arrive"),
    )
    op.create_index(
        "idx_po_scenario_arrival",
        "purchase_order",
        ["scenario_id", "arrival_round", "status"],
    )

    # ── inv_level ──────────────────────────────────────────────────────────────
    op.add_column(
        "inv_level",
        sa.Column("backorder_qty", sa.Double(), nullable=True,
                  comment="Simulation: unfulfilled demand carried as backorder"),
    )
    op.add_column(
        "inv_level",
        sa.Column("safety_stock_qty", sa.Double(), nullable=True,
                  comment="Simulation: safety stock level (snapshot from inv_policy.ss_quantity)"),
    )

    # Back-fill nulls with 0 so application code can use them without null checks
    op.execute("UPDATE inv_level SET backorder_qty = 0.0 WHERE backorder_qty IS NULL")
    op.execute("UPDATE inv_level SET safety_stock_qty = 0.0 WHERE safety_stock_qty IS NULL")


def downgrade() -> None:
    op.drop_index("idx_po_scenario_arrival", table_name="purchase_order")
    op.drop_column("purchase_order", "arrival_round")
    op.drop_column("inv_level", "backorder_qty")
    op.drop_column("inv_level", "safety_stock_qty")
