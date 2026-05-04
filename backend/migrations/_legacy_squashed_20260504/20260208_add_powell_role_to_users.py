"""Add powell_role to users table

Revision ID: 20260208_powell_role
Revises:
Create Date: 2026-02-08

Powell Framework Enhancement:
- powell_role determines the user's landing page (fixed)
- capabilities can be customized by group admin (flexible)
- This separates routing from permissions
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '20260208_powell_role'
down_revision = '20260207_po_scenario'
branch_labels = None
depends_on = None


def upgrade():
    # Create the enum type if it doesn't exist
    # Using raw SQL for PostgreSQL enum creation with IF NOT EXISTS
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'powell_role_enum') THEN
                CREATE TYPE powell_role_enum AS ENUM ('SC_VP', 'SOP_DIRECTOR', 'MPS_MANAGER', 'DEMO_ALL');
            END IF;
        END
        $$;
    """)

    # Add the column if it doesn't exist
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'powell_role'
            ) THEN
                ALTER TABLE users ADD COLUMN powell_role powell_role_enum;
                COMMENT ON COLUMN users.powell_role IS 'Powell role determines landing page; capabilities can be customized';
            END IF;
        END
        $$;
    """)

    # Create index if it doesn't exist
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_users_powell_role ON users (powell_role);
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_users_powell_role;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS powell_role;")
    op.execute("DROP TYPE IF EXISTS powell_role_enum;")
