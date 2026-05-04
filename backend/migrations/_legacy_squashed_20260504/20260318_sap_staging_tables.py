"""Add SAP staging tables and tenant ERP configuration.

New tables:
- sap_extraction_runs: Metadata per extraction batch
- sap_staging_rows: Raw SAP data in JSONB for audit and delta detection
- sap_table_schemas: Column set tracking per SAP table per tenant

New columns on tenants:
- erp_vendor, erp_variant, import_base_dir, export_base_dir, erp_retention_snapshots

Revision ID: 20260318_sap_staging
Revises: acb744466de8
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = '20260318_sap_staging'
down_revision = 'acb744466de8'
branch_labels = None
depends_on = None


def upgrade():
    # --- sap_extraction_runs ---
    op.create_table(
        'sap_extraction_runs',
        sa.Column('id', UUID, primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('connection_id', sa.Integer, sa.ForeignKey('sap_connections.id', ondelete='SET NULL'), nullable=True),
        sa.Column('extraction_date', sa.Date, nullable=False),
        sa.Column('erp_system', sa.String(100), nullable=True),
        sa.Column('source_method', sa.String(20), nullable=False),
        sa.Column('master_tables', sa.Integer, server_default='0'),
        sa.Column('master_rows', sa.Integer, server_default='0'),
        sa.Column('transaction_tables', sa.Integer, server_default='0'),
        sa.Column('transaction_rows', sa.Integer, server_default='0'),
        sa.Column('cdc_tables', sa.Integer, server_default='0'),
        sa.Column('cdc_rows', sa.Integer, server_default='0'),
        sa.Column('user_tables', sa.Integer, server_default='0'),
        sa.Column('user_rows', sa.Integer, server_default='0'),
        sa.Column('status', sa.String(20), server_default='pending'),
        sa.Column('started_at', sa.DateTime, nullable=True),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('csv_directory', sa.String(500), nullable=True),
        sa.Column('manifest', JSONB, nullable=True),
        sa.Column('delta_summary', JSONB, nullable=True),
        sa.Column('errors', JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_extraction_runs_tenant', 'sap_extraction_runs', ['tenant_id', 'extraction_date'])

    # --- sap_staging_rows ---
    op.create_table(
        'sap_staging_rows',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('connection_id', sa.Integer, sa.ForeignKey('sap_connections.id', ondelete='SET NULL'), nullable=True),
        sa.Column('extraction_id', UUID, sa.ForeignKey('sap_extraction_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('extraction_date', sa.Date, nullable=False),
        sa.Column('sap_table', sa.String(40), nullable=False),
        sa.Column('data_category', sa.String(20), nullable=False),
        sa.Column('source_method', sa.String(20), nullable=False),
        sa.Column('row_data', JSONB, nullable=False),
        sa.Column('row_hash', sa.String(32), nullable=False),
        sa.Column('business_key', sa.String(200), nullable=True),
        sa.Column('is_staged', sa.Boolean, server_default='false'),
        sa.Column('staged_at', sa.DateTime, nullable=True),
        sa.Column('staging_errors', JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_staging_tenant_table_date', 'sap_staging_rows', ['tenant_id', 'sap_table', 'extraction_date'])
    op.create_index('ix_staging_extraction', 'sap_staging_rows', ['extraction_id'])
    op.create_index('ix_staging_bkey', 'sap_staging_rows', ['tenant_id', 'sap_table', 'business_key'])
    op.create_index(
        'ix_staging_unstaged', 'sap_staging_rows', ['tenant_id', 'is_staged'],
        postgresql_where=sa.text('NOT is_staged'),
    )

    # --- sap_table_schemas ---
    op.create_table(
        'sap_table_schemas',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sap_table', sa.String(40), nullable=False),
        sa.Column('columns', JSONB, nullable=False),
        sa.Column('column_types', JSONB, nullable=True),
        sa.Column('key_fields', JSONB, nullable=False),
        sa.Column('data_category', sa.String(20), nullable=False),
        sa.Column('first_seen', sa.DateTime, server_default=sa.text('now()')),
        sa.Column('last_seen', sa.DateTime, server_default=sa.text('now()')),
        sa.UniqueConstraint('tenant_id', 'sap_table', name='uq_sap_table_schema'),
    )

    # --- Tenant ERP configuration columns ---
    op.add_column('tenants', sa.Column('erp_vendor', sa.String(30), nullable=True))
    op.add_column('tenants', sa.Column('erp_variant', sa.String(30), nullable=True))
    op.add_column('tenants', sa.Column('import_base_dir', sa.String(500), nullable=True))
    op.add_column('tenants', sa.Column('export_base_dir', sa.String(500), nullable=True))
    op.add_column('tenants', sa.Column('erp_retention_snapshots', sa.Integer, server_default='5', nullable=False))


def downgrade():
    op.drop_column('tenants', 'erp_retention_snapshots')
    op.drop_column('tenants', 'export_base_dir')
    op.drop_column('tenants', 'import_base_dir')
    op.drop_column('tenants', 'erp_variant')
    op.drop_column('tenants', 'erp_vendor')
    op.drop_table('sap_table_schemas')
    op.drop_table('sap_staging_rows')
    op.drop_table('sap_extraction_runs')
