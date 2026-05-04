"""merge_before_planning_trm_tables

Revision ID: 93f865a0dab9
Revises: 20260312_tactical_tgnn, 20260324090000, 20260314_stochastic_config, 20260312_lgbm_forecast
Create Date: 2026-03-15 07:19:18.893316

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '93f865a0dab9'
down_revision: Union[str, None] = ('20260312_tactical_tgnn', '20260324090000', '20260314_stochastic_config', '20260312_lgbm_forecast')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
