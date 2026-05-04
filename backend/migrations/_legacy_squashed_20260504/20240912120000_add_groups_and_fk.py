"""add customers table and customer relationships

Revision ID: 20240912120000
Revises: 20240910152000
Create Date: 2025-09-12 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20240912120000'
down_revision = '20240910152000'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'groups',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('logo', sa.String(length=255), nullable=True),
        sa.Column('admin_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['admin_id'], ['users.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('admin_id')
    )

    op.add_column('users', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_users_group', 'users', 'groups', ['customer_id'], ['id'], ondelete='CASCADE')

    op.add_column('supply_chain_configs', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_scc_group', 'supply_chain_configs', 'groups', ['customer_id'], ['id'], ondelete='CASCADE')

    op.add_column('games', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_games_group', 'games', 'groups', ['customer_id'], ['id'], ondelete='CASCADE')

def downgrade():
    op.drop_constraint('fk_games_group', 'games', type_='foreignkey')
    op.drop_column('games', 'customer_id')

    op.drop_constraint('fk_scc_group', 'supply_chain_configs', type_='foreignkey')
    op.drop_column('supply_chain_configs', 'customer_id')

    op.drop_constraint('fk_users_group', 'users', type_='foreignkey')
    op.drop_column('users', 'customer_id')

    op.drop_table('groups')
