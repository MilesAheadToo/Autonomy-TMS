"""Add erp_connections table for generalized ERP integration

Revision ID: 20260318_erp_conn
Revises: 20260318_scenario_events
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa

revision = "20260318_erp_conn"
down_revision = "20260318_scenario_events"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "erp_connections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("erp_type", sa.String(30), nullable=False),
        sa.Column("erp_version", sa.String(50), nullable=True),
        sa.Column("connection_method", sa.String(30), nullable=False, server_default="rest_api"),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("auth_type", sa.String(30), nullable=True),
        sa.Column("auth_credentials_encrypted", sa.Text(), nullable=True),
        sa.Column("csv_directory", sa.String(500), nullable=True),
        sa.Column("csv_pattern", sa.String(100), nullable=True),
        sa.Column("connection_params", sa.JSON(), nullable=True),
        sa.Column("discovered_models", sa.JSON(), nullable=True),
        sa.Column("file_table_mapping", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_validated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_validated_at", sa.DateTime(), nullable=True),
        sa.Column("validation_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )


def downgrade():
    op.drop_table("erp_connections")
