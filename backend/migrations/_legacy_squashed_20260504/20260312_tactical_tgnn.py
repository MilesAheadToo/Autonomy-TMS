"""Add tactical tGNN columns and checkpoint tracking table

Revision ID: 20260312_tactical_tgnn
Revises: 20260308_gnn_reason
Create Date: 2026-03-12

Changes:
1. Add lateral_iteration, lateral_convergence_score, domain_tgnn_source
   columns to gnn_directive_reviews to track which of the three parallel
   tGNNs produced each directive and how many lateral iterations were needed.
2. Create tactical_tgnn_checkpoints table for tracking trained model
   checkpoints per config_id and domain (demand_planning, supply_planning,
   inventory_optim).
"""

revision = "20260312_tactical_tgnn"
down_revision = "20260308_gnn_reason"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def _table_exists(conn, table_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables"
            "  WHERE table_name = :t"
            ")"
        ),
        {"t": table_name},
    )
    return bool(result.scalar())


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.columns"
            "  WHERE table_name = :t AND column_name = :c"
            ")"
        ),
        {"t": table_name, "c": column_name},
    )
    return bool(result.scalar())


def upgrade() -> None:
    conn = op.get_bind()

    # --- 1. Extend gnn_directive_reviews ---
    if _table_exists(conn, "gnn_directive_reviews"):
        if not _column_exists(conn, "gnn_directive_reviews", "lateral_iteration"):
            op.add_column(
                "gnn_directive_reviews",
                sa.Column(
                    "lateral_iteration",
                    sa.Integer(),
                    nullable=True,
                    comment="Number of lateral iterations run by TacticalHiveCoordinator (1 or 2)",
                ),
            )
        if not _column_exists(conn, "gnn_directive_reviews", "lateral_convergence_score"):
            op.add_column(
                "gnn_directive_reviews",
                sa.Column(
                    "lateral_convergence_score",
                    sa.Float(),
                    nullable=True,
                    comment="Max absolute signal delta between lateral iterations for this site",
                ),
            )
        if not _column_exists(conn, "gnn_directive_reviews", "domain_tgnn_source"):
            op.add_column(
                "gnn_directive_reviews",
                sa.Column(
                    "domain_tgnn_source",
                    sa.String(length=30),
                    nullable=True,
                    comment="Domain tGNN that produced this directive: demand_planning | supply_planning | inventory_optim | tactical_hive",
                ),
            )

    # --- 2. Create tactical_tgnn_checkpoints ---
    if not _table_exists(conn, "tactical_tgnn_checkpoints"):
        op.create_table(
            "tactical_tgnn_checkpoints",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "config_id",
                sa.Integer(),
                sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
                comment="Supply chain config this checkpoint was trained for",
            ),
            sa.Column(
                "domain",
                sa.String(length=20),
                nullable=False,
                comment="Domain: demand_planning | supply_planning | inventory_optim",
            ),
            sa.Column(
                "checkpoint_path",
                sa.String(length=500),
                nullable=False,
                comment="Absolute path to the .pt checkpoint file",
            ),
            sa.Column(
                "trained_at",
                sa.DateTime(),
                nullable=True,
                comment="Timestamp when training completed",
            ),
            sa.Column(
                "num_sites",
                sa.Integer(),
                nullable=True,
                comment="Number of sites the model was trained on",
            ),
            sa.Column(
                "validation_loss",
                sa.Float(),
                nullable=True,
                comment="Validation loss at checkpoint time",
            ),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
                comment="Whether this is the active checkpoint for this config+domain",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
        )
        # Unique constraint: only one active checkpoint per config+domain
        op.create_index(
            "ix_tactical_tgnn_checkpoints_active",
            "tactical_tgnn_checkpoints",
            ["config_id", "domain", "is_active"],
            unique=False,
        )


def downgrade() -> None:
    conn = op.get_bind()

    # Remove gnn_directive_reviews columns
    if _table_exists(conn, "gnn_directive_reviews"):
        if _column_exists(conn, "gnn_directive_reviews", "domain_tgnn_source"):
            op.drop_column("gnn_directive_reviews", "domain_tgnn_source")
        if _column_exists(conn, "gnn_directive_reviews", "lateral_convergence_score"):
            op.drop_column("gnn_directive_reviews", "lateral_convergence_score")
        if _column_exists(conn, "gnn_directive_reviews", "lateral_iteration"):
            op.drop_column("gnn_directive_reviews", "lateral_iteration")

    # Drop tactical_tgnn_checkpoints
    if _table_exists(conn, "tactical_tgnn_checkpoints"):
        op.drop_index("ix_tactical_tgnn_checkpoints_active", table_name="tactical_tgnn_checkpoints")
        op.drop_table("tactical_tgnn_checkpoints")
