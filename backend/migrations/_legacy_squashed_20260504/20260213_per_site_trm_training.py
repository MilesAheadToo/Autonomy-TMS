"""Per-site TRM training with learning-depth curriculum

Revision ID: 20260213_per_site_trm
Revises: 20260213_rename_dqs_cli
Create Date: 2026-02-13

Changes:
- Add 'safety_stock' to trm_type_enum (was missing from original migration)
- Add site_id + index to trm_replay_buffer
- Add trm_site_results column to powell_training_run
- Create trm_site_training_config table (per-site × per-TRM-type progress)
- Create trm_base_model table (cold-start base models)
"""
from alembic import op
import sqlalchemy as sa

revision = '20260213_per_site_trm'
down_revision = '20260213_rename_dqs_cli'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add 'safety_stock' to trm_type_enum if missing
    op.execute("ALTER TYPE trm_type_enum ADD VALUE IF NOT EXISTS 'safety_stock'")

    # 2. Add site_id to trm_replay_buffer
    op.add_column('trm_replay_buffer',
                  sa.Column('site_id', sa.Integer(), nullable=True))
    op.create_index('idx_replay_buffer_site_trm',
                    'trm_replay_buffer', ['site_id', 'trm_type'])

    # 3. Add trm_site_results to powell_training_run
    op.add_column('powell_training_run',
                  sa.Column('trm_site_results', sa.JSON(), nullable=True))

    # 4. Create trm_site_training_config
    op.create_table(
        'trm_site_training_config',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),

        sa.Column('powell_config_id', sa.Integer(),
                  sa.ForeignKey('powell_training_config.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('site.id'),
                  nullable=False, index=True),
        sa.Column('site_name', sa.String(100), nullable=False),
        sa.Column('master_type', sa.String(50), nullable=False),

        sa.Column('trm_type', sa.String(50), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),

        # Phase 1: Engine Imitation (BC)
        sa.Column('phase1_status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('phase1_epochs_completed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('phase1_epochs_target', sa.Integer(), nullable=False, server_default='20'),
        sa.Column('phase1_loss', sa.Float(), nullable=True),
        sa.Column('phase1_accuracy', sa.Float(), nullable=True),

        # Phase 2: Context Learning (Supervised)
        sa.Column('phase2_status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('phase2_epochs_completed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('phase2_epochs_target', sa.Integer(), nullable=False, server_default='50'),
        sa.Column('phase2_loss', sa.Float(), nullable=True),
        sa.Column('phase2_accuracy', sa.Float(), nullable=True),
        sa.Column('phase2_expert_samples', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('phase2_min_samples', sa.Integer(), nullable=False, server_default='500'),

        # Phase 3: Outcome Optimization (RL/VFA)
        sa.Column('phase3_status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('phase3_epochs_completed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('phase3_epochs_target', sa.Integer(), nullable=False, server_default='80'),
        sa.Column('phase3_loss', sa.Float(), nullable=True),
        sa.Column('phase3_reward_mean', sa.Float(), nullable=True),
        sa.Column('phase3_outcome_samples', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('phase3_min_samples', sa.Integer(), nullable=False, server_default='1000'),

        # Model state
        sa.Column('model_checkpoint_path', sa.String(255), nullable=True),
        sa.Column('model_version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('eval_accuracy', sa.Float(), nullable=True),
        sa.Column('eval_vs_engine_improvement', sa.Float(), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('last_trained_at', sa.DateTime(), nullable=True),

        # Constraints
        sa.UniqueConstraint('powell_config_id', 'site_id', 'trm_type',
                            name='uq_site_trm_config'),
    )
    op.create_index('idx_site_training_config_site',
                    'trm_site_training_config', ['site_id', 'trm_type'])

    # 5. Create trm_base_model
    op.create_table(
        'trm_base_model',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id'),
                  nullable=False, index=True),
        sa.Column('master_type', sa.String(50), nullable=False),
        sa.Column('trm_type', sa.String(50), nullable=False),

        sa.Column('checkpoint_path', sa.String(255), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('sites_trained_on', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_samples', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('final_loss', sa.Float(), nullable=True),

        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),

        sa.UniqueConstraint('customer_id', 'master_type', 'trm_type',
                            name='uq_base_model_group_type'),
    )


def downgrade():
    op.drop_table('trm_base_model')
    op.drop_table('trm_site_training_config')
    op.drop_column('powell_training_run', 'trm_site_results')
    op.drop_index('idx_replay_buffer_site_trm', table_name='trm_replay_buffer')
    op.drop_column('trm_replay_buffer', 'site_id')
