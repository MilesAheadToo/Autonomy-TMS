"""add_mps_tables

Revision ID: 6f38d8e5eecd
Revises: ddfb5f63890a
Create Date: 2026-01-19 06:33:38.704727

Creates Master Production Scheduling (MPS) tables:
- mps_plans: Main MPS plan records
- mps_plan_items: Time-phased quantities for products
- mps_capacity_checks: Rough-cut capacity planning results
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


# revision identifiers, used by Alembic.
revision: str = '6f38d8e5eecd'
down_revision: Union[str, None] = 'ddfb5f63890a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create MPS status enum type
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE mpsstatus AS ENUM (
                'DRAFT', 'PENDING_APPROVAL', 'APPROVED',
                'IN_EXECUTION', 'COMPLETED', 'CANCELLED'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)

    # Create mps_plans table
    op.create_table(
        'mps_plans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('supply_chain_config_id', sa.Integer(), nullable=False),
        sa.Column('planning_horizon_weeks', sa.Integer(), nullable=False, server_default='13'),
        sa.Column('bucket_size_days', sa.Integer(), nullable=False, server_default='7'),
        sa.Column('start_date', sa.DateTime(), nullable=False),
        sa.Column('end_date', sa.DateTime(), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='DRAFT'),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('approved_by', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('execution_started_at', sa.DateTime(), nullable=True),
        sa.Column('execution_completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['supply_chain_config_id'], ['supply_chain_configs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_mps_plans_id'), 'mps_plans', ['id'], unique=False)
    op.create_index(op.f('ix_mps_plans_supply_chain_config_id'), 'mps_plans', ['supply_chain_config_id'], unique=False)
    op.create_index(op.f('ix_mps_plans_status'), 'mps_plans', ['status'], unique=False)

    # Create mps_plan_items table
    op.create_table(
        'mps_plan_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=False),
        sa.Column('weekly_quantities', JSON, nullable=False, server_default='[]'),
        sa.Column('lot_size_rule', sa.String(length=50), nullable=True),
        sa.Column('lot_size_value', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['plan_id'], ['mps_plans.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['product_id'], ['items.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['site_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_mps_plan_items_id'), 'mps_plan_items', ['id'], unique=False)
    op.create_index(op.f('ix_mps_plan_items_plan_id'), 'mps_plan_items', ['plan_id'], unique=False)
    op.create_index(op.f('ix_mps_plan_items_product_id'), 'mps_plan_items', ['product_id'], unique=False)
    op.create_index(op.f('ix_mps_plan_items_site_id'), 'mps_plan_items', ['site_id'], unique=False)

    # Create mps_capacity_checks table
    op.create_table(
        'mps_capacity_checks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('resource_name', sa.String(length=255), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=False),
        sa.Column('period_start', sa.DateTime(), nullable=False),
        sa.Column('period_end', sa.DateTime(), nullable=False),
        sa.Column('required_capacity', sa.Float(), nullable=False),
        sa.Column('available_capacity', sa.Float(), nullable=False),
        sa.Column('utilization_percent', sa.Float(), nullable=False),
        sa.Column('is_overloaded', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('overload_amount', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['plan_id'], ['mps_plans.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['site_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_mps_capacity_checks_id'), 'mps_capacity_checks', ['id'], unique=False)
    op.create_index(op.f('ix_mps_capacity_checks_plan_id'), 'mps_capacity_checks', ['plan_id'], unique=False)
    op.create_index(op.f('ix_mps_capacity_checks_site_id'), 'mps_capacity_checks', ['site_id'], unique=False)


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table('mps_capacity_checks')
    op.drop_table('mps_plan_items')
    op.drop_table('mps_plans')

    # Drop enum type
    op.execute('DROP TYPE mpsstatus')
