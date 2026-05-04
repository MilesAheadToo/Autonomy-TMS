"""Create equipment_move table for empty reposition tracking

Revision ID: 20260414_equip_move
Revises: 20260410_tms_02
Create Date: 2026-04-14 12:00:00.000000

Adds equipment_move table for tracking empty trailer/container repositioning.
Primary training source for EquipmentRepositionTRM and performance attribution.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '20260414_equip_move'
down_revision = '20260410_tms_02'
branch_labels = None
depends_on = None


reason_enum = postgresql.ENUM(
    'REBALANCE', 'EMPTY_RETURN', 'MAINTENANCE',
    'DEMURRAGE_AVOIDANCE', 'DEADHEAD', 'CUSTOMER_REQUEST',
    name='equipment_move_reason_enum', create_type=False)

status_enum = postgresql.ENUM(
    'PLANNED', 'DISPATCHED', 'IN_TRANSIT', 'ARRIVED', 'CANCELLED',
    name='equipment_move_status_enum', create_type=False)


def upgrade():
    reason_enum.create(op.get_bind(), checkfirst=True)
    status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'equipment_move',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('equipment_id', sa.Integer,
                  sa.ForeignKey('equipment.id', ondelete='CASCADE'), nullable=False),
        sa.Column('carrier_id', sa.Integer,
                  sa.ForeignKey('carrier.id', ondelete='SET NULL')),
        sa.Column('from_site_id', sa.Integer,
                  sa.ForeignKey('site.id'), nullable=False),
        sa.Column('to_site_id', sa.Integer,
                  sa.ForeignKey('site.id'), nullable=False),
        sa.Column('miles', sa.Float(asdecimal=False), nullable=False),
        sa.Column('dispatched_at', sa.DateTime),
        sa.Column('arrived_at', sa.DateTime),
        sa.Column('planned_arrival_at', sa.DateTime),
        sa.Column('cost', sa.Float(asdecimal=False)),
        sa.Column('cost_of_not_repositioning', sa.Float(asdecimal=False)),
        sa.Column('roi', sa.Float(asdecimal=False)),
        sa.Column('reason', reason_enum, nullable=False),
        sa.Column('status', status_enum, nullable=False,
                  server_default='PLANNED'),
        sa.Column('agent_decision_id', sa.String(100)),
        sa.Column('decision_rationale', sa.JSON),
        sa.Column('tenant_id', sa.Integer,
                  sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('config_id', sa.Integer,
                  sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE')),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
    )

    op.create_index('idx_equipment_move_tenant', 'equipment_move',
                    ['tenant_id', 'status'])
    op.create_index('idx_equipment_move_equipment', 'equipment_move',
                    ['equipment_id', 'dispatched_at'])
    op.create_index('idx_equipment_move_lane', 'equipment_move',
                    ['from_site_id', 'to_site_id'])


def downgrade():
    op.drop_index('idx_equipment_move_lane', table_name='equipment_move')
    op.drop_index('idx_equipment_move_equipment', table_name='equipment_move')
    op.drop_index('idx_equipment_move_tenant', table_name='equipment_move')
    op.drop_table('equipment_move')
    status_enum.drop(op.get_bind(), checkfirst=True)
    reason_enum.drop(op.get_bind(), checkfirst=True)
