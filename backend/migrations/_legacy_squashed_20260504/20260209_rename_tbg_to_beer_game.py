"""Rename TBG config names to Beer Game and beer_game_steps to simulation_steps

Revision ID: 20260209_tbg_rename
Revises: 20260206_site_type
Create Date: 2026-02-09

Renames all supply chain config names, group names, and admin users from
TBG abbreviation to full "Beer Game" naming. Also renames beer_game_steps
table to simulation_steps.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260209_tbg_rename'
down_revision = '20260206_site_type'
branch_labels = None
depends_on = None


# Config name mappings: old -> new
CONFIG_RENAMES = {
    'Default TBG': 'Default Beer Game',
    'Case TBG': 'Case Beer Game',
    'Six-Pack TBG': 'Six-Pack Beer Game',
    'Bottle TBG': 'Bottle Beer Game',
    'Three FG TBG': 'Three FG Beer Game',
    'Variable TBG': 'Variable Beer Game',
    'Multi-Item SixPack TBG': 'Multi-Item SixPack Beer Game',
}

GROUP_RENAMES = {
    'TBG': 'Beer Game',
    'Six-Pack TBG': 'Six-Pack Beer Game',
    'Bottle TBG': 'Bottle Beer Game',
    'Three FG TBG': 'Three FG Beer Game',
    'Variable TBG': 'Variable Beer Game',
    'Multi-Item SixPack TBG': 'Multi-Item SixPack Beer Game',
}

USER_RENAMES = {
    'tbg_admin': ('beer_game_admin', 'beer_game_admin@autonomy.ai', 'Beer Game Administrator'),
    'ThreeTBG_admin': ('ThreeBeerGame_admin', 'ThreeBeerGame_admin@autonomy.ai', 'Three FG Beer Game Administrator'),
    'VarTBG_admin': ('VarBeerGame_admin', 'VarBeerGame_admin@autonomy.ai', 'Variable Beer Game Administrator'),
}


def upgrade():
    # 1. Rename supply chain config names
    for old_name, new_name in CONFIG_RENAMES.items():
        op.execute(
            sa.text("UPDATE supply_chain_configs SET name = :new WHERE name = :old"),
            {"old": old_name, "new": new_name}
        )

    # 2. Rename group names
    for old_name, new_name in GROUP_RENAMES.items():
        op.execute(
            sa.text("UPDATE customers SET name = :new WHERE name = :old"),
            {"old": old_name, "new": new_name}
        )

    # 3. Rename admin users
    for old_username, (new_username, new_email, new_fullname) in USER_RENAMES.items():
        op.execute(
            sa.text(
                "UPDATE users SET username = :new_user, email = :new_email, "
                "full_name = :new_name WHERE username = :old_user"
            ),
            {
                "old_user": old_username,
                "new_user": new_username,
                "new_email": new_email,
                "new_name": new_fullname,
            }
        )

    # 4. Rename beer_game_steps table to simulation_steps (if it exists)
    # This table is dynamically created by the data generator
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT FROM information_schema.tables "
        "WHERE table_name = 'beer_game_steps')"
    ))
    if result.scalar():
        op.rename_table('beer_game_steps', 'simulation_steps')


def downgrade():
    # Reverse config renames
    for old_name, new_name in CONFIG_RENAMES.items():
        op.execute(
            sa.text("UPDATE supply_chain_configs SET name = :old WHERE name = :new"),
            {"old": old_name, "new": new_name}
        )

    # Reverse group renames
    for old_name, new_name in GROUP_RENAMES.items():
        op.execute(
            sa.text("UPDATE customers SET name = :old WHERE name = :new"),
            {"old": old_name, "new": new_name}
        )

    # Reverse user renames
    for old_username, (new_username, new_email, new_fullname) in USER_RENAMES.items():
        op.execute(
            sa.text(
                "UPDATE users SET username = :old_user, email = :old_email, "
                "full_name = :old_name WHERE username = :new_user"
            ),
            {
                "old_user": old_username,
                "old_email": f"{old_username}@autonomy.ai",
                "old_name": f"{old_username} Administrator",
                "new_user": new_username,
            }
        )

    # Reverse table rename
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT FROM information_schema.tables "
        "WHERE table_name = 'simulation_steps')"
    ))
    if result.scalar():
        op.rename_table('simulation_steps', 'beer_game_steps')
