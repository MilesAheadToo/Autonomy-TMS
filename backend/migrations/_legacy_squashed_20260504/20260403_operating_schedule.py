"""Tenant operating schedule for human oversight

Revision ID: 20260403_operating_schedule
Revises: 20260403_writeback_delay
Create Date: 2026-04-03

Adds operating schedule tables so write-back delays respect business hours:
- tenant_operating_schedule: Weekly hours per day-of-week
- tenant_holiday_calendar: Non-operating dates
- tenant_oversight_config: Timezone, bypass rules, on-call
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260403_operating_schedule'
down_revision: Union[str, None] = '20260403_writeback_delay'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Weekly operating schedule
    op.create_table(
        'tenant_operating_schedule',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('day_of_week', sa.Integer(), nullable=False, comment='0=Monday ... 6=Sunday'),
        sa.Column('start_time', sa.String(5), nullable=False, server_default='08:00'),
        sa.Column('end_time', sa.String(5), nullable=False, server_default='17:00'),
        sa.Column('is_operating', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('NOW()')),
        sa.UniqueConstraint('tenant_id', 'day_of_week', name='uq_schedule_tenant_day'),
    )

    # Holiday calendar
    op.create_table(
        'tenant_holiday_calendar',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('holiday_date', sa.DateTime(), nullable=False),
        sa.Column('name', sa.String(200), nullable=True),
        sa.Column('recurring', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('NOW()')),
        sa.UniqueConstraint('tenant_id', 'holiday_date', name='uq_holiday_tenant_date'),
    )

    # Oversight configuration
    op.create_table(
        'tenant_oversight_config',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('timezone', sa.String(50), nullable=True, server_default='UTC'),
        sa.Column('respect_business_hours', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('urgent_bypass_enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('urgent_bypass_threshold', sa.Float(), nullable=False, server_default='0.85'),
        sa.Column('extend_delay_over_weekends', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('max_calendar_delay_hours', sa.Integer(), nullable=False, server_default='72'),
        sa.Column('oncall_enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('oncall_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('NOW()')),
    )

    # RLS on all three tables
    for table in ['tenant_operating_schedule', 'tenant_holiday_calendar', 'tenant_oversight_config']:
        op.execute(f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            CREATE POLICY {table}_tenant_isolation ON {table}
                USING (tenant_id = current_setting('app.current_tenant_id', true)::int);
        """)


def downgrade() -> None:
    for table in ['tenant_oversight_config', 'tenant_holiday_calendar', 'tenant_operating_schedule']:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
