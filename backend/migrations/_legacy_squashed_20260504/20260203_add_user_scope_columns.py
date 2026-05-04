"""add user scope columns

Add site_scope, product_scope, and functional_scope columns to users table
for row-level security and scoping user access to specific sites, products,
and functional areas.

Revision ID: 20260203_add_user_scope
Revises: 20260203_fix_participant_actions
Create Date: 2026-02-03 14:15:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260203_add_user_scope'
down_revision = '20260203_fix_participant_actions'
branch_labels = None
depends_on = None


def upgrade():
    # Add scope columns to users table
    # These are JSON arrays that define which sites/products/functions a user can access
    # NULL means "all" (no restriction)

    op.add_column('users', sa.Column('site_scope', sa.JSON(), nullable=True))
    op.add_column('users', sa.Column('product_scope', sa.JSON(), nullable=True))
    op.add_column('users', sa.Column('functional_scope', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('users', 'functional_scope')
    op.drop_column('users', 'product_scope')
    op.drop_column('users', 'site_scope')
