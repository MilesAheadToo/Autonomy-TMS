"""Add MPS (Master Production Scheduling) Permissions

Revision ID: 20260119_add_mps_permissions
Revises: previous_migration
Create Date: 2026-01-19

Adds permissions for Master Production Scheduling functionality:
- mps.view: View MPS plans
- mps.manage: Create and edit MPS plans
- mps.approve: Approve MPS plans for execution
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers
revision = '20260119_add_mps_permissions'
down_revision = '20260119_rename_aws_sc'
branch_labels = None
depends_on = None


def upgrade():
    """Add MPS permissions to the database"""

    # Get connection and metadata
    conn = op.get_bind()

    # Check if permissions table exists
    inspector = sa.inspect(conn)
    if 'permissions' not in inspector.get_table_names():
        print("⚠️  Permissions table does not exist. Skipping MPS permission seeding.")
        return

    # Insert MPS permissions
    mps_permissions = [
        {
            'name': 'mps.view',
            'resource': 'mps',
            'action': 'view',
            'description': 'View Master Production Scheduling plans',
            'category': 'Planning',
            'is_system': True,
            'created_at': datetime.utcnow()
        },
        {
            'name': 'mps.manage',
            'resource': 'mps',
            'action': 'manage',
            'description': 'Create and edit Master Production Scheduling plans',
            'category': 'Planning',
            'is_system': True,
            'created_at': datetime.utcnow()
        },
        {
            'name': 'mps.approve',
            'resource': 'mps',
            'action': 'approve',
            'description': 'Approve Master Production Scheduling plans for execution',
            'category': 'Planning',
            'is_system': True,
            'created_at': datetime.utcnow()
        },
    ]

    # Check which permissions already exist
    existing_permissions = conn.execute(
        sa.text("SELECT name FROM permissions WHERE name LIKE 'mps.%'")
    ).fetchall()
    existing_names = {row[0] for row in existing_permissions}

    # Insert only new permissions
    for perm in mps_permissions:
        if perm['name'] not in existing_names:
            conn.execute(
                sa.text("""
                    INSERT INTO permissions (name, resource, action, description, category, is_system, created_at)
                    VALUES (:name, :resource, :action, :description, :category, :is_system, :created_at)
                """),
                perm
            )
            print(f"✅ Added permission: {perm['name']}")
        else:
            print(f"⏭️  Permission already exists: {perm['name']}")

    # Assign MPS permissions to GROUP_ADMIN role
    # First, check if roles table exists
    if 'roles' in inspector.get_table_names():
        # Get Customer Admin role ID
        group_admin_role = conn.execute(
            sa.text("SELECT id FROM roles WHERE slug = 'group-admin' LIMIT 1")
        ).fetchone()

        if group_admin_role:
            role_id = group_admin_role[0]

            # Get MPS permission IDs
            mps_perm_ids = conn.execute(
                sa.text("SELECT id FROM permissions WHERE name LIKE 'mps.%'")
            ).fetchall()

            # Assign each permission to Customer Admin role
            for perm_id_tuple in mps_perm_ids:
                perm_id = perm_id_tuple[0]

                # Check if already assigned
                existing = conn.execute(
                    sa.text("""
                        SELECT 1 FROM role_permissions
                        WHERE role_id = :role_id AND permission_id = :perm_id
                    """),
                    {'role_id': role_id, 'perm_id': perm_id}
                ).fetchone()

                if not existing:
                    conn.execute(
                        sa.text("""
                            INSERT INTO role_permissions (role_id, permission_id, granted, created_at)
                            VALUES (:role_id, :perm_id, true, :created_at)
                        """),
                        {'role_id': role_id, 'perm_id': perm_id, 'created_at': datetime.utcnow()}
                    )
                    print(f"✅ Assigned permission {perm_id} to Customer Admin role")
                else:
                    print(f"⏭️  Permission {perm_id} already assigned to Customer Admin role")
        else:
            print("⚠️  Customer Admin role not found. Permissions created but not assigned.")
    else:
        print("⚠️  Roles table does not exist. Permissions created but not assigned.")

    print("✅ MPS permissions migration completed")


def downgrade():
    """Remove MPS permissions from the database"""

    conn = op.get_bind()

    # Delete role-permission associations
    conn.execute(
        sa.text("""
            DELETE FROM role_permissions
            WHERE permission_id IN (
                SELECT id FROM permissions WHERE name LIKE 'mps.%'
            )
        """)
    )
    print("✅ Removed MPS permission assignments")

    # Delete permissions
    conn.execute(
        sa.text("DELETE FROM permissions WHERE name LIKE 'mps.%'")
    )
    print("✅ Removed MPS permissions")

    print("✅ MPS permissions downgrade completed")
