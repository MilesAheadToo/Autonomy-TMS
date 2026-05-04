"""add capacity plans

Revision ID: 20260120_add_capacity_plans
Revises: 20260120_add_production_orders
Create Date: 2026-01-20 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260120_add_capacity_plans'
down_revision = '20260120_add_production_orders'
branch_labels = None
depends_on = None


def upgrade():
    """Create capacity planning tables."""
    # Create capacity_plans table
    op.create_table(
        'capacity_plans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('supply_chain_config_id', sa.Integer(), nullable=False),
        sa.Column('planning_horizon_weeks', sa.Integer(), nullable=False, server_default='13'),
        sa.Column('bucket_size_days', sa.Integer(), nullable=False, server_default='7'),
        sa.Column('start_date', sa.DateTime(), nullable=False),
        sa.Column('end_date', sa.DateTime(), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='DRAFT'),
        sa.Column('is_scenario', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('scenario_description', sa.Text(), nullable=True),
        sa.Column('base_plan_id', sa.Integer(), nullable=True),
        sa.Column('total_resources', sa.Integer(), nullable=True),
        sa.Column('overloaded_resources', sa.Integer(), nullable=True),
        sa.Column('avg_utilization_percent', sa.Float(), nullable=True),
        sa.Column('max_utilization_percent', sa.Float(), nullable=True),
        sa.Column('bottleneck_identified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes for capacity_plans
    op.create_index('ix_capacity_plans_id', 'capacity_plans', ['id'])
    op.create_index('ix_capacity_plans_supply_chain_config_id', 'capacity_plans', ['supply_chain_config_id'])
    op.create_index('ix_capacity_plans_status', 'capacity_plans', ['status'])
    op.create_index('ix_capacity_plans_created_by', 'capacity_plans', ['created_by'])

    # Create capacity_resources table
    op.create_table(
        'capacity_resources',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('resource_name', sa.String(255), nullable=False),
        sa.Column('resource_code', sa.String(50), nullable=True),
        sa.Column('resource_type', sa.String(50), nullable=False, server_default='MACHINE'),
        sa.Column('site_id', sa.Integer(), nullable=False),
        sa.Column('available_capacity', sa.Float(), nullable=False),
        sa.Column('capacity_unit', sa.String(50), nullable=False, server_default='hours'),
        sa.Column('efficiency_percent', sa.Float(), nullable=False, server_default='100.0'),
        sa.Column('utilization_target_percent', sa.Float(), nullable=False, server_default='85.0'),
        sa.Column('cost_per_hour', sa.Float(), nullable=True),
        sa.Column('setup_time_hours', sa.Float(), nullable=True),
        sa.Column('shifts_per_day', sa.Integer(), nullable=True),
        sa.Column('hours_per_shift', sa.Float(), nullable=True),
        sa.Column('working_days_per_week', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes for capacity_resources
    op.create_index('ix_capacity_resources_id', 'capacity_resources', ['id'])
    op.create_index('ix_capacity_resources_plan_id', 'capacity_resources', ['plan_id'])
    op.create_index('ix_capacity_resources_site_id', 'capacity_resources', ['site_id'])
    op.create_index('ix_capacity_resources_resource_type', 'capacity_resources', ['resource_type'])

    # Create capacity_requirements table
    op.create_table(
        'capacity_requirements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('resource_id', sa.Integer(), nullable=False),
        sa.Column('period_start', sa.DateTime(), nullable=False),
        sa.Column('period_end', sa.DateTime(), nullable=False),
        sa.Column('period_number', sa.Integer(), nullable=False),
        sa.Column('required_capacity', sa.Float(), nullable=False),
        sa.Column('available_capacity', sa.Float(), nullable=False),
        sa.Column('utilization_percent', sa.Float(), nullable=False),
        sa.Column('is_overloaded', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('overload_amount', sa.Float(), nullable=True),
        sa.Column('is_bottleneck', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('source_type', sa.String(50), nullable=True),
        sa.Column('source_id', sa.Integer(), nullable=True),
        sa.Column('requirement_breakdown', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes for capacity_requirements
    op.create_index('ix_capacity_requirements_id', 'capacity_requirements', ['id'])
    op.create_index('ix_capacity_requirements_plan_id', 'capacity_requirements', ['plan_id'])
    op.create_index('ix_capacity_requirements_resource_id', 'capacity_requirements', ['resource_id'])
    op.create_index('ix_capacity_requirements_period_start', 'capacity_requirements', ['period_start'])
    op.create_index('ix_capacity_requirements_is_overloaded', 'capacity_requirements', ['is_overloaded'])
    op.create_index('ix_capacity_requirements_is_bottleneck', 'capacity_requirements', ['is_bottleneck'])

    # Create foreign key constraints (with table existence checks)
    inspector = sa.inspect(op.get_bind())
    existing_tables = inspector.get_table_names()

    # capacity_plans foreign keys
    if 'supply_chain_configs' in existing_tables:
        op.create_foreign_key(
            'fk_capacity_plans_supply_chain_config_id',
            'capacity_plans', 'supply_chain_configs',
            ['supply_chain_config_id'], ['id'],
            ondelete='CASCADE'
        )

    if 'users' in existing_tables:
        op.create_foreign_key(
            'fk_capacity_plans_created_by',
            'capacity_plans', 'users',
            ['created_by'], ['id'],
            ondelete='SET NULL'
        )
        op.create_foreign_key(
            'fk_capacity_plans_updated_by',
            'capacity_plans', 'users',
            ['updated_by'], ['id'],
            ondelete='SET NULL'
        )

    # Self-referencing foreign key for base_plan_id
    op.create_foreign_key(
        'fk_capacity_plans_base_plan_id',
        'capacity_plans', 'capacity_plans',
        ['base_plan_id'], ['id'],
        ondelete='SET NULL'
    )

    # capacity_resources foreign keys
    op.create_foreign_key(
        'fk_capacity_resources_plan_id',
        'capacity_resources', 'capacity_plans',
        ['plan_id'], ['id'],
        ondelete='CASCADE'
    )

    if 'nodes' in existing_tables:
        op.create_foreign_key(
            'fk_capacity_resources_site_id',
            'capacity_resources', 'nodes',
            ['site_id'], ['id'],
            ondelete='CASCADE'
        )

    # capacity_requirements foreign keys
    op.create_foreign_key(
        'fk_capacity_requirements_plan_id',
        'capacity_requirements', 'capacity_plans',
        ['plan_id'], ['id'],
        ondelete='CASCADE'
    )
    op.create_foreign_key(
        'fk_capacity_requirements_resource_id',
        'capacity_requirements', 'capacity_resources',
        ['resource_id'], ['id'],
        ondelete='CASCADE'
    )


def downgrade():
    """Drop capacity planning tables."""
    # Drop foreign keys first
    op.drop_constraint('fk_capacity_requirements_resource_id', 'capacity_requirements', type_='foreignkey')
    op.drop_constraint('fk_capacity_requirements_plan_id', 'capacity_requirements', type_='foreignkey')
    op.drop_constraint('fk_capacity_resources_site_id', 'capacity_resources', type_='foreignkey')
    op.drop_constraint('fk_capacity_resources_plan_id', 'capacity_resources', type_='foreignkey')
    op.drop_constraint('fk_capacity_plans_base_plan_id', 'capacity_plans', type_='foreignkey')
    op.drop_constraint('fk_capacity_plans_updated_by', 'capacity_plans', type_='foreignkey')
    op.drop_constraint('fk_capacity_plans_created_by', 'capacity_plans', type_='foreignkey')
    op.drop_constraint('fk_capacity_plans_supply_chain_config_id', 'capacity_plans', type_='foreignkey')

    # Drop indexes
    op.drop_index('ix_capacity_requirements_is_bottleneck', 'capacity_requirements')
    op.drop_index('ix_capacity_requirements_is_overloaded', 'capacity_requirements')
    op.drop_index('ix_capacity_requirements_period_start', 'capacity_requirements')
    op.drop_index('ix_capacity_requirements_resource_id', 'capacity_requirements')
    op.drop_index('ix_capacity_requirements_plan_id', 'capacity_requirements')
    op.drop_index('ix_capacity_requirements_id', 'capacity_requirements')
    op.drop_index('ix_capacity_resources_resource_type', 'capacity_resources')
    op.drop_index('ix_capacity_resources_site_id', 'capacity_resources')
    op.drop_index('ix_capacity_resources_plan_id', 'capacity_resources')
    op.drop_index('ix_capacity_resources_id', 'capacity_resources')
    op.drop_index('ix_capacity_plans_created_by', 'capacity_plans')
    op.drop_index('ix_capacity_plans_status', 'capacity_plans')
    op.drop_index('ix_capacity_plans_supply_chain_config_id', 'capacity_plans')
    op.drop_index('ix_capacity_plans_id', 'capacity_plans')

    # Drop tables
    op.drop_table('capacity_requirements')
    op.drop_table('capacity_resources')
    op.drop_table('capacity_plans')
