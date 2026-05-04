"""Add RBAC tables

Revision ID: 20260116_rbac
Revises: 20260116_tenancy
Create Date: 2026-01-16 07:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20260116_rbac'
down_revision = '20260116_tenancy'
branch_labels = None
depends_on = None


def upgrade():
    # Create permissions table
    op.create_table(
        'permissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('resource', sa.String(50), nullable=False),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(50), nullable=True),
        sa.Column('is_system', sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_permission_name'),
        sa.UniqueConstraint('resource', 'action', name='uq_resource_action')
    )
    op.create_index('ix_permissions_id', 'permissions', ['id'])
    op.create_index('ix_permissions_name', 'permissions', ['name'])
    op.create_index('ix_permissions_resource', 'permissions', ['resource'])
    op.create_index('ix_permissions_action', 'permissions', ['action'])

    # Create roles table
    op.create_table(
        'roles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('slug', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_system', sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.UniqueConstraint('slug', 'tenant_id', name='uq_role_slug_tenant')
    )
    op.create_index('ix_roles_id', 'roles', ['id'])
    op.create_index('ix_roles_name', 'roles', ['name'])
    op.create_index('ix_roles_slug', 'roles', ['slug'])
    op.create_index('ix_roles_tenant_id', 'roles', ['tenant_id'])

    # Create role_permissions association table
    op.create_table(
        'role_permissions',
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.Column('permission_id', sa.Integer(), nullable=False),
        sa.Column('granted', sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('role_id', 'permission_id'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], ondelete='CASCADE')
    )

    # Create user_roles association table
    op.create_table(
        'user_roles',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.Column('assigned_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('assigned_by', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('user_id', 'role_id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_by'], ['users.id'])
    )

    # Create role_permission_grants audit table
    op.create_table(
        'role_permission_grants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.Column('permission_id', sa.Integer(), nullable=False),
        sa.Column('granted', sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column('granted_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('granted_by', sa.Integer(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_by', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['granted_by'], ['users.id']),
        sa.ForeignKeyConstraint(['revoked_by'], ['users.id'])
    )
    op.create_index('ix_role_permission_grants_id', 'role_permission_grants', ['id'])
    op.create_index('ix_role_permission_grants_role_id', 'role_permission_grants', ['role_id'])
    op.create_index('ix_role_permission_grants_permission_id', 'role_permission_grants', ['permission_id'])
    op.create_index('ix_role_permission_grants_granted_at', 'role_permission_grants', ['granted_at'])

    # Create user_role_assignments audit table
    op.create_table(
        'user_role_assignments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.Column('assigned_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('assigned_by', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_by', sa.Integer(), nullable=True),
        sa.Column('scope', sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_by'], ['users.id']),
        sa.ForeignKeyConstraint(['revoked_by'], ['users.id'])
    )
    op.create_index('ix_user_role_assignments_id', 'user_role_assignments', ['id'])
    op.create_index('ix_user_role_assignments_user_id', 'user_role_assignments', ['user_id'])
    op.create_index('ix_user_role_assignments_role_id', 'user_role_assignments', ['role_id'])
    op.create_index('ix_user_role_assignments_assigned_at', 'user_role_assignments', ['assigned_at'])
    op.create_index('ix_user_role_assignments_expires_at', 'user_role_assignments', ['expires_at'])


def downgrade():
    op.drop_table('user_role_assignments')
    op.drop_table('role_permission_grants')
    op.drop_table('user_roles')
    op.drop_table('role_permissions')
    op.drop_table('roles')
    op.drop_table('permissions')
