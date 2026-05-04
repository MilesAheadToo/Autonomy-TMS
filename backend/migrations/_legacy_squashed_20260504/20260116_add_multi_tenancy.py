"""Add multi-tenancy tables and tenant_id columns

Revision ID: 20260116_tenancy
Revises: 20260115_add_sso
Create Date: 2026-01-16 07:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20260116_tenancy'
down_revision = '20260115_add_sso'
branch_labels = None
depends_on = None


def upgrade():
    # Create tenants table
    op.create_table(
        'tenants',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('display_name', sa.String(length=200), nullable=True),
        sa.Column('slug', sa.String(length=100), nullable=False),
        sa.Column('subdomain', sa.String(length=50), nullable=False),
        sa.Column('custom_domain', sa.String(length=200), nullable=True),
        sa.Column('logo_url', sa.String(length=500), nullable=True),
        sa.Column('primary_color', sa.String(length=7), nullable=True),
        sa.Column('secondary_color', sa.String(length=7), nullable=True),
        sa.Column('favicon_url', sa.String(length=500), nullable=True),
        sa.Column('contact_email', sa.String(length=255), nullable=True),
        sa.Column('contact_phone', sa.String(length=50), nullable=True),
        sa.Column('billing_email', sa.String(length=255), nullable=True),
        sa.Column('address_line1', sa.String(length=255), nullable=True),
        sa.Column('address_line2', sa.String(length=255), nullable=True),
        sa.Column('city', sa.String(length=100), nullable=True),
        sa.Column('state', sa.String(length=100), nullable=True),
        sa.Column('postal_code', sa.String(length=20), nullable=True),
        sa.Column('country', sa.String(length=100), nullable=True),
        sa.Column('status', sa.Enum('trial', 'active', 'suspended', 'cancelled', name='tenantstatus'), nullable=False, default='trial'),
        sa.Column('billing_plan', sa.Enum('free', 'starter', 'professional', 'enterprise', 'custom', name='billingplan'), nullable=False, default='free'),
        sa.Column('stripe_customer_id', sa.String(length=255), nullable=True),
        sa.Column('stripe_subscription_id', sa.String(length=255), nullable=True),
        sa.Column('trial_ends_at', sa.DateTime(), nullable=True),
        sa.Column('subscription_ends_at', sa.DateTime(), nullable=True),
        sa.Column('max_users', sa.Integer(), nullable=False, default=50),
        sa.Column('max_games', sa.Integer(), nullable=False, default=100),
        sa.Column('max_supply_chain_configs', sa.Integer(), nullable=False, default=10),
        sa.Column('max_storage_mb', sa.Integer(), nullable=False, default=1000),
        sa.Column('current_user_count', sa.Integer(), nullable=False, default=0),
        sa.Column('current_game_count', sa.Integer(), nullable=False, default=0),
        sa.Column('current_config_count', sa.Integer(), nullable=False, default=0),
        sa.Column('current_storage_mb', sa.Integer(), nullable=False, default=0),
        sa.Column('features', sa.JSON(), nullable=True),
        sa.Column('settings', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('owner_id', sa.Integer(), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['groups.id'], ),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tenants_id', 'tenants', ['id'])
    op.create_index('ix_tenants_name', 'tenants', ['name'])
    op.create_index('ix_tenants_slug', 'tenants', ['slug'], unique=True)
    op.create_index('ix_tenants_subdomain', 'tenants', ['subdomain'], unique=True)
    op.create_index('ix_tenants_custom_domain', 'tenants', ['custom_domain'], unique=True)
    op.create_index('ix_tenants_status', 'tenants', ['status'])
    op.create_index('ix_tenants_stripe_customer_id', 'tenants', ['stripe_customer_id'], unique=True)
    op.create_index('ix_tenants_created_at', 'tenants', ['created_at'])

    # Create tenant_invitations table
    op.create_table(
        'tenant_invitations',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=False, default='PLAYER'),
        sa.Column('token', sa.String(length=255), nullable=False),
        sa.Column('status', sa.Enum('pending', 'accepted', 'expired', 'revoked', name='invitation_status'), nullable=False, default='pending'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('accepted_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('invited_by_id', sa.Integer(), nullable=True),
        sa.Column('accepted_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['accepted_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['invited_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tenant_invitations_id', 'tenant_invitations', ['id'])
    op.create_index('ix_tenant_invitations_tenant_id', 'tenant_invitations', ['tenant_id'])
    op.create_index('ix_tenant_invitations_email', 'tenant_invitations', ['email'])
    op.create_index('ix_tenant_invitations_token', 'tenant_invitations', ['token'], unique=True)
    op.create_index('ix_tenant_invitations_status', 'tenant_invitations', ['status'])
    op.create_index('ix_tenant_invitations_created_at', 'tenant_invitations', ['created_at'])
    op.create_index('ix_tenant_invitations_expires_at', 'tenant_invitations', ['expires_at'])

    # Create tenant_usage_logs table
    op.create_table(
        'tenant_usage_logs',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_count', sa.Integer(), default=0),
        sa.Column('game_count', sa.Integer(), default=0),
        sa.Column('config_count', sa.Integer(), default=0),
        sa.Column('storage_mb', sa.Integer(), default=0),
        sa.Column('api_requests_count', sa.Integer(), default=0),
        sa.Column('recorded_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tenant_usage_logs_id', 'tenant_usage_logs', ['id'])
    op.create_index('ix_tenant_usage_logs_tenant_id', 'tenant_usage_logs', ['tenant_id'])
    op.create_index('ix_tenant_usage_logs_recorded_at', 'tenant_usage_logs', ['recorded_at'])

    # Add tenant_id to users table
    op.add_column('users', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_index('ix_users_tenant_id', 'users', ['tenant_id'])
    op.create_foreign_key('fk_users_tenant_id', 'users', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE')

    # Add tenant_id to games table (if column doesn't exist)
    # Check if column exists first to avoid errors
    try:
        op.add_column('games', sa.Column('tenant_id', sa.Integer(), nullable=True))
        op.create_index('ix_games_tenant_id', 'games', ['tenant_id'])
        op.create_foreign_key('fk_games_tenant_id', 'games', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE')
    except:
        pass  # Column may already exist

    # Add tenant_id to supply_chain_configs table
    try:
        op.add_column('supply_chain_configs', sa.Column('tenant_id', sa.Integer(), nullable=True))
        op.create_index('ix_supply_chain_configs_tenant_id', 'supply_chain_configs', ['tenant_id'])
        op.create_foreign_key('fk_supply_chain_configs_tenant_id', 'supply_chain_configs', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE')
    except:
        pass

    # Add tenant_id to sso_providers table
    op.add_column('sso_providers', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_index('ix_sso_providers_tenant_id', 'sso_providers', ['tenant_id'])
    op.create_foreign_key('fk_sso_providers_tenant_id', 'sso_providers', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE')


def downgrade():
    # Remove foreign keys and indexes first
    op.drop_constraint('fk_sso_providers_tenant_id', 'sso_providers', type_='foreignkey')
    op.drop_index('ix_sso_providers_tenant_id', 'sso_providers')
    op.drop_column('sso_providers', 'tenant_id')

    try:
        op.drop_constraint('fk_supply_chain_configs_tenant_id', 'supply_chain_configs', type_='foreignkey')
        op.drop_index('ix_supply_chain_configs_tenant_id', 'supply_chain_configs')
        op.drop_column('supply_chain_configs', 'tenant_id')
    except:
        pass

    try:
        op.drop_constraint('fk_games_tenant_id', 'games', type_='foreignkey')
        op.drop_index('ix_games_tenant_id', 'games')
        op.drop_column('games', 'tenant_id')
    except:
        pass

    op.drop_constraint('fk_users_tenant_id', 'users', type_='foreignkey')
    op.drop_index('ix_users_tenant_id', 'users')
    op.drop_column('users', 'tenant_id')

    # Drop tables
    op.drop_table('tenant_usage_logs')
    op.drop_table('tenant_invitations')
    op.drop_table('tenants')

    # Drop enum types
    op.execute('DROP TYPE IF EXISTS tenantstatus')
    op.execute('DROP TYPE IF EXISTS billingplan')
    op.execute('DROP TYPE IF EXISTS invitation_status')
