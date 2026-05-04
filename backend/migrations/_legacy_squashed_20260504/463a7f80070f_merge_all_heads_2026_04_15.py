"""merge all heads 2026-04-15

Revision ID: 463a7f80070f
Revises: 20260330_extract_audit, 20260402_material_scope, 20260403_capacity_tgnn, 20260403_site_ckpt, 20260405_config_slug, 20260405_conformal_fk, 20260405_virtual_clock, 20260415_route_cache
Create Date: 2026-04-15 08:25:19.025174

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '463a7f80070f'
down_revision: Union[str, None] = ('20260330_extract_audit', '20260402_material_scope', '20260403_capacity_tgnn', '20260403_site_ckpt', '20260405_config_slug', '20260405_conformal_fk', '20260405_virtual_clock', '20260415_route_cache')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
