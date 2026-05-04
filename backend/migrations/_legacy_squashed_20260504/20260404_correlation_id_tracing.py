"""Add correlation_id to all powell decision tables for end-to-end tracing

Revision ID: 20260404_correlation_id
Revises: 20260404_forecast_baseline
Create Date: 2026-04-04

Adds correlation_id (UUID) column to all powell_*_decisions tables
for end-to-end decision chain tracing: CDC event → HiveSignal →
TRM decision → MCP write-back → reversal.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260404_correlation_id'
down_revision: Union[str, None] = '20260404_forecast_baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POWELL_TABLES = [
    "powell_atp_allocation_decisions",
    "powell_po_decisions",
    "powell_mo_decisions",
    "powell_to_decisions",
    "powell_inventory_rebalancing_decisions",
    "powell_quality_decisions",
    "powell_maintenance_decisions",
    "powell_subcontracting_decisions",
    "powell_order_tracking_decisions",
    "powell_buffer_decisions",
    "powell_forecast_adjustment_decisions",
    "powell_forecast_baseline_decisions",
]


def upgrade() -> None:
    for table in POWELL_TABLES:
        try:
            op.add_column(table, sa.Column('correlation_id', sa.String(36), nullable=True))
            op.create_index(f'ix_{table}_corr_id', table, ['correlation_id'])
        except Exception:
            pass  # Column may already exist on newer tables


def downgrade() -> None:
    for table in POWELL_TABLES:
        try:
            op.drop_index(f'ix_{table}_corr_id', table)
            op.drop_column(table, 'correlation_id')
        except Exception:
            pass
