"""Sprint 4: Add template tables

Revision ID: 20260114_sprint4_templates
Revises: 20260113_performance_indexes
Create Date: 2026-01-14 08:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20260114_sprint4_templates'
down_revision = '20260113_performance_indexes'
branch_labels = None
depends_on = None


def upgrade():
    # Create templates table
    op.create_table(
        'templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('slug', sa.String(length=255), nullable=False),
        sa.Column('category', sa.Enum('distribution', 'scenario', 'game', 'supply_chain', name='templatecategory'), nullable=False),
        sa.Column('industry', sa.Enum('general', 'retail', 'manufacturing', 'logistics', 'healthcare', 'technology', 'food_beverage', 'automotive', name='templateindustry'), nullable=True),
        sa.Column('difficulty', sa.Enum('beginner', 'intermediate', 'advanced', 'expert', name='templatedifficulty'), nullable=True),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('short_description', sa.String(length=500), nullable=True),
        sa.Column('configuration', sa.JSON(), nullable=False),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('icon', sa.String(length=50), nullable=True),
        sa.Column('color', sa.String(length=20), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('usage_count', sa.Integer(), nullable=True, default=0),
        sa.Column('is_featured', sa.Boolean(), nullable=True, default=False),
        sa.Column('is_active', sa.Boolean(), nullable=True, default=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug')
    )
    op.create_index(op.f('ix_templates_name'), 'templates', ['name'], unique=False)
    op.create_index(op.f('ix_templates_slug'), 'templates', ['slug'], unique=False)
    op.create_index(op.f('ix_templates_category'), 'templates', ['category'], unique=False)
    op.create_index(op.f('ix_templates_industry'), 'templates', ['industry'], unique=False)

    # Create tutorial_progress table
    op.create_table(
        'tutorial_progress',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('tutorial_id', sa.String(length=100), nullable=False),
        sa.Column('completed', sa.Boolean(), nullable=True, default=False),
        sa.Column('current_step', sa.Integer(), nullable=True, default=0),
        sa.Column('total_steps', sa.Integer(), nullable=False),
        sa.Column('state', sa.JSON(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('last_accessed', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tutorial_progress_user_id'), 'tutorial_progress', ['user_id'], unique=False)
    op.create_index(op.f('ix_tutorial_progress_tutorial_id'), 'tutorial_progress', ['tutorial_id'], unique=False)

    # Create user_preferences table
    op.create_table(
        'user_preferences',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('theme', sa.String(length=20), nullable=True, default='light'),
        sa.Column('show_tutorials', sa.Boolean(), nullable=True, default=True),
        sa.Column('show_tips', sa.Boolean(), nullable=True, default=True),
        sa.Column('onboarding_completed', sa.Boolean(), nullable=True, default=False),
        sa.Column('quick_start_shown', sa.Boolean(), nullable=True, default=False),
        sa.Column('preferences', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index(op.f('ix_user_preferences_user_id'), 'user_preferences', ['user_id'], unique=False)


def downgrade():
    # Drop tables in reverse order
    op.drop_index(op.f('ix_user_preferences_user_id'), table_name='user_preferences')
    op.drop_table('user_preferences')

    op.drop_index(op.f('ix_tutorial_progress_tutorial_id'), table_name='tutorial_progress')
    op.drop_index(op.f('ix_tutorial_progress_user_id'), table_name='tutorial_progress')
    op.drop_table('tutorial_progress')

    op.drop_index(op.f('ix_templates_industry'), table_name='templates')
    op.drop_index(op.f('ix_templates_category'), table_name='templates')
    op.drop_index(op.f('ix_templates_slug'), table_name='templates')
    op.drop_index(op.f('ix_templates_name'), table_name='templates')
    op.drop_table('templates')

    # Drop enums
    op.execute('DROP TYPE IF EXISTS templatecategory')
    op.execute('DROP TYPE IF EXISTS templateindustry')
    op.execute('DROP TYPE IF EXISTS templatedifficulty')
