"""Add simulation extension columns to transfer_order.

Adds:
  transfer_order.source_po_id             — link TO to originating PO
  transfer_order.source_participant_round_id — DAG: bidirectional link to ParticipantRound

These columns exist in the ORM model but were never migrated.

Revision ID: 20260304_transfer_order_sim_cols
Revises: 20260304_sc_execution_model_compliance, 20260304_aws_sc_compliance, 20260228_escalation_arbiter
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa

revision = "20260304_transfer_order_sim_cols"
down_revision = (
    "20260304_sc_execution_model_compliance",
    "20260304_aws_sc_compliance",
    "20260228_escalation_arbiter",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Check if columns already exist before adding (idempotent)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {c["name"] for c in inspector.get_columns("transfer_order")}

    if "source_po_id" not in existing:
        op.add_column(
            "transfer_order",
            sa.Column(
                "source_po_id",
                sa.Integer(),
                sa.ForeignKey("purchase_order.id", ondelete="SET NULL"),
                nullable=True,
                comment="Simulation: link TO to originating PO",
            ),
        )
        op.create_index(
            "idx_to_source_po",
            "transfer_order",
            ["source_po_id"],
        )

    if "source_participant_round_id" not in existing:
        op.add_column(
            "transfer_order",
            sa.Column(
                "source_participant_round_id",
                sa.Integer(),
                nullable=True,
                comment="DAG: bidirectional link to ParticipantRound (Phase 1)",
            ),
        )
        op.create_index(
            "idx_to_participant_round",
            "transfer_order",
            ["source_participant_round_id"],
        )


def downgrade() -> None:
    op.drop_index("idx_to_participant_round", table_name="transfer_order")
    op.drop_column("transfer_order", "source_participant_round_id")
    op.drop_index("idx_to_source_po", table_name="transfer_order")
    op.drop_column("transfer_order", "source_po_id")
