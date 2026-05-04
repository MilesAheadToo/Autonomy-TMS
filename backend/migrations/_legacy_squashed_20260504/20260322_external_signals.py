"""External Signal Intelligence tables.

Outside-in planning data from public APIs (FRED, Open-Meteo, EIA, GDELT, etc.)
for Azirella chat context injection.

Revision ID: ext_signal_001
Revises: (latest)
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


revision = "ext_signal_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # External Signal Sources (tenant-configurable)
    op.create_table(
        "external_signal_sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_key", sa.String(50), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("source_params", JSON(), nullable=True),
        sa.Column("industry_tags", JSON(), nullable=True),
        sa.Column("region_tags", JSON(), nullable=True),
        sa.Column("product_tags", JSON(), nullable=True),
        sa.Column("refresh_cadence", sa.String(20), default="daily"),
        sa.Column("last_refresh_at", sa.DateTime(), nullable=True),
        sa.Column("last_refresh_status", sa.String(20), nullable=True),
        sa.Column("last_refresh_error", sa.Text(), nullable=True),
        sa.Column("signals_collected", sa.Integer(), default=0),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "source_key", "config_id", name="uq_ext_signal_source_tenant_key"),
    )
    op.create_index("ix_ext_signal_source_tenant", "external_signal_sources", ["tenant_id"])
    op.create_index("ix_ext_signal_source_active", "external_signal_sources", ["is_active"])

    # External Signals (individual data points)
    op.create_table(
        "external_signals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("supply_chain_configs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("external_signal_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_key", sa.String(50), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("signal_type", sa.String(100), nullable=False),
        sa.Column("signal_key", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("raw_value", sa.Float(), nullable=True),
        sa.Column("raw_unit", sa.String(50), nullable=True),
        sa.Column("change_pct", sa.Float(), nullable=True),
        sa.Column("change_direction", sa.String(10), nullable=True),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("reference_period", sa.String(50), nullable=True),
        sa.Column("previous_value", sa.Float(), nullable=True),
        sa.Column("relevance_score", sa.Float(), default=0.5, nullable=False),
        sa.Column("urgency_score", sa.Float(), default=0.3, nullable=False),
        sa.Column("magnitude_score", sa.Float(), default=0.3, nullable=False),
        sa.Column("affected_trm_types", JSON(), nullable=True),
        sa.Column("planning_layer", sa.String(20), nullable=True),
        sa.Column("affected_product_tags", JSON(), nullable=True),
        sa.Column("affected_region_tags", JSON(), nullable=True),
        sa.Column("embedding_text", sa.Text(), nullable=True),
        sa.Column("is_embedded", sa.Boolean(), default=False, nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "signal_key", name="uq_ext_signal_key"),
    )
    op.create_index("ix_ext_signal_tenant", "external_signals", ["tenant_id"])
    op.create_index("ix_ext_signal_category", "external_signals", ["category"])
    op.create_index("ix_ext_signal_date", "external_signals", ["signal_date"])
    op.create_index("ix_ext_signal_active", "external_signals", ["is_active", "tenant_id"])
    op.create_index("ix_ext_signal_source", "external_signals", ["source_id"])
    op.create_index("ix_ext_signal_relevance", "external_signals", ["tenant_id", "relevance_score"])


def downgrade() -> None:
    op.drop_table("external_signals")
    op.drop_table("external_signal_sources")
