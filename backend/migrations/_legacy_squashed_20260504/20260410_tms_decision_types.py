"""Add 11 TMS decision types to decision_type_enum

Revision ID: 20260410_tms_02
Revises: 20260409_tms_01
Create Date: 2026-04-10 08:00:00.000000

Adds 11 new TMS-specific values to the decision_type_enum so that the
AgentDecision table can store decisions from all 11 TMS TRM agents.

This is required for:
- The 11 TMS frontend worklist pages to find their decisions
- The Decision Stream to surface TMS agent recommendations
- The TMS decision seeder to populate demo data
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260410_tms_02'
down_revision = '20260409_tms_01'
branch_labels = None
depends_on = None


# New enum values to add (must match DecisionType enum in models/decision_tracking.py)
NEW_TMS_VALUES = [
    'capacity_promise',
    'shipment_tracking',
    'demand_sensing',
    'capacity_buffer',
    'exception_management',
    'freight_procurement',
    'broker_routing',
    'dock_scheduling',
    'load_build',
    'intermodal_transfer',
    'equipment_reposition',
]


def upgrade():
    """Add TMS values to existing decision_type_enum.

    PostgreSQL requires ALTER TYPE ... ADD VALUE for adding enum values.
    Each ADD VALUE must be in its own transaction (or use IF NOT EXISTS).
    """
    # Get raw connection so we can run outside the migration transaction
    connection = op.get_bind()

    for value in NEW_TMS_VALUES:
        # IF NOT EXISTS makes this idempotent (PostgreSQL 9.6+)
        connection.exec_driver_sql(
            f"ALTER TYPE decision_type_enum ADD VALUE IF NOT EXISTS '{value}'"
        )


def downgrade():
    """Removing enum values requires recreating the enum type.

    This is destructive and complex (must update all referencing columns).
    For dev/staging, use:
        DROP TYPE decision_type_enum CASCADE; -- and recreate
    Production downgrade not supported for safety.
    """
    # No-op: enum value removal is not safe in production
    pass
