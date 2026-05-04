"""Introduce user_type enum and migrate legacy roles

Revision ID: 20241001120000
Revises: 20240920160000
Create Date: 2025-10-01 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
import json


# revision identifiers, used by Alembic.
revision = '20241001120000'
down_revision = '20240920160000'
branch_labels = None
depends_on = None


def _normalize_token(value):
    if value is None:
        return ''
    return ''.join(ch for ch in str(value).lower() if ch.isalnum())


def upgrade() -> None:
    bind = op.get_bind()

    user_type_enum = sa.Enum('SYSTEM_ADMIN', 'GROUP_ADMIN', 'PLAYER', name='user_type_enum')
    user_type_enum.create(bind, checkfirst=True)

    op.add_column(
        'users',
        sa.Column('user_type', user_type_enum, nullable=False, server_default='PLAYER'),
    )

    users_table = sa.table(
        'users',
        sa.column('id', sa.Integer),
        sa.column('user_type', user_type_enum),
        sa.column('is_superuser', sa.Boolean),
        sa.column('roles', sa.JSON),
    )

    result = bind.execute(sa.select(users_table.c.id, users_table.c.is_superuser, users_table.c.roles))

    for row in result:
        if row.is_superuser:
            new_type = 'SYSTEM_ADMIN'
        else:
            roles_value = row.roles or []
            if isinstance(roles_value, str):
                try:
                    roles_value = json.loads(roles_value)
                except json.JSONDecodeError:
                    roles_value = []

            tokens = {_normalize_token(role) for role in roles_value if role is not None}
            if {'systemadmin', 'superadmin'} & tokens:
                new_type = 'SYSTEM_ADMIN'
            elif {'groupadmin', 'admin'} & tokens:
                new_type = 'GROUP_ADMIN'
            else:
                new_type = 'PLAYER'

        values = {'user_type': new_type}
        if new_type == 'SYSTEM_ADMIN' and not row.is_superuser:
            values['is_superuser'] = True

        bind.execute(
            users_table.update()
            .where(users_table.c.id == row.id)
            .values(**values)
        )

    op.drop_column('users', 'roles')


def downgrade() -> None:
    bind = op.get_bind()

    op.add_column('users', sa.Column('roles', sa.JSON, nullable=True))

    users_table = sa.table(
        'users',
        sa.column('id', sa.Integer),
        sa.column('user_type', sa.String(length=50)),
        sa.column('roles', sa.JSON),
    )

    result = bind.execute(sa.select(users_table.c.id, users_table.c.user_type))

    for row in result:
        user_type = row.user_type or 'PLAYER'
        if user_type == 'SYSTEM_ADMIN':
            roles = ['system_admin']
        elif user_type == 'GROUP_ADMIN':
            roles = ['group_admin', 'admin']
        else:
            roles = ['player']

        bind.execute(
            users_table.update()
            .where(users_table.c.id == row.id)
            .values(roles=roles)
        )

    op.drop_column('users', 'user_type')

    user_type_enum = sa.Enum('SYSTEM_ADMIN', 'GROUP_ADMIN', 'PLAYER', name='user_type_enum')
    user_type_enum.drop(bind, checkfirst=True)
