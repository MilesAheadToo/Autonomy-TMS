"""Add stochastic_config JSON column to supply_chain_configs.

Per-config pipeline tuning parameters for SAP extraction thresholds
and distribution fitting (min_observations, min_rows_sufficiency, etc.).

Revision ID: 20260314_stochastic_config
Revises: 20260314_agent_stochastic_params
Create Date: 2026-03-14
"""
from alembic import op
from sqlalchemy import text

# revision identifiers
revision = "20260314_stochastic_config"
down_revision = "20260314_agent_stochastic_params"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text(
        "ALTER TABLE supply_chain_configs "
        "ADD COLUMN IF NOT EXISTS stochastic_config JSONB"
    ))
    conn.execute(text(
        "COMMENT ON COLUMN supply_chain_configs.stochastic_config IS "
        "'Stochastic pipeline tuning: min_observations, min_rows_sufficiency, "
        "cv_lognormal_threshold, min_group_count'"
    ))


def downgrade():
    conn = op.get_bind()
    conn.execute(text(
        "ALTER TABLE supply_chain_configs "
        "DROP COLUMN IF EXISTS stochastic_config"
    ))
