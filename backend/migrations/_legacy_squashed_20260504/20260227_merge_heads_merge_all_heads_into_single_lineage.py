"""Merge all heads into single lineage

Revision ID: 20260227_merge_heads
Revises: 20241001120000, 20260208_conformal, 20260209_layer_license, 20260209_tbg_rename, 20260210_fix_site_fk, 20260210_pegging, 20260210_powell_training, 20260224_participant_to_su, 20260227_exec_brief
Create Date: 2026-02-27 06:05:26.027827

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260227_merge_heads'
down_revision: Union[str, None] = ('20241001120000', '20260208_conformal', '20260209_layer_license', '20260209_tbg_rename', '20260210_fix_site_fk', '20260210_pegging', '20260210_powell_training', '20260224_participant_to_su', '20260227_exec_brief')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
