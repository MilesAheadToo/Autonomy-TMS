"""Add model_checkpoints and training_datasets tables (SOC II)

Revision ID: 20260319_model_ckpt
Revises: 20260319_gnn_vert_urg
Create Date: 2026-03-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260319_model_ckpt"
down_revision = "20260319_gnn_vert_urg"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_checkpoints",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        # Tenant isolation (SOC II)
        sa.Column("tenant_id", sa.Integer(),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("config_id", sa.Integer(),
                  sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=False),
        # Model identity
        sa.Column("model_type", sa.String(50), nullable=False),
        sa.Column("site_key", sa.String(100), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        # Storage
        sa.Column("storage_backend", sa.String(20), nullable=False, server_default="filesystem"),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("file_hash", sa.String(64), nullable=True),
        # Model metadata
        sa.Column("model_class", sa.String(100), nullable=True),
        sa.Column("state_dim", sa.Integer(), nullable=True),
        sa.Column("parameter_count", sa.BigInteger(), nullable=True),
        sa.Column("training_phase", sa.String(30), nullable=True),
        sa.Column("training_metadata", JSONB(), nullable=True),
        # Lifecycle
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_best", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("superseded_by", sa.BigInteger(),
                  sa.ForeignKey("model_checkpoints.id", ondelete="SET NULL"), nullable=True),
        # Audit (SOC II CC7.1)
        sa.Column("created_by", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ckpt_tenant_config", "model_checkpoints", ["tenant_id", "config_id"])
    op.create_index("ix_ckpt_model_site", "model_checkpoints", ["config_id", "model_type", "site_key"])
    op.create_index("ix_ckpt_active", "model_checkpoints",
                    ["config_id", "model_type", "site_key", "is_active"])
    op.create_unique_constraint("uq_ckpt_version", "model_checkpoints",
                                ["config_id", "model_type", "site_key", "version"])

    op.create_table(
        "training_datasets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("config_id", sa.Integer(),
                  sa.ForeignKey("supply_chain_configs.id", ondelete="CASCADE"), nullable=False),
        # Dataset identity
        sa.Column("dataset_type", sa.String(50), nullable=False),
        sa.Column("model_type", sa.String(50), nullable=True),
        # Storage
        sa.Column("storage_backend", sa.String(20), nullable=False, server_default="postgresql"),
        sa.Column("storage_reference", sa.String(500), nullable=True),
        # Provenance
        sa.Column("record_count", sa.BigInteger(), nullable=True),
        sa.Column("date_range_start", sa.DateTime(), nullable=True),
        sa.Column("date_range_end", sa.DateTime(), nullable=True),
        sa.Column("source_description", sa.Text(), nullable=True),
        # Linked checkpoint
        sa.Column("checkpoint_id", sa.BigInteger(),
                  sa.ForeignKey("model_checkpoints.id", ondelete="SET NULL"), nullable=True),
        # Data quality
        sa.Column("data_hash", sa.String(64), nullable=True),
        sa.Column("quality_metrics", JSONB(), nullable=True),
        # Security classification (SOC II)
        sa.Column("contains_customer_data", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("data_classification", sa.String(30), nullable=False, server_default="'confidential'"),
        # Audit
        sa.Column("created_by", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_tds_tenant_config", "training_datasets", ["tenant_id", "config_id"])
    op.create_index("ix_tds_type", "training_datasets", ["dataset_type", "model_type"])

    # Enable RLS on both tables (SOC II CC6.1)
    op.execute("ALTER TABLE model_checkpoints ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE training_datasets ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.execute("ALTER TABLE training_datasets DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE model_checkpoints DISABLE ROW LEVEL SECURITY")
    op.drop_table("training_datasets")
    op.drop_table("model_checkpoints")
