"""rename remaining player_id columns to participant_id

Complete the terminology migration from player -> participant.
Multiple tables still have player_id that should be participant_id.

Tables affected:
- agent_mode_history
- agent_performance_logs
- orders
- participant_inventory
- participant_rounds
- rlhf_feedback

Revision ID: 20260203_rename_player_id
Revises: 20260203_add_user_scope
Create Date: 2026-02-03 14:20:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260203_rename_player_id'
down_revision = '20260203_add_user_scope'
branch_labels = None
depends_on = None

# Tables that need player_id -> participant_id rename
TABLES_TO_RENAME = [
    'agent_mode_history',
    'agent_performance_logs',
    'orders',
    'participant_inventory',
    'participant_rounds',
    'rlhf_feedback',
]


def upgrade():
    for table in TABLES_TO_RENAME:
        op.alter_column(
            table,
            'player_id',
            new_column_name='participant_id'
        )


def downgrade():
    for table in TABLES_TO_RENAME:
        op.alter_column(
            table,
            'participant_id',
            new_column_name='player_id'
        )
