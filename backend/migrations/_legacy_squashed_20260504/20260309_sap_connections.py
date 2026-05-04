"""Create sap_connections table

Revision ID: 20260309_sap_conn
Revises: 20260308_gnn_reason
Create Date: 2026-03-09

Persists SAP connection configurations to the database instead of
in-memory storage. Supports OData, RFC, CSV, and IDoc connection
methods for S/4HANA, APO, ECC, and BW systems.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260309_sap_conn"
down_revision = "20260308_gnn_reason"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sap_connections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id", sa.Integer(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # SAP System Identity
        sa.Column("system_type", sa.String(20), nullable=False, server_default="s4hana"),
        sa.Column("sid", sa.String(10), nullable=True),
        # Connection Method
        sa.Column("connection_method", sa.String(20), nullable=False, server_default="odata"),
        # Network / Host
        sa.Column("hostname", sa.String(255), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("use_ssl", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("ssl_verify", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # RFC-specific
        sa.Column("ashost", sa.String(255), nullable=True),
        sa.Column("sysnr", sa.String(5), nullable=True),
        # SAP Login
        sa.Column("client", sa.String(5), nullable=True),
        sa.Column("sap_user", sa.String(50), nullable=True),
        sa.Column("sap_password_encrypted", sa.Text(), nullable=True),
        sa.Column("language", sa.String(5), nullable=True, server_default="EN"),
        # OData-specific
        sa.Column("odata_base_path", sa.String(500), nullable=True),
        # CSV-specific
        sa.Column("csv_directory", sa.String(500), nullable=True),
        sa.Column("csv_pattern", sa.String(100), nullable=True),
        # SAP Router
        sa.Column("sap_router_string", sa.String(500), nullable=True),
        # Cloud Connector
        sa.Column("cloud_connector_location_id", sa.String(100), nullable=True),
        # Status
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_validated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_validated_at", sa.DateTime(), nullable=True),
        sa.Column("validation_message", sa.Text(), nullable=True),
        # Metadata
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )


def downgrade():
    op.drop_table("sap_connections")
