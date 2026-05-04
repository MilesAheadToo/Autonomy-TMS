"""Add stochastic distribution fields to supply chain models

Revision ID: 20260113_stochastic_distributions
Revises: 20260112_order_aggregation
Create Date: 2026-01-13

This migration adds JSON fields to support stochastic distributions for
operational variables (lead times, capacities, yields, demand, etc.).

Changes (Phase 3 Planning Tables Only):
1. ProductionProcess: Add 5 distribution fields (mfg lead time, cycle time, yield, setup, changeover)
2. ProductionCapacity: Add 1 distribution field (capacity)
3. ProductBom: Add 1 distribution field (scrap rate)
4. SourcingRules: Add 1 distribution field (sourcing lead time)
5. VendorLeadTime: Add 1 distribution field (lead time)
6. Forecast: Add 2 distribution fields (demand, forecast error)

Total: 11 fields across 6 existing tables

All fields are NULLABLE for backward compatibility:
- NULL = use deterministic value from existing field (backward compatible)
- JSON = use stochastic distribution

Example distribution JSON:
{
  "type": "normal",
  "mean": 7.0,
  "stddev": 1.5,
  "min": 3.0,
  "max": 12.0,
  "seed": 42
}
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic
revision = '20260113_stochastic_distributions'
down_revision = '20260112_order_aggregation'
branch_labels = None
depends_on = None


def upgrade():
    """Add stochastic distribution fields to existing Phase 3 planning tables"""

    # 1. ProductionProcess: Add 5 distribution fields
    print("Adding distribution fields to production_process...")
    op.add_column('production_process',
                  sa.Column('mfg_lead_time_dist', sa.JSON(), nullable=True,
                           comment='Stochastic distribution for manufacturing lead time'))
    op.add_column('production_process',
                  sa.Column('cycle_time_dist', sa.JSON(), nullable=True,
                           comment='Stochastic distribution for cycle time'))
    op.add_column('production_process',
                  sa.Column('yield_dist', sa.JSON(), nullable=True,
                           comment='Stochastic distribution for yield percentage'))
    op.add_column('production_process',
                  sa.Column('setup_time_dist', sa.JSON(), nullable=True,
                           comment='Stochastic distribution for setup time'))
    op.add_column('production_process',
                  sa.Column('changeover_time_dist', sa.JSON(), nullable=True,
                           comment='Stochastic distribution for changeover time'))

    # 2. ProductionCapacity: Add 1 distribution field
    print("Adding distribution fields to production_capacity...")
    op.add_column('production_capacity',
                  sa.Column('capacity_dist', sa.JSON(), nullable=True,
                           comment='Stochastic distribution for capacity'))

    # 3. ProductBom: Add 1 distribution field
    print("Adding distribution fields to product_bom...")
    op.add_column('product_bom',
                  sa.Column('scrap_rate_dist', sa.JSON(), nullable=True,
                           comment='Stochastic distribution for scrap rate percentage'))

    # 4. SourcingRules: Add 1 distribution field
    print("Adding distribution fields to sourcing_rules...")
    op.add_column('sourcing_rules',
                  sa.Column('sourcing_lead_time_dist', sa.JSON(), nullable=True,
                           comment='Stochastic distribution for sourcing lead time'))

    # 5. VendorLeadTime: Add 1 distribution field
    print("Adding distribution fields to vendor_lead_time...")
    op.add_column('vendor_lead_time',
                  sa.Column('lead_time_dist', sa.JSON(), nullable=True,
                           comment='Stochastic distribution for vendor lead time'))

    # 6. Forecast: Add 2 distribution fields
    print("Adding distribution fields to forecast...")
    op.add_column('forecast',
                  sa.Column('demand_dist', sa.JSON(), nullable=True,
                           comment='Stochastic distribution for demand'))
    op.add_column('forecast',
                  sa.Column('forecast_error_dist', sa.JSON(), nullable=True,
                           comment='Stochastic distribution for forecast error'))

    print("✅ Successfully added 11 stochastic distribution fields across 6 tables")
    print("   - ProductionProcess: 5 fields")
    print("   - ProductionCapacity: 1 field")
    print("   - ProductBom: 1 field")
    print("   - SourcingRules: 1 field")
    print("   - VendorLeadTime: 1 field")
    print("   - Forecast: 2 fields")
    print("")
    print("All fields are NULL by default (backward compatible).")
    print("Set distribution JSON to enable stochastic behavior.")


def downgrade():
    """Remove stochastic distribution fields from all tables"""

    print("Removing distribution fields from all tables...")

    # 6. Forecast: Remove 2 distribution fields
    op.drop_column('forecast', 'forecast_error_dist')
    op.drop_column('forecast', 'demand_dist')

    # 5. VendorLeadTime: Remove 1 distribution field
    op.drop_column('vendor_lead_time', 'lead_time_dist')

    # 4. SourcingRules: Remove 1 distribution field
    op.drop_column('sourcing_rules', 'sourcing_lead_time_dist')

    # 3. ProductBom: Remove 1 distribution field
    op.drop_column('product_bom', 'scrap_rate_dist')

    # 2. ProductionCapacity: Remove 1 distribution field
    op.drop_column('production_capacity', 'capacity_dist')

    # 1. ProductionProcess: Remove 5 distribution fields
    op.drop_column('production_process', 'changeover_time_dist')
    op.drop_column('production_process', 'setup_time_dist')
    op.drop_column('production_process', 'yield_dist')
    op.drop_column('production_process', 'cycle_time_dist')
    op.drop_column('production_process', 'mfg_lead_time_dist')

    print("✅ Successfully removed 11 stochastic distribution fields")
