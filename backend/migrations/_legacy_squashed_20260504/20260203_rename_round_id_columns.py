"""rename round_id to scenario_round_id

Part of terminology migration. Tables with round_id that should be
scenario_round_id to match the model.

Revision ID: 20260203_rename_round_id
Revises: 20260203_rename_player_id
Create Date: 2026-02-03 14:22:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260203_rename_round_id'
down_revision = '20260203_rename_player_id'
branch_labels = None
depends_on = None


def upgrade():
    # Rename round_id to scenario_round_id in participant_rounds
    op.alter_column(
        'participant_rounds',
        'round_id',
        new_column_name='scenario_round_id'
    )


def downgrade():
    op.alter_column(
        'participant_rounds',
        'scenario_round_id',
        new_column_name='round_id'
    )
