"""merge_all_heads

Revision ID: a7630db18e62
Revises: 20260313_aiio, 20260313_rccp, 20260313_shipment_config, 20260314_geocode_cache, 20260314_rccp_validation, 20260314_sap_job_toggles, 20260315_override, 20260318_econ, 20260318_dispid, 20260318_ext_ids, 20260318_sap_staging, 20260318_tenant_config_cleanup, 20260319_tp, ext_signal_001, 20260322_site_plan_cfg, 20260323_ek, 20260323_rl_step, 20260324_aiio, 20260324_scenario_engine, 20260325_demo_date_shift
Create Date: 2026-03-25 13:17:46.012958

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7630db18e62'
down_revision: Union[str, None] = ('20260313_aiio', '20260313_rccp', '20260313_shipment_config', '20260314_geocode_cache', '20260314_rccp_validation', '20260314_sap_job_toggles', '20260315_override', '20260318_econ', '20260318_dispid', '20260318_ext_ids', '20260318_sap_staging', '20260318_tenant_config_cleanup', '20260319_tp', 'ext_signal_001', '20260322_site_plan_cfg', '20260323_ek', '20260323_rl_step', '20260324_aiio', '20260324_scenario_engine', '20260325_demo_date_shift')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
