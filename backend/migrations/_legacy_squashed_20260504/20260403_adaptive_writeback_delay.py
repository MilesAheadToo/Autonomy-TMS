"""Adaptive MCP write-back delay on governance policies

Revision ID: 20260403_writeback_delay
Revises: 20260403_mcp_infra
Create Date: 2026-04-03

Adds adaptive write-back delay columns to decision_governance_policies
and extends mcp_pending_writeback with scheduling metadata.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260403_writeback_delay'
down_revision: Union[str, None] = '20260403_mcp_infra'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adaptive write-back delay columns on governance policies
    op.add_column('decision_governance_policies', sa.Column(
        'writeback_enabled', sa.Boolean(), server_default='true', nullable=False,
        comment='Enable MCP write-back for this action type',
    ))
    op.add_column('decision_governance_policies', sa.Column(
        'writeback_base_delay_minutes', sa.Integer(), server_default='30', nullable=False,
        comment='Base delay before ERP write-back (minutes)',
    ))
    op.add_column('decision_governance_policies', sa.Column(
        'writeback_min_delay_minutes', sa.Integer(), server_default='5', nullable=False,
        comment='Floor: minimum delay even for urgent+confident decisions',
    ))
    op.add_column('decision_governance_policies', sa.Column(
        'writeback_max_delay_minutes', sa.Integer(), server_default='480', nullable=False,
        comment='Ceiling: max delay for low-urgency/low-confidence (8h)',
    ))
    op.add_column('decision_governance_policies', sa.Column(
        'writeback_urgency_weight', sa.Float(), server_default='1.0', nullable=False,
        comment='How much urgency reduces delay (0=ignore, 1=full, 2=double)',
    ))
    op.add_column('decision_governance_policies', sa.Column(
        'writeback_confidence_weight', sa.Float(), server_default='1.0', nullable=False,
        comment='How much confidence reduces delay (0=ignore, 1=full, 2=double)',
    ))

    # Extend mcp_pending_writeback with scheduling metadata
    op.add_column('mcp_pending_writeback', sa.Column(
        'aiio_mode', sa.String(20), nullable=True,
    ))
    op.add_column('mcp_pending_writeback', sa.Column(
        'delay_minutes', sa.Integer(), nullable=True,
    ))
    op.add_column('mcp_pending_writeback', sa.Column(
        'urgency', sa.Float(), nullable=True,
    ))
    op.add_column('mcp_pending_writeback', sa.Column(
        'confidence', sa.Float(), nullable=True,
    ))
    op.add_column('mcp_pending_writeback', sa.Column(
        'eligible_at', sa.DateTime(), nullable=True,
        comment='When this write-back becomes eligible for execution',
    ))
    op.add_column('mcp_pending_writeback', sa.Column(
        'execution_result', sa.Text(), nullable=True,
    ))

    # Index for the scheduler query
    op.create_index(
        'ix_mcp_pending_eligible',
        'mcp_pending_writeback',
        ['status', 'eligible_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_mcp_pending_eligible', 'mcp_pending_writeback')
    op.drop_column('mcp_pending_writeback', 'execution_result')
    op.drop_column('mcp_pending_writeback', 'eligible_at')
    op.drop_column('mcp_pending_writeback', 'confidence')
    op.drop_column('mcp_pending_writeback', 'urgency')
    op.drop_column('mcp_pending_writeback', 'delay_minutes')
    op.drop_column('mcp_pending_writeback', 'aiio_mode')
    op.drop_column('decision_governance_policies', 'writeback_confidence_weight')
    op.drop_column('decision_governance_policies', 'writeback_urgency_weight')
    op.drop_column('decision_governance_policies', 'writeback_max_delay_minutes')
    op.drop_column('decision_governance_policies', 'writeback_min_delay_minutes')
    op.drop_column('decision_governance_policies', 'writeback_base_delay_minutes')
    op.drop_column('decision_governance_policies', 'writeback_enabled')
