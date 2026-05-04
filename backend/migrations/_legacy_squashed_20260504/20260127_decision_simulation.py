"""Decision Simulation and Approval Workflows

Revision ID: 20260127_decision_simulation
Revises: 20260127_scenario_branching
Create Date: 2026-01-27

Extends scenario branching to support decision simulation with approval workflows.
Enables agents and humans to propose actions, simulate impact in child scenarios,
and present business cases for approval.

Use Cases:
- Strategic: Network redesign, acquisition scenarios, operating model changes
- Tactical: Safety stock adjustments, sourcing rule changes, capacity expansions
- Operational: Expedite requests, emergency purchases, allocation overrides
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql, postgresql

# revision identifiers, used by Alembic.
revision = '20260127_decision_simulation'
down_revision = '20260127_scenario_branching'
branch_labels = None
depends_on = None


def upgrade():
    # =========================================================================
    # Check Database State
    # =========================================================================

    # Check database dialect
    conn = op.get_bind()
    dialect_name = conn.dialect.name
    inspector = sa.inspect(conn)

    # =========================================================================
    # Create decision_proposals Table
    # =========================================================================

    op.create_table(
        'decision_proposals',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),

        # Scenario linkage (child scenario created for simulation)
        sa.Column('scenario_id', sa.Integer, nullable=False),
        sa.Column('parent_scenario_id', sa.Integer, nullable=True),

        # Proposal metadata
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('description', sa.String(2000), nullable=True),
        sa.Column('proposed_by', sa.String(100), nullable=False),  # User ID or Agent ID
        sa.Column('proposed_by_type', sa.String(20), nullable=False),  # 'human' or 'agent'

        # Action details
        sa.Column('action_type', sa.String(50), nullable=False),  # 'expedite', 'increase_safety_stock', etc.
        sa.Column('action_params',
            postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
            nullable=False),

        # Authority and approval
        sa.Column('authority_level_required', sa.String(50), nullable=True),  # 'manager', 'director', 'vp'
        sa.Column('requires_approval_from', sa.String(100), nullable=True),  # User ID or role
        sa.Column('status', sa.String(20), nullable=False, default='pending'),  # pending, approved, rejected, executed

        # Business case (calculated from scenario simulation)
        sa.Column('business_case',
            postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
            nullable=True),

        # Impact metrics (from scenario comparison)
        sa.Column('financial_impact',
            postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
            nullable=True),
        sa.Column('operational_impact',
            postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
            nullable=True),
        sa.Column('strategic_impact',
            postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
            nullable=True),
        sa.Column('risk_metrics',
            postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
            nullable=True),

        # Approval tracking
        sa.Column('approved_by', sa.String(100), nullable=True),
        sa.Column('approved_at', sa.DateTime, nullable=True),
        sa.Column('rejection_reason', sa.String(1000), nullable=True),

        # Audit fields
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.text('NOW()'), onupdate=sa.text('NOW()')),
        sa.Column('executed_at', sa.DateTime, nullable=True),

        # Foreign keys
        sa.ForeignKeyConstraint(['scenario_id'], ['supply_chain_configs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_scenario_id'], ['supply_chain_configs.id'], ondelete='SET NULL'),
    )

    # Create indexes for decision_proposals
    op.create_index('idx_decision_proposals_scenario', 'decision_proposals', ['scenario_id'])
    op.create_index('idx_decision_proposals_status', 'decision_proposals', ['status'])
    op.create_index('idx_decision_proposals_proposed_by', 'decision_proposals', ['proposed_by'])
    op.create_index('idx_decision_proposals_action_type', 'decision_proposals', ['action_type'])
    op.create_index('idx_decision_proposals_created', 'decision_proposals', ['created_at'])

    # =========================================================================
    # Create authority_definitions Table
    # =========================================================================

    op.create_table(
        'authority_definitions',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),

        # Scope
        sa.Column('customer_id', sa.Integer, nullable=False),
        sa.Column('config_id', sa.Integer, nullable=True),  # Null = applies to all configs in group

        # Agent/User scope
        sa.Column('agent_id', sa.String(100), nullable=True),  # Specific agent
        sa.Column('user_id', sa.Integer, nullable=True),  # Specific user
        sa.Column('role', sa.String(50), nullable=True),  # Role-based (e.g., 'planner', 'manager')

        # Authority details
        sa.Column('action_type', sa.String(50), nullable=False),
        sa.Column('max_value', sa.Float, nullable=True),  # Monetary or quantity threshold
        sa.Column('requires_approval', sa.Boolean, nullable=False, default=True),
        sa.Column('approval_authority', sa.String(50), nullable=True),  # Required authority level

        # Constraints
        sa.Column('conditions',
            postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
            nullable=True),  # Additional conditions (e.g., "only for items with value > $1000")

        # Audit
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('NOW()')),
        sa.Column('created_by', sa.Integer, nullable=True),

        # Foreign keys
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    )

    # Create indexes for authority_definitions
    op.create_index('idx_authority_group', 'authority_definitions', ['customer_id'])
    op.create_index('idx_authority_config', 'authority_definitions', ['config_id'])
    op.create_index('idx_authority_action', 'authority_definitions', ['action_type'])
    op.create_index('idx_authority_agent', 'authority_definitions', ['agent_id'])

    # =========================================================================
    # Create business_impact_snapshots Table
    # =========================================================================

    op.create_table(
        'business_impact_snapshots',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),

        # Linkage
        sa.Column('proposal_id', sa.Integer, nullable=False),
        sa.Column('scenario_id', sa.Integer, nullable=False),

        # Snapshot metadata
        sa.Column('snapshot_type', sa.String(20), nullable=False),  # 'before', 'after', 'comparison'
        sa.Column('planning_horizon', sa.Integer, nullable=False),  # Weeks simulated
        sa.Column('simulation_runs', sa.Integer, nullable=True),  # Number of Monte Carlo runs

        # Probabilistic Balanced Scorecard
        sa.Column('financial_metrics',
            postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
            nullable=False),  # total_cost, revenue, roi with P10/P50/P90
        sa.Column('customer_metrics',
            postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
            nullable=False),  # otif, fill_rate, backlog with distributions
        sa.Column('operational_metrics',
            postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
            nullable=False),  # inventory_turns, dos, cycle_time with distributions
        sa.Column('strategic_metrics',
            postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
            nullable=False),  # flexibility, supplier_reliability, co2_emissions

        # Computed at
        sa.Column('computed_at', sa.DateTime, nullable=False, server_default=sa.text('NOW()')),

        # Foreign keys
        sa.ForeignKeyConstraint(['proposal_id'], ['decision_proposals.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['scenario_id'], ['supply_chain_configs.id'], ondelete='CASCADE'),
    )

    # Create indexes for business_impact_snapshots
    op.create_index('idx_impact_proposal', 'business_impact_snapshots', ['proposal_id'])
    op.create_index('idx_impact_scenario', 'business_impact_snapshots', ['scenario_id'])
    op.create_index('idx_impact_type', 'business_impact_snapshots', ['snapshot_type'])


def downgrade():
    # =========================================================================
    # Drop Tables
    # =========================================================================

    op.drop_table('business_impact_snapshots')
    op.drop_table('authority_definitions')
    op.drop_table('decision_proposals')
