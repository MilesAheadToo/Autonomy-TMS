"""fix participant_actions player_id to participant_id

Part of the terminology migration from player -> participant.
The participant_actions table still has player_id column but the
model expects participant_id.

Revision ID: 20260203_fix_participant_actions
Revises: 20260203_vendor_product_id_to_string
Create Date: 2026-02-03 14:10:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260203_fix_participant_actions'
down_revision = '20260203_vendor_product_id_to_string'
branch_labels = None
depends_on = None


def upgrade():
    # Rename player_id to participant_id in participant_actions table
    op.alter_column(
        'participant_actions',
        'player_id',
        new_column_name='participant_id'
    )


def downgrade():
    # Revert participant_id back to player_id
    op.alter_column(
        'participant_actions',
        'participant_id',
        new_column_name='player_id'
    )
