"""Add tenant_id to powell_belief_state, drop config_id.

Syncs the DB schema with the ORM model (PowellBeliefState.tenant_id).
Existing rows (all config_id=22 → tenant_id=3) are migrated via JOIN.

Revision ID: 20260304_belief_state_tenant_id
Revises: 20260304_forecast_pipeline_drift
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "20260304_belief_state_tenant_id"
down_revision = "20260304_forecast_pipeline_drift"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add tenant_id as nullable first (will populate from config_id join)
    op.add_column(
        "powell_belief_state",
        sa.Column("tenant_id", sa.Integer(), nullable=True),
    )

    # 2. Populate tenant_id from supply_chain_configs.tenant_id via config_id
    op.execute("""
        UPDATE powell_belief_state pbs
        SET tenant_id = sc.tenant_id
        FROM supply_chain_configs sc
        WHERE pbs.config_id = sc.id
          AND pbs.config_id IS NOT NULL
    """)

    # 3. For any remaining NULLs (no config_id), set to first available tenant
    op.execute("""
        UPDATE powell_belief_state
        SET tenant_id = (SELECT id FROM tenants ORDER BY id LIMIT 1)
        WHERE tenant_id IS NULL
    """)

    # 4. Add FK constraint to tenants
    op.create_foreign_key(
        "powell_belief_state_tenant_id_fkey",
        "powell_belief_state",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 5. Add index for tenant + entity_type queries
    op.create_index(
        "idx_belief_tenant",
        "powell_belief_state",
        ["tenant_id", "entity_type"],
    )

    # 6. Drop old config_id FK and index, then drop column
    op.drop_constraint(
        "powell_belief_state_config_id_fkey",
        "powell_belief_state",
        type_="foreignkey",
    )
    op.drop_index("idx_belief_config", table_name="powell_belief_state")
    op.drop_column("powell_belief_state", "config_id")


def downgrade() -> None:
    # Re-add config_id (data loss — tenant_id not reversible without mapping)
    op.add_column(
        "powell_belief_state",
        sa.Column("config_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "powell_belief_state_config_id_fkey",
        "powell_belief_state",
        "supply_chain_configs",
        ["config_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("idx_belief_config", "powell_belief_state", ["config_id"])
    op.drop_index("idx_belief_tenant", table_name="powell_belief_state")
    op.drop_constraint(
        "powell_belief_state_tenant_id_fkey",
        "powell_belief_state",
        type_="foreignkey",
    )
    op.drop_column("powell_belief_state", "tenant_id")
