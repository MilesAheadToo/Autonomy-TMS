"""Add d365_staging and odoo_staging PostgreSQL schemas

Revision ID: 20260318_d365_odoo_stg
Revises: 20260318_erp_conn
Create Date: 2026-03-18
"""
from alembic import op

revision = "20260318_d365_odoo_stg"
down_revision = "20260318_erp_conn"
branch_labels = None
depends_on = None


def upgrade():
    # Create schemas (sap_staging already exists from prior migration)
    op.execute("CREATE SCHEMA IF NOT EXISTS d365_staging")
    op.execute("CREATE SCHEMA IF NOT EXISTS odoo_staging")

    # ── D365 staging tables ──────────────────────────────────────────────
    op.execute("""
        CREATE TABLE d365_staging.extraction_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            connection_id INTEGER REFERENCES erp_connections(id) ON DELETE SET NULL,
            erp_variant VARCHAR(30) NOT NULL,
            extraction_date DATE NOT NULL,
            source_method VARCHAR(20) NOT NULL,
            data_area_id VARCHAR(10),
            master_tables INTEGER DEFAULT 0,
            master_rows INTEGER DEFAULT 0,
            transaction_tables INTEGER DEFAULT 0,
            transaction_rows INTEGER DEFAULT 0,
            cdc_tables INTEGER DEFAULT 0,
            cdc_rows INTEGER DEFAULT 0,
            status VARCHAR(20) DEFAULT 'pending',
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            config_id INTEGER REFERENCES supply_chain_configs(id) ON DELETE SET NULL,
            build_summary JSONB,
            delta_summary JSONB,
            errors JSONB,
            warnings JSONB,
            created_at TIMESTAMP NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_d365_ext_tenant ON d365_staging.extraction_runs(tenant_id, extraction_date)")

    op.execute("""
        CREATE TABLE d365_staging.rows (
            id BIGSERIAL PRIMARY KEY,
            extraction_id UUID NOT NULL REFERENCES d365_staging.extraction_runs(id) ON DELETE CASCADE,
            tenant_id INTEGER NOT NULL,
            d365_entity VARCHAR(60) NOT NULL,
            data_category VARCHAR(20) NOT NULL,
            row_data JSONB NOT NULL,
            row_hash VARCHAR(32) NOT NULL,
            business_key VARCHAR(200),
            is_staged BOOLEAN DEFAULT FALSE,
            staged_at TIMESTAMP,
            staging_error TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_d365_rows_ext ON d365_staging.rows(extraction_id)")
    op.execute("CREATE INDEX ix_d365_rows_tbl ON d365_staging.rows(tenant_id, d365_entity, extraction_id)")
    op.execute("CREATE INDEX ix_d365_rows_bk ON d365_staging.rows(tenant_id, d365_entity, business_key)")

    op.execute("""
        CREATE TABLE d365_staging.table_schemas (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            d365_entity VARCHAR(60) NOT NULL,
            columns JSONB NOT NULL,
            key_fields JSONB NOT NULL,
            data_category VARCHAR(20) NOT NULL,
            row_count INTEGER DEFAULT 0,
            first_seen TIMESTAMP DEFAULT now(),
            last_seen TIMESTAMP DEFAULT now(),
            UNIQUE(tenant_id, d365_entity)
        )
    """)

    # ── Odoo staging tables ──────────────────────────────────────────────
    op.execute("""
        CREATE TABLE odoo_staging.extraction_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            connection_id INTEGER REFERENCES erp_connections(id) ON DELETE SET NULL,
            erp_variant VARCHAR(30) NOT NULL,
            extraction_date DATE NOT NULL,
            source_method VARCHAR(20) NOT NULL,
            odoo_database VARCHAR(100),
            master_tables INTEGER DEFAULT 0,
            master_rows INTEGER DEFAULT 0,
            transaction_tables INTEGER DEFAULT 0,
            transaction_rows INTEGER DEFAULT 0,
            cdc_tables INTEGER DEFAULT 0,
            cdc_rows INTEGER DEFAULT 0,
            status VARCHAR(20) DEFAULT 'pending',
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            config_id INTEGER REFERENCES supply_chain_configs(id) ON DELETE SET NULL,
            build_summary JSONB,
            delta_summary JSONB,
            errors JSONB,
            warnings JSONB,
            created_at TIMESTAMP NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_odoo_ext_tenant ON odoo_staging.extraction_runs(tenant_id, extraction_date)")

    op.execute("""
        CREATE TABLE odoo_staging.rows (
            id BIGSERIAL PRIMARY KEY,
            extraction_id UUID NOT NULL REFERENCES odoo_staging.extraction_runs(id) ON DELETE CASCADE,
            tenant_id INTEGER NOT NULL,
            odoo_model VARCHAR(60) NOT NULL,
            data_category VARCHAR(20) NOT NULL,
            row_data JSONB NOT NULL,
            row_hash VARCHAR(32) NOT NULL,
            business_key VARCHAR(200),
            is_staged BOOLEAN DEFAULT FALSE,
            staged_at TIMESTAMP,
            staging_error TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_odoo_rows_ext ON odoo_staging.rows(extraction_id)")
    op.execute("CREATE INDEX ix_odoo_rows_tbl ON odoo_staging.rows(tenant_id, odoo_model, extraction_id)")
    op.execute("CREATE INDEX ix_odoo_rows_bk ON odoo_staging.rows(tenant_id, odoo_model, business_key)")

    op.execute("""
        CREATE TABLE odoo_staging.table_schemas (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            odoo_model VARCHAR(60) NOT NULL,
            columns JSONB NOT NULL,
            key_fields JSONB NOT NULL,
            data_category VARCHAR(20) NOT NULL,
            row_count INTEGER DEFAULT 0,
            first_seen TIMESTAMP DEFAULT now(),
            last_seen TIMESTAMP DEFAULT now(),
            UNIQUE(tenant_id, odoo_model)
        )
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS odoo_staging.table_schemas")
    op.execute("DROP TABLE IF EXISTS odoo_staging.rows")
    op.execute("DROP TABLE IF EXISTS odoo_staging.extraction_runs")
    op.execute("DROP TABLE IF EXISTS d365_staging.table_schemas")
    op.execute("DROP TABLE IF EXISTS d365_staging.rows")
    op.execute("DROP TABLE IF EXISTS d365_staging.extraction_runs")
    op.execute("DROP SCHEMA IF EXISTS odoo_staging")
    op.execute("DROP SCHEMA IF EXISTS d365_staging")
