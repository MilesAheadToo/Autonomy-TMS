"""Config-level mode: add mode to supply_chain_configs, default_config_id to users, merge companion tenants

Revision ID: 20260311_config_mode
Revises: (merges all current heads)
Create Date: 2026-03-11

Changes:
  1. supply_chain_configs.mode  (String(20), NOT NULL, default 'production')
  2. users.default_config_id    (Integer FK → supply_chain_configs.id, nullable)
  3. Backfill mode from tenant.mode
  4. Migrate companion tenant configs (16→2, 17→3, 21→20) by moving configs + users
  5. Set default_config_id for all users
  6. Delete companion tenants 16, 17, 21
"""

from alembic import op
import sqlalchemy as sa

# Merge all current heads into this single revision
revision = '20260311_config_mode'
down_revision = (
    '20260309_sap_conn',
    '20260310_hana_db',
    '20260311_directives_provisioning',
    '20260311_email_signals',
    '20260311_site_tp_refactor',
)
branch_labels = None
depends_on = None


def upgrade():
    # -------------------------------------------------------------------------
    # 1. Add mode column to supply_chain_configs
    # -------------------------------------------------------------------------
    op.add_column(
        'supply_chain_configs',
        sa.Column('mode', sa.String(20), nullable=False, server_default='production')
    )

    # -------------------------------------------------------------------------
    # 2. Add default_config_id column to users
    # -------------------------------------------------------------------------
    op.add_column(
        'users',
        sa.Column('default_config_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_users_default_config',
        'users',
        'supply_chain_configs',
        ['default_config_id'],
        ['id'],
        ondelete='SET NULL',
    )

    # -------------------------------------------------------------------------
    # 3. Backfill supply_chain_configs.mode from tenant mode
    # -------------------------------------------------------------------------
    op.execute("""
        UPDATE supply_chain_configs sc
        SET mode = t.mode
        FROM tenants t
        WHERE sc.tenant_id = t.id
    """)

    # -------------------------------------------------------------------------
    # 4a. Migrate companion tenant configs: move configs to their parent tenants.
    #     The companion configs are learning configs.  Their parent tenants already
    #     have a production BASELINE that is is_active=TRUE.  We must deactivate the
    #     companion configs first to avoid violating uq_tenant_active_baseline
    #     (UNIQUE WHERE is_active=TRUE AND scenario_type='BASELINE').
    # -------------------------------------------------------------------------
    op.execute("UPDATE supply_chain_configs SET is_active = false WHERE id IN (32, 33, 38)")
    op.execute("UPDATE supply_chain_configs SET tenant_id = 2  WHERE id = 32")
    op.execute("UPDATE supply_chain_configs SET tenant_id = 3  WHERE id = 33")
    op.execute("UPDATE supply_chain_configs SET tenant_id = 20 WHERE id = 38")

    # -------------------------------------------------------------------------
    # 4b. Migrate companion tenant users: move users to their parent tenants
    # -------------------------------------------------------------------------
    op.execute("UPDATE users SET tenant_id = 2  WHERE id = 86")
    op.execute("UPDATE users SET tenant_id = 3  WHERE id = 87")
    op.execute("UPDATE users SET tenant_id = 20 WHERE id = 90")

    # -------------------------------------------------------------------------
    # 5a. Set default_config_id for migrated users (their learning configs)
    # -------------------------------------------------------------------------
    op.execute("UPDATE users SET default_config_id = 32 WHERE id = 86")
    op.execute("UPDATE users SET default_config_id = 33 WHERE id = 87")
    op.execute("UPDATE users SET default_config_id = 38 WHERE id = 90")

    # -------------------------------------------------------------------------
    # 5b. Set default_config_id for all other users without one yet
    #     Use each tenant's is_active=True BASELINE config
    # -------------------------------------------------------------------------
    op.execute("""
        UPDATE users u
        SET default_config_id = (
            SELECT sc.id
            FROM supply_chain_configs sc
            WHERE sc.tenant_id = u.tenant_id
              AND sc.is_active = true
              AND sc.scenario_type = 'BASELINE'
            ORDER BY sc.id
            LIMIT 1
        )
        WHERE u.default_config_id IS NULL
          AND u.tenant_id IS NOT NULL
    """)

    # -------------------------------------------------------------------------
    # 6. Delete companion tenants (now empty — users migrated above)
    # -------------------------------------------------------------------------
    op.execute("DELETE FROM tenants WHERE id IN (16, 17, 21)")


def downgrade():
    # Reverse order: restore FK, drop columns (data loss on tenant records — acceptable)
    op.drop_constraint('fk_users_default_config', 'users', type_='foreignkey')
    op.drop_column('users', 'default_config_id')
    op.drop_column('supply_chain_configs', 'mode')
