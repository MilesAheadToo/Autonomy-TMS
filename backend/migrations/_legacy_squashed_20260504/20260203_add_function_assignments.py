"""Add function assignments for expanded role architecture.

Revision ID: 20260203_add_function_assignments
Revises: 20260203_rename_round_id_columns
Create Date: 2026-02-03

This migration adds:
1. ParticipantFunction enum type for planning and execution functions
2. FunctionCategory enum type for planning vs execution categorization
3. function_assignments table for mapping participants to specific functions
4. New columns on participants table for function-related fields
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260203_add_function_assignments'
down_revision = '20260203_rename_round_id'
branch_labels = None
depends_on = None


def column_exists(table_name, column_name, conn):
    """Check if a column exists in a table."""
    from sqlalchemy import text
    result = conn.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = :table AND column_name = :column
    """), {"table": table_name, "column": column_name})
    return result.fetchone() is not None


def table_exists(table_name, conn):
    """Check if a table exists."""
    from sqlalchemy import text
    result = conn.execute(text("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = :table AND table_schema = 'public'
    """), {"table": table_name})
    return result.fetchone() is not None


def upgrade():
    """Add function assignment tables and columns."""
    from sqlalchemy import text

    conn = op.get_bind()

    # Create participantfunction enum using raw SQL (idempotent)
    conn.execute(text("""
        DO $$ BEGIN
            CREATE TYPE participantfunction AS ENUM (
                'forecasting', 'demand_planning', 'inventory_planning',
                'supply_planning', 'allocation_planning',
                'atp_promising', 'shipping', 'po_creation', 'receiving',
                'node_operator'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """))

    # Create agentmode enum using raw SQL (idempotent)
    conn.execute(text("""
        DO $$ BEGIN
            CREATE TYPE agentmode AS ENUM ('manual', 'copilot', 'autonomous');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """))

    # Reference existing enum types (create_type=False is critical)
    participant_function_enum = postgresql.ENUM(
        'forecasting', 'demand_planning', 'inventory_planning',
        'supply_planning', 'allocation_planning',
        'atp_promising', 'shipping', 'po_creation', 'receiving',
        'node_operator',
        name='participantfunction',
        create_type=False
    )

    agentmode_enum = postgresql.ENUM(
        'manual', 'copilot', 'autonomous',
        name='agentmode',
        create_type=False
    )

    # Create function_assignments table if it doesn't exist
    if not table_exists('function_assignments', conn):
        op.create_table(
            'function_assignments',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('scenario_id', sa.Integer(), nullable=False),
            sa.Column('participant_id', sa.Integer(), nullable=False),
            sa.Column('site_key', sa.String(100), nullable=False),
            sa.Column('function', participant_function_enum, nullable=False),
            sa.Column('agent_mode', agentmode_enum, nullable=True),
            sa.Column('auto_execute_threshold', sa.Float(), nullable=True, default=0.8),
            sa.Column('is_active', sa.Boolean(), nullable=True, default=True),
            sa.Column('planning_horizon_weeks', sa.Integer(), nullable=True),
            sa.Column('trm_agent_type', sa.String(50), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['scenario_id'], ['scenarios.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['participant_id'], ['participants.id'], ondelete='CASCADE'),
            sa.UniqueConstraint('scenario_id', 'site_key', 'function', name='uq_scenario_site_function')
        )
        op.create_index(op.f('ix_function_assignments_id'), 'function_assignments', ['id'], unique=False)
        op.create_index(op.f('ix_function_assignments_site_key'), 'function_assignments', ['site_key'], unique=False)

    # Add new columns to participants table (only if they don't exist)
    if not column_exists('participants', 'function', conn):
        op.add_column('participants', sa.Column(
            'function',
            participant_function_enum,
            nullable=True,
            comment='Functional responsibility (forecasting, atp_promising, etc.)'
        ))

    if not column_exists('participants', 'planning_horizon_weeks', conn):
        op.add_column('participants', sa.Column(
            'planning_horizon_weeks',
            sa.Integer(),
            nullable=True,
            comment='For planners: lookahead horizon (default from function)'
        ))

    if not column_exists('participants', 'max_decision_latency_ms', conn):
        op.add_column('participants', sa.Column(
            'max_decision_latency_ms',
            sa.Integer(),
            nullable=True,
            comment='For executors: max acceptable latency (e.g., ATP=10ms)'
        ))

    if not column_exists('participants', 'trm_agent_type', conn):
        op.add_column('participants', sa.Column(
            'trm_agent_type',
            sa.String(50),
            nullable=True,
            comment='TRM agent type: atp_executor, po_creation_trm, etc.'
        ))


def downgrade():
    """Remove function assignment tables and columns."""
    from sqlalchemy import text

    conn = op.get_bind()

    # Remove columns from participants table (only if they exist)
    if column_exists('participants', 'trm_agent_type', conn):
        op.drop_column('participants', 'trm_agent_type')

    if column_exists('participants', 'max_decision_latency_ms', conn):
        op.drop_column('participants', 'max_decision_latency_ms')

    if column_exists('participants', 'planning_horizon_weeks', conn):
        op.drop_column('participants', 'planning_horizon_weeks')

    if column_exists('participants', 'function', conn):
        op.drop_column('participants', 'function')

    # Drop function_assignments table if it exists
    if table_exists('function_assignments', conn):
        op.drop_index(op.f('ix_function_assignments_site_key'), table_name='function_assignments')
        op.drop_index(op.f('ix_function_assignments_id'), table_name='function_assignments')
        op.drop_table('function_assignments')

    # Note: We don't drop the enum types as they may cause issues with other tables
    # that might still reference them.
