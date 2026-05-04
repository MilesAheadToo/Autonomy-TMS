"""Add experiential_knowledge table

Structured behavioral knowledge entities elevated from override patterns.
Feeds into RL pipeline via state augmentation, reward shaping, conditional CDT,
and simulation modifiers. GENUINE vs COMPENSATING classification per Alicke.

Revision ID: 20260323_ek
Revises: 20260322_site_plan_cfg
"""

from alembic import op
import sqlalchemy as sa

try:
    from pgvector.sqlalchemy import Vector
    HAS_PGVECTOR = True
except ImportError:
    HAS_PGVECTOR = False

revision = "20260323_ek"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "experiential_knowledge",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=False),

        # Entity scope
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_ids", sa.JSON(), nullable=False),

        # Pattern classification
        sa.Column("pattern_type", sa.String(80), nullable=False),

        # Conditions and effect
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("effect", sa.JSON(), nullable=False),

        # Confidence
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),

        # Knowledge type (GENUINE / COMPENSATING)
        sa.Column("knowledge_type", sa.String(20), nullable=True),
        sa.Column("knowledge_type_rationale", sa.Text(), nullable=True),

        # Source
        sa.Column("source_type", sa.String(30), nullable=False),

        # Evidence trail
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_user_ids", sa.JSON(), nullable=False, server_default="[]"),

        # TRM routing
        sa.Column("trm_types_affected", sa.JSON(), nullable=False, server_default="[]"),

        # RL integration
        sa.Column("state_feature_names", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("reward_shaping_bonus", sa.Float(), nullable=False, server_default="0.05"),
        sa.Column("cdt_uncertainty_multiplier", sa.Float(), nullable=False, server_default="1.0"),

        # Lifecycle
        sa.Column("status", sa.String(20), nullable=False, server_default="CANDIDATE"),
        sa.Column("stale_after_days", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("last_validated_at", sa.DateTime(), nullable=True),
        sa.Column("validated_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("contradiction_id", sa.Integer(), sa.ForeignKey("experiential_knowledge.id", ondelete="SET NULL"), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), sa.ForeignKey("experiential_knowledge.id", ondelete="SET NULL"), nullable=True),
        sa.Column("retired_reason", sa.Text(), nullable=True),

        # RAG
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(768) if HAS_PGVECTOR else sa.JSON(), nullable=True),

        # Timestamps
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # Indexes
    op.create_index("idx_ek_tenant_config", "experiential_knowledge", ["tenant_id", "config_id"])
    op.create_index("idx_ek_tenant_config_status", "experiential_knowledge", ["tenant_id", "config_id", "status"])
    op.create_index("idx_ek_status", "experiential_knowledge", ["status"])
    op.create_index("idx_ek_pattern_type", "experiential_knowledge", ["pattern_type"])
    op.create_index("idx_ek_entity_type", "experiential_knowledge", ["entity_type"])

    # RLS (SOC II compliance)
    op.execute("ALTER TABLE experiential_knowledge ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("experiential_knowledge")
