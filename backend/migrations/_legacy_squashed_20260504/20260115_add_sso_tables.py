"""Add SSO tables for Option 1 Enterprise Features

Revision ID: 20260115_add_sso
Revises: 20260322093000
Create Date: 2026-01-15 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20260115_add_sso'
down_revision = '99b1d0fb8f3a'
branch_labels = None
depends_on = None


def upgrade():
    # Create sso_providers table
    op.create_table(
        'sso_providers',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('slug', sa.String(length=50), nullable=False),
        sa.Column('type', sa.Enum('saml', 'oauth2', 'ldap', name='ssoprovidertype'), nullable=False),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, default=True),
        sa.Column('allowed_domains', sa.JSON(), nullable=True),
        sa.Column('auto_create_users', sa.Boolean(), nullable=False, default=True),
        sa.Column('default_user_type', sa.String(length=50), nullable=False, default='PLAYER'),
        sa.Column('default_group_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['default_group_id'], ['groups.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sso_providers_id', 'sso_providers', ['id'])
    op.create_index('ix_sso_providers_slug', 'sso_providers', ['slug'], unique=True)

    # Create user_sso_mappings table
    op.create_table(
        'user_sso_mappings',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=False),
        sa.Column('external_email', sa.String(length=255), nullable=True),
        sa.Column('external_name', sa.String(length=255), nullable=True),
        sa.Column('external_attributes', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_sync', sa.DateTime(), nullable=True),
        sa.Column('last_login', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['provider_id'], ['sso_providers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_user_sso_mappings_id', 'user_sso_mappings', ['id'])
    op.create_index('ix_user_sso_mappings_user_id', 'user_sso_mappings', ['user_id'])
    op.create_index('ix_user_sso_mappings_provider_id', 'user_sso_mappings', ['provider_id'])
    op.create_index('ix_user_sso_mappings_external_id', 'user_sso_mappings', ['external_id'])

    # Create sso_login_attempts table
    op.create_table(
        'sso_login_attempts',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=True),
        sa.Column('external_email', sa.String(length=255), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False, default=False),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('attempted_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['provider_id'], ['sso_providers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sso_login_attempts_id', 'sso_login_attempts', ['id'])
    op.create_index('ix_sso_login_attempts_provider_id', 'sso_login_attempts', ['provider_id'])
    op.create_index('ix_sso_login_attempts_user_id', 'sso_login_attempts', ['user_id'])
    op.create_index('ix_sso_login_attempts_external_id', 'sso_login_attempts', ['external_id'])
    op.create_index('ix_sso_login_attempts_success', 'sso_login_attempts', ['success'])
    op.create_index('ix_sso_login_attempts_attempted_at', 'sso_login_attempts', ['attempted_at'])


def downgrade():
    # Drop tables in reverse order
    op.drop_table('sso_login_attempts')
    op.drop_table('user_sso_mappings')
    op.drop_table('sso_providers')

    # Drop enum type
    op.execute('DROP TYPE IF EXISTS ssoprovidertype')
