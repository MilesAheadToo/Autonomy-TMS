"""Fix site_id FK type mismatch: String → Integer

All site_id foreign key columns were String(100) referencing site.id which is Integer.
This migration converts them to Integer to match the referenced column type.

Affected tables:
- sourcing_rules: from_site_id, to_site_id
- inv_policy: site_id
- inv_level: site_id
- supply_plan: site_id, from_site_id
- forecast: site_id
- production_process: site_id
- shipment: from_site_id, to_site_id
- maintenance_order: site_id
- project_order: site_id
- turnaround_order: from_site_id, to_site_id, refurbishment_site_id
- site_hierarchy_node: site_id

Revision ID: 20260210_fix_site_fk
Create Date: 2026-02-10
"""
from alembic import op
import sqlalchemy as sa

revision = '20260210_fix_site_fk'
down_revision = None
branch_labels = None
depends_on = None

# All columns to convert: (table_name, column_name, nullable)
COLUMNS = [
    ('sourcing_rules', 'from_site_id', True),
    ('sourcing_rules', 'to_site_id', True),
    ('inv_policy', 'site_id', True),
    ('inv_level', 'site_id', True),
    ('supply_plan', 'site_id', True),
    ('supply_plan', 'from_site_id', True),
    ('forecast', 'site_id', True),
    ('production_process', 'site_id', True),
    ('shipment', 'from_site_id', False),
    ('shipment', 'to_site_id', False),
    ('maintenance_order', 'site_id', False),
    ('project_order', 'site_id', False),
    ('turnaround_order', 'from_site_id', False),
    ('turnaround_order', 'to_site_id', False),
    ('turnaround_order', 'refurbishment_site_id', True),
    ('site_hierarchy_node', 'site_id', True),
]


def upgrade():
    for table, column, nullable in COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} "
            f"TYPE INTEGER USING {column}::integer"
        )


def downgrade():
    for table, column, nullable in COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} "
            f"TYPE VARCHAR(100) USING {column}::varchar"
        )
