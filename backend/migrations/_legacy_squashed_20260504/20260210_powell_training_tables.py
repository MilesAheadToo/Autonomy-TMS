"""Powell Training Configuration and Run Tracking tables

Revision ID: 20260210_powell_training
Revises: None (standalone)
Create Date: 2026-02-10

Creates tables for configuring and tracking Powell AI model training:
- powell_training_config: Master training configuration (S&OP, tGNN, TRM params)
- trm_training_config: Per-TRM-type training configuration
- powell_training_run: Training run execution log
"""
from alembic import op
import sqlalchemy as sa

revision = '20260210_powell_training'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create enum types
    trm_type_enum = sa.Enum(
        'atp_executor', 'rebalancing', 'po_creation', 'order_tracking',
        name='trm_type_enum'
    )
    trm_type_enum.create(op.get_bind(), checkfirst=True)

    training_status_enum = sa.Enum(
        'pending', 'generating_data', 'training_sop', 'training_tgnn',
        'training_trm', 'completed', 'failed',
        name='training_status_enum'
    )
    training_status_enum.create(op.get_bind(), checkfirst=True)

    # 1. powell_training_config
    op.create_table(
        'powell_training_config',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),

        # Ownership
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id'), nullable=False, index=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),

        # Hierarchy config links
        sa.Column('sop_hierarchy_config_id', sa.Integer(),
                  sa.ForeignKey('planning_hierarchy_config.id'), nullable=True),
        sa.Column('execution_hierarchy_config_id', sa.Integer(),
                  sa.ForeignKey('planning_hierarchy_config.id'), nullable=True),

        # Data generation
        sa.Column('num_simulation_runs', sa.Integer(), nullable=False, server_default='128'),
        sa.Column('timesteps_per_run', sa.Integer(), nullable=False, server_default='64'),
        sa.Column('history_window', sa.Integer(), nullable=False, server_default='52'),
        sa.Column('forecast_horizon', sa.Integer(), nullable=False, server_default='8'),
        sa.Column('demand_patterns', sa.JSON(), nullable=True),

        # S&OP GraphSAGE
        sa.Column('train_sop_graphsage', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('sop_hidden_dim', sa.Integer(), nullable=False, server_default='128'),
        sa.Column('sop_embedding_dim', sa.Integer(), nullable=False, server_default='64'),
        sa.Column('sop_num_layers', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('sop_epochs', sa.Integer(), nullable=False, server_default='50'),
        sa.Column('sop_learning_rate', sa.Float(), nullable=False, server_default='0.001'),
        sa.Column('sop_batch_size', sa.Integer(), nullable=False, server_default='32'),
        sa.Column('sop_retrain_frequency_hours', sa.Integer(), nullable=False, server_default='168'),

        # Execution tGNN
        sa.Column('train_execution_tgnn', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('tgnn_hidden_dim', sa.Integer(), nullable=False, server_default='128'),
        sa.Column('tgnn_window_size', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('tgnn_num_layers', sa.Integer(), nullable=False, server_default='2'),
        sa.Column('tgnn_epochs', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('tgnn_learning_rate', sa.Float(), nullable=False, server_default='0.001'),
        sa.Column('tgnn_batch_size', sa.Integer(), nullable=False, server_default='32'),
        sa.Column('tgnn_retrain_frequency_hours', sa.Integer(), nullable=False, server_default='24'),

        # TRM
        sa.Column('trm_training_method', sa.String(50), nullable=False, server_default='hybrid'),
        sa.Column('trm_bc_epochs', sa.Integer(), nullable=False, server_default='20'),
        sa.Column('trm_rl_epochs', sa.Integer(), nullable=False, server_default='80'),
        sa.Column('trm_learning_rate', sa.Float(), nullable=False, server_default='0.0001'),
        sa.Column('trm_batch_size', sa.Integer(), nullable=False, server_default='64'),

        # State
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),

        # Last run info
        sa.Column('last_training_started', sa.DateTime(), nullable=True),
        sa.Column('last_training_completed', sa.DateTime(), nullable=True),
        sa.Column('last_training_status', sa.String(50), nullable=True),
        sa.Column('last_training_error', sa.Text(), nullable=True),
    )

    # 2. trm_training_config
    op.create_table(
        'trm_training_config',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('powell_config_id', sa.Integer(),
                  sa.ForeignKey('powell_training_config.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('trm_type', trm_type_enum, nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),

        # Architecture
        sa.Column('state_dim', sa.Integer(), nullable=False, server_default='26'),
        sa.Column('hidden_dim', sa.Integer(), nullable=False, server_default='128'),
        sa.Column('num_heads', sa.Integer(), nullable=False, server_default='4'),
        sa.Column('num_layers', sa.Integer(), nullable=False, server_default='2'),

        # Training
        sa.Column('epochs', sa.Integer(), nullable=True),
        sa.Column('learning_rate', sa.Float(), nullable=True),
        sa.Column('batch_size', sa.Integer(), nullable=True),
        sa.Column('reward_weights', sa.JSON(), nullable=True),
        sa.Column('retrain_frequency_hours', sa.Integer(), nullable=False, server_default='24'),
        sa.Column('min_training_samples', sa.Integer(), nullable=False, server_default='1000'),

        # State
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),

        # Last run
        sa.Column('last_trained', sa.DateTime(), nullable=True),
        sa.Column('last_training_samples', sa.Integer(), nullable=True),
        sa.Column('last_training_loss', sa.Float(), nullable=True),
        sa.Column('model_checkpoint_path', sa.String(255), nullable=True),
    )

    # 3. powell_training_run
    op.create_table(
        'powell_training_run',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('powell_config_id', sa.Integer(),
                  sa.ForeignKey('powell_training_config.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('status', training_status_enum, nullable=False, server_default='pending'),
        sa.Column('started_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime(), nullable=True),

        # Progress
        sa.Column('current_phase', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('progress_percent', sa.Float(), nullable=False, server_default='0'),

        # Data generation
        sa.Column('samples_generated', sa.Integer(), nullable=True),
        sa.Column('data_generation_time_seconds', sa.Float(), nullable=True),

        # S&OP results
        sa.Column('sop_epochs_completed', sa.Integer(), nullable=True),
        sa.Column('sop_final_loss', sa.Float(), nullable=True),
        sa.Column('sop_training_time_seconds', sa.Float(), nullable=True),
        sa.Column('sop_checkpoint_path', sa.String(255), nullable=True),

        # tGNN results
        sa.Column('tgnn_epochs_completed', sa.Integer(), nullable=True),
        sa.Column('tgnn_final_loss', sa.Float(), nullable=True),
        sa.Column('tgnn_training_time_seconds', sa.Float(), nullable=True),
        sa.Column('tgnn_checkpoint_path', sa.String(255), nullable=True),

        # TRM results
        sa.Column('trm_results', sa.JSON(), nullable=True),

        # Error
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('error_phase', sa.String(50), nullable=True),

        # User
        sa.Column('triggered_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
    )


def downgrade():
    op.drop_table('powell_training_run')
    op.drop_table('trm_training_config')
    op.drop_table('powell_training_config')

    sa.Enum(name='training_status_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='trm_type_enum').drop(op.get_bind(), checkfirst=True)
