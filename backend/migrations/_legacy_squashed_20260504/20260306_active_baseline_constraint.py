"""active_baseline_constraint

Revision ID: 20260306_active_baseline_constraint
Revises: a1ded8e0aa88
Create Date: 2026-03-06

Enforce exactly one active BASELINE config per tenant:
1. Data fix: set is_active=False on all WORKING/SIMULATION configs
2. Data fix: ensure each tenant has exactly one active BASELINE
3. Add unique partial index: uq_tenant_active_baseline
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers
revision: str = "20260306_active_baseline_constraint"
down_revision: Union[str, None] = "a1ded8e0aa88"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Step 1: Deactivate all WORKING/SIMULATION configs (branches should not be "active")
    conn.execute(text("""
        UPDATE supply_chain_configs
        SET is_active = FALSE
        WHERE scenario_type IN ('WORKING', 'SIMULATION')
          AND is_active = TRUE
    """))

    # Step 2: For each tenant, ensure exactly one active BASELINE config.
    # First, find tenants that have NO active BASELINE but have configs.
    tenants_without_active = conn.execute(text("""
        SELECT DISTINCT tenant_id
        FROM supply_chain_configs
        WHERE tenant_id IS NOT NULL
          AND tenant_id NOT IN (
              SELECT tenant_id FROM supply_chain_configs
              WHERE is_active = TRUE AND scenario_type = 'BASELINE'
                AND tenant_id IS NOT NULL
          )
    """)).fetchall()

    for (tenant_id,) in tenants_without_active:
        # Find the best candidate: root BASELINE (no parent), else first config
        candidate = conn.execute(text("""
            SELECT id FROM supply_chain_configs
            WHERE tenant_id = :tid
              AND scenario_type = 'BASELINE'
              AND parent_config_id IS NULL
            ORDER BY id
            LIMIT 1
        """), {"tid": tenant_id}).fetchone()

        if not candidate:
            # No BASELINE at all — promote the first config
            candidate = conn.execute(text("""
                SELECT id FROM supply_chain_configs
                WHERE tenant_id = :tid
                ORDER BY id
                LIMIT 1
            """), {"tid": tenant_id}).fetchone()

        if candidate:
            config_id = candidate[0]
            # Deactivate all others for this tenant
            conn.execute(text("""
                UPDATE supply_chain_configs
                SET is_active = FALSE
                WHERE tenant_id = :tid AND id != :cid
            """), {"tid": tenant_id, "cid": config_id})
            # Activate the candidate as BASELINE
            conn.execute(text("""
                UPDATE supply_chain_configs
                SET is_active = TRUE, scenario_type = 'BASELINE'
                WHERE id = :cid
            """), {"cid": config_id})

    # Step 3: Handle tenants with multiple active BASELINEs — keep only the first
    conn.execute(text("""
        UPDATE supply_chain_configs
        SET is_active = FALSE
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (PARTITION BY tenant_id ORDER BY id) as rn
                FROM supply_chain_configs
                WHERE is_active = TRUE AND scenario_type = 'BASELINE'
                  AND tenant_id IS NOT NULL
            ) ranked
            WHERE rn > 1
        )
    """))

    # Step 4: Create the unique partial index
    op.execute("""
        CREATE UNIQUE INDEX uq_tenant_active_baseline
        ON supply_chain_configs (tenant_id)
        WHERE is_active = TRUE AND scenario_type = 'BASELINE'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_tenant_active_baseline")
