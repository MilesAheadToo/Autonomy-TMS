"""Powell Sequential Decision Framework tables

Revision ID: 20260202_powell_framework
Revises: 20260202_chat_player_to_participant
Create Date: 2026-02-02 14:00:00.000000

Powell's Sequential Decision Analytics and Modeling (SDAM) framework
provides a unified theoretical foundation for supply chain optimization.

Tables:
- powell_belief_state: Uncertainty quantification via conformal prediction
- powell_policy_parameters: Optimized policy parameters (θ) from CFA
- powell_value_function: VFA state values for tabular fallback
- powell_hierarchical_constraints: Consistency constraints across planning levels
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260202_powell_framework'
down_revision = '20260202_chat_participant'
branch_labels = None
depends_on = None


def upgrade():
    """
    Add tables for Powell Sequential Decision Framework.
    """

    # 1. powell_belief_state table
    # Stores uncertainty quantification using conformal prediction intervals
    op.create_table(
        'powell_belief_state',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('entity_type', sa.String(50), nullable=False),  # 'demand', 'lead_time', 'yield'
        sa.Column('entity_id', sa.String(100), nullable=False),  # product_id, supplier_id, etc.

        # Point estimates
        sa.Column('point_estimate', sa.Float(), nullable=True),

        # Conformal intervals (coverage-guaranteed)
        sa.Column('conformal_lower', sa.Float(), nullable=True),
        sa.Column('conformal_upper', sa.Float(), nullable=True),
        sa.Column('conformal_coverage', sa.Float(), nullable=True),  # e.g., 0.90
        sa.Column('conformal_method', sa.String(50), nullable=True),  # 'aci', 'split_conformal', etc.

        # Tracking for adaptive conformal inference (ACI)
        sa.Column('recent_residuals', sa.JSON(), nullable=True),  # Last 100 residuals
        sa.Column('coverage_history', sa.JSON(), nullable=True),  # Last 100 coverage indicators
        sa.Column('alpha', sa.Float(), nullable=True),  # Current miscoverage rate for ACI

        # Metadata
        sa.Column('config_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE')
    )

    op.create_index('idx_belief_entity', 'powell_belief_state', ['entity_type', 'entity_id'])
    op.create_index('idx_belief_config', 'powell_belief_state', ['config_id'])
    op.create_index('idx_belief_updated', 'powell_belief_state', ['updated_at'])

    # 2. powell_policy_parameters table
    # Stores optimized policy parameters θ from Powell's CFA approach
    op.create_table(
        'powell_policy_parameters',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('policy_type', sa.String(50), nullable=False),  # 'inventory', 'lot_sizing', 'exception', 'mrp'
        sa.Column('entity_type', sa.String(50), nullable=True),  # 'product', 'site', 'product_site'
        sa.Column('entity_id', sa.String(100), nullable=True),

        # Optimized parameters (JSON for flexibility)
        sa.Column('parameters', sa.JSON(), nullable=False),

        # Optimization metadata
        sa.Column('optimization_method', sa.String(50), nullable=True),  # 'differential_evolution', 'nelder_mead'
        sa.Column('optimization_objective', sa.String(50), nullable=True),  # 'expected_cost', 'cvar_95'
        sa.Column('optimization_value', sa.Float(), nullable=True),
        sa.Column('confidence_interval_lower', sa.Float(), nullable=True),
        sa.Column('confidence_interval_upper', sa.Float(), nullable=True),
        sa.Column('num_scenarios', sa.Integer(), nullable=True),
        sa.Column('num_iterations', sa.Integer(), nullable=True),

        # Validity period
        sa.Column('valid_from', sa.Date(), nullable=True),
        sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),

        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE')
    )

    op.create_index('idx_policy_config_type', 'powell_policy_parameters', ['config_id', 'policy_type'])
    op.create_index('idx_policy_entity', 'powell_policy_parameters', ['entity_type', 'entity_id'])
    op.create_index('idx_policy_active', 'powell_policy_parameters', ['is_active'])

    # 3. powell_value_function table
    # Stores VFA state values for tabular fallback and monitoring
    op.create_table(
        'powell_value_function',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('agent_type', sa.String(50), nullable=False),  # 'trm', 'rl', 'gnn', 'execution'

        # State discretization (for tabular VFA)
        sa.Column('state_key', sa.String(255), nullable=False),

        # Value estimates
        sa.Column('v_value', sa.Float(), nullable=True),  # V(s) estimate
        sa.Column('q_values', sa.JSON(), nullable=True),  # Q(s,a) estimates by action

        # TD learning metadata
        sa.Column('td_error_history', sa.JSON(), nullable=True),
        sa.Column('update_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_visit_period', sa.Integer(), nullable=True),

        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE')
    )

    op.create_index('idx_vf_config_state', 'powell_value_function', ['config_id', 'state_key'])
    op.create_index('idx_vf_agent_type', 'powell_value_function', ['agent_type'])
    op.create_index('idx_vf_updated', 'powell_value_function', ['updated_at'])

    # Add unique constraint for config+agent_type+state_key
    op.create_unique_constraint(
        'uq_powell_vf_state',
        'powell_value_function',
        ['config_id', 'agent_type', 'state_key']
    )

    # 4. powell_hierarchical_constraints table
    # Stores constraints from higher planning levels for consistency
    op.create_table(
        'powell_hierarchical_constraints',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('planning_level', sa.String(50), nullable=False),  # 'strategic', 'tactical', 'operational', 'execution'
        sa.Column('constraint_type', sa.String(50), nullable=False),  # 'bound', 'fence', 'target', 'reservation'

        # Constraint specification
        sa.Column('entity_type', sa.String(50), nullable=True),  # 'product', 'site', 'resource'
        sa.Column('entity_id', sa.String(100), nullable=True),
        sa.Column('parameter_name', sa.String(100), nullable=True),
        sa.Column('min_value', sa.Float(), nullable=True),
        sa.Column('max_value', sa.Float(), nullable=True),
        sa.Column('target_value', sa.Float(), nullable=True),

        # Validity period
        sa.Column('valid_from', sa.Date(), nullable=True),
        sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),

        # Source of constraint (which higher level set it)
        sa.Column('source_level', sa.String(50), nullable=True),
        sa.Column('source_decision_id', sa.Integer(), nullable=True),

        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE')
    )

    op.create_index('idx_constraint_config_level', 'powell_hierarchical_constraints', ['config_id', 'planning_level'])
    op.create_index('idx_constraint_entity', 'powell_hierarchical_constraints', ['entity_type', 'entity_id'])
    op.create_index('idx_constraint_active', 'powell_hierarchical_constraints', ['is_active'])

    # 5. powell_exception_resolution table
    # Stores exception handling decisions for VFA learning
    op.create_table(
        'powell_exception_resolution',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('exception_id', sa.String(100), nullable=False),

        # Exception details
        sa.Column('exception_type', sa.String(50), nullable=False),
        sa.Column('severity', sa.Float(), nullable=True),
        sa.Column('affected_quantity', sa.Float(), nullable=True),
        sa.Column('affected_product_id', sa.String(100), nullable=True),
        sa.Column('affected_site_id', sa.String(100), nullable=True),

        # Resolution
        sa.Column('action_taken', sa.String(50), nullable=False),
        sa.Column('estimated_cost', sa.Float(), nullable=True),
        sa.Column('actual_cost', sa.Float(), nullable=True),
        sa.Column('reasoning', sa.Text(), nullable=True),

        # Learning
        sa.Column('state_key', sa.String(255), nullable=True),
        sa.Column('td_error', sa.Float(), nullable=True),

        sa.Column('resolved_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE')
    )

    op.create_index('idx_exception_config', 'powell_exception_resolution', ['config_id'])
    op.create_index('idx_exception_type', 'powell_exception_resolution', ['exception_type'])
    op.create_index('idx_exception_action', 'powell_exception_resolution', ['action_taken'])
    op.create_index('idx_exception_resolved', 'powell_exception_resolution', ['resolved_at'])

    # 6. powell_stochastic_solution table
    # Stores solutions from stochastic programming for policy extraction
    op.create_table(
        'powell_stochastic_solution',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('config_id', sa.Integer(), nullable=False),

        # Problem specification
        sa.Column('planning_horizon', sa.Integer(), nullable=False),
        sa.Column('num_scenarios', sa.Integer(), nullable=False),
        sa.Column('risk_measure', sa.String(50), nullable=True),  # 'expected', 'cvar', 'robust'

        # First-stage decisions (JSON)
        sa.Column('first_stage_decisions', sa.JSON(), nullable=False),

        # Risk metrics
        sa.Column('expected_cost', sa.Float(), nullable=False),
        sa.Column('var_95', sa.Float(), nullable=True),
        sa.Column('cvar_95', sa.Float(), nullable=True),
        sa.Column('var_99', sa.Float(), nullable=True),
        sa.Column('cvar_99', sa.Float(), nullable=True),

        # Cost distribution (percentiles)
        sa.Column('cost_p10', sa.Float(), nullable=True),
        sa.Column('cost_p50', sa.Float(), nullable=True),
        sa.Column('cost_p90', sa.Float(), nullable=True),

        # Extracted policy parameters
        sa.Column('policy_params', sa.JSON(), nullable=True),

        # Solve metadata
        sa.Column('solve_status', sa.String(50), nullable=True),
        sa.Column('solve_time', sa.Float(), nullable=True),
        sa.Column('optimality_gap', sa.Float(), nullable=True),

        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE')
    )

    op.create_index('idx_stochastic_config', 'powell_stochastic_solution', ['config_id'])
    op.create_index('idx_stochastic_created', 'powell_stochastic_solution', ['created_at'])
    op.create_index('idx_stochastic_risk', 'powell_stochastic_solution', ['risk_measure'])


def downgrade():
    """Remove Powell framework tables."""

    # Drop powell_stochastic_solution
    op.drop_index('idx_stochastic_risk', table_name='powell_stochastic_solution')
    op.drop_index('idx_stochastic_created', table_name='powell_stochastic_solution')
    op.drop_index('idx_stochastic_config', table_name='powell_stochastic_solution')
    op.drop_table('powell_stochastic_solution')

    # Drop powell_exception_resolution
    op.drop_index('idx_exception_resolved', table_name='powell_exception_resolution')
    op.drop_index('idx_exception_action', table_name='powell_exception_resolution')
    op.drop_index('idx_exception_type', table_name='powell_exception_resolution')
    op.drop_index('idx_exception_config', table_name='powell_exception_resolution')
    op.drop_table('powell_exception_resolution')

    # Drop powell_hierarchical_constraints
    op.drop_index('idx_constraint_active', table_name='powell_hierarchical_constraints')
    op.drop_index('idx_constraint_entity', table_name='powell_hierarchical_constraints')
    op.drop_index('idx_constraint_config_level', table_name='powell_hierarchical_constraints')
    op.drop_table('powell_hierarchical_constraints')

    # Drop powell_value_function
    op.drop_constraint('uq_powell_vf_state', 'powell_value_function', type_='unique')
    op.drop_index('idx_vf_updated', table_name='powell_value_function')
    op.drop_index('idx_vf_agent_type', table_name='powell_value_function')
    op.drop_index('idx_vf_config_state', table_name='powell_value_function')
    op.drop_table('powell_value_function')

    # Drop powell_policy_parameters
    op.drop_index('idx_policy_active', table_name='powell_policy_parameters')
    op.drop_index('idx_policy_entity', table_name='powell_policy_parameters')
    op.drop_index('idx_policy_config_type', table_name='powell_policy_parameters')
    op.drop_table('powell_policy_parameters')

    # Drop powell_belief_state
    op.drop_index('idx_belief_updated', table_name='powell_belief_state')
    op.drop_index('idx_belief_config', table_name='powell_belief_state')
    op.drop_index('idx_belief_entity', table_name='powell_belief_state')
    op.drop_table('powell_belief_state')
