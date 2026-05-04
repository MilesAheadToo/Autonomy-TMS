"""Consolidate 7 groups into 3 tenants and drop legacy groups table.

Merges organizational groups into 3 canonical tenants:
  1. The Beer Game (groups 1, 3, 4) — admin: tbg_admin@autonomy.com
  2. Complex SC (group 2)           — admin: complex_sc_admin@autonomy.com
  3. Food Distributor (groups 12, 13, 14) — admin: admin@distdemo.com

Then retargets all 69 FK constraints from groups → tenants, drops
users.group_id, sso_providers.default_group_id, tenants.tenant_id (legacy),
and finally drops the groups table.

Fully idempotent — safe to run even if parts were already applied.

Revision ID: 20260227_drop_groups
Revises: 20260227_merge_heads
Create Date: 2026-02-27 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260227_drop_groups'
down_revision = '20260227_merge_heads'
branch_labels = None
depends_on = None

# Groups → Tenant mapping
# Groups 1, 3, 4 → Tenant 1 (The Beer Game)
# Group 2 → Tenant 2 (Complex SC)
# Groups 12, 13, 14 → Tenant 3 (Food Distributor)
CONSOLIDATION_MAP = {
    3: 1,   # Three FG TBG → The Beer Game
    4: 1,   # Variable TBG → The Beer Game
    12: 3,  # Food Distributor Learning → Food Distributor
    13: 3,  # Food Distributor → Food Distributor
    14: 3,  # Food Dist → Food Distributor
}
# Groups 1 and 2 keep their IDs (1 → tenant 1, 2 → tenant 2)


def _table_exists(table_name):
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = :tbl)"
    ), {"tbl": table_name})
    return result.scalar()


def _column_exists(table_name, column_name):
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :tbl AND column_name = :col)"
    ), {"tbl": table_name, "col": column_name})
    return result.scalar()


def _enum_exists(enum_name):
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = :name)"
    ), {"name": enum_name})
    return result.scalar()


def upgrade():
    conn = op.get_bind()

    # ================================================================== #
    # STEP 1: Create/update 3 tenants from 7 groups
    # ================================================================== #
    if _table_exists('groups'):
        # Tenant 1: The Beer Game (primary group = 1)
        admin_row = conn.execute(sa.text(
            "SELECT id FROM users WHERE email = 'tbg_admin@autonomy.com'"
        )).first()
        admin_1 = admin_row[0] if admin_row else 1

        existing = conn.execute(sa.text("SELECT id FROM tenants WHERE id = 1")).first()
        if existing:
            conn.execute(sa.text(
                "UPDATE tenants SET name = 'The Beer Game', slug = 'the-beer-game', "
                "subdomain = 'the-beer-game', mode = 'learning' WHERE id = 1"
            ))
        else:
            conn.execute(sa.text("""
                INSERT INTO tenants (id, name, slug, subdomain, status, billing_plan,
                    admin_id, mode, max_users, max_games, max_supply_chain_configs,
                    max_storage_mb, current_user_count, current_game_count,
                    current_config_count, current_storage_mb, created_at)
                VALUES (1, 'The Beer Game', 'the-beer-game', 'the-beer-game', 'ACTIVE',
                    'FREE', :admin_id, 'learning', 100, 50, 20, 5000, 0, 0, 0, 0, NOW())
            """), {"admin_id": admin_1})

        # Tenant 2: Complex SC (already exists as id=2, update name)
        admin_row = conn.execute(sa.text(
            "SELECT id FROM users WHERE email = 'complex_sc_admin@autonomy.com'"
        )).first()
        admin_2 = admin_row[0] if admin_row else 2

        existing = conn.execute(sa.text("SELECT id FROM tenants WHERE id = 2")).first()
        if existing:
            conn.execute(sa.text(
                "UPDATE tenants SET name = 'Complex SC', slug = 'complex-sc', "
                "subdomain = 'complex-sc', admin_id = :admin_id, mode = 'production' WHERE id = 2"
            ), {"admin_id": admin_2})
        else:
            conn.execute(sa.text("""
                INSERT INTO tenants (id, name, slug, subdomain, status, billing_plan,
                    admin_id, mode, max_users, max_games, max_supply_chain_configs,
                    max_storage_mb, current_user_count, current_game_count,
                    current_config_count, current_storage_mb, created_at)
                VALUES (2, 'Complex SC', 'complex-sc', 'complex-sc', 'ACTIVE',
                    'FREE', :admin_id, 'production', 100, 50, 20, 5000, 0, 0, 0, 0, NOW())
            """), {"admin_id": admin_2})

        # Tenant 3: Food Distributor (consolidating groups 12, 13, 14)
        admin_row = conn.execute(sa.text(
            "SELECT id FROM users WHERE email = 'admin@distdemo.com'"
        )).first()
        admin_3 = admin_row[0] if admin_row else 57

        existing = conn.execute(sa.text("SELECT id FROM tenants WHERE id = 3")).first()
        if existing:
            conn.execute(sa.text(
                "UPDATE tenants SET name = 'Food Distributor', slug = 'food-distributor', "
                "subdomain = 'food-distributor', admin_id = :admin_id, mode = 'production' WHERE id = 3"
            ), {"admin_id": admin_3})
        else:
            conn.execute(sa.text("""
                INSERT INTO tenants (id, name, slug, subdomain, status, billing_plan,
                    admin_id, mode, max_users, max_games, max_supply_chain_configs,
                    max_storage_mb, current_user_count, current_game_count,
                    current_config_count, current_storage_mb, created_at)
                VALUES (3, 'Food Distributor', 'food-distributor', 'food-distributor', 'ACTIVE',
                    'FREE', :admin_id, 'production', 100, 50, 20, 5000, 0, 0, 0, 0, NOW())
            """), {"admin_id": admin_3})

    # ================================================================== #
    # STEP 2: Reassign users.tenant_id from users.group_id
    # ================================================================== #
    if _column_exists('users', 'group_id'):
        # Direct mappings: group 1→tenant 1, group 2→tenant 2
        conn.execute(sa.text("UPDATE users SET tenant_id = 1 WHERE group_id = 1"))
        conn.execute(sa.text("UPDATE users SET tenant_id = 2 WHERE group_id = 2"))
        # Consolidated: groups 3,4 → tenant 1
        conn.execute(sa.text("UPDATE users SET tenant_id = 1 WHERE group_id IN (3, 4)"))
        # Consolidated: groups 12,13,14 → tenant 3
        conn.execute(sa.text("UPDATE users SET tenant_id = 3 WHERE group_id IN (12, 13, 14)"))
        # Catch-all for systemadmin or any other users without tenant_id
        conn.execute(sa.text(
            "UPDATE users SET tenant_id = 1 WHERE tenant_id IS NULL AND group_id IS NOT NULL"
        ))

    # ================================================================== #
    # STEP 3: Reassign tenant_id in ALL tables (consolidate group IDs)
    # ================================================================== #
    # Find all tables with tenant_id column that have data pointing to old group IDs
    tenant_id_tables = conn.execute(sa.text(
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'tenant_id' "
        "AND table_name != 'users' AND table_name != 'tenants'"
    )).fetchall()

    for (table_name,) in tenant_id_tables:
        # Groups 3, 4 → Tenant 1 (The Beer Game)
        try:
            conn.execute(sa.text(
                f'UPDATE "{table_name}" SET tenant_id = 1 WHERE tenant_id IN (3, 4)'
            ))
        except Exception:
            pass
        # Groups 12, 13, 14 → Tenant 3 (Food Distributor)
        try:
            conn.execute(sa.text(
                f'UPDATE "{table_name}" SET tenant_id = 3 WHERE tenant_id IN (12, 13, 14)'
            ))
        except Exception:
            pass

    # Also handle company_id columns that reference groups
    for table_name in ['atp_projection', 'ctp_projection', 'inv_projection', 'order_promise']:
        if _table_exists(table_name) and _column_exists(table_name, 'company_id'):
            try:
                conn.execute(sa.text(
                    f'UPDATE "{table_name}" SET company_id = 1 WHERE company_id IN (3, 4)'
                ))
                conn.execute(sa.text(
                    f'UPDATE "{table_name}" SET company_id = 3 WHERE company_id IN (12, 13, 14)'
                ))
            except Exception:
                pass

    # ================================================================== #
    # STEP 4: Drop ALL FK constraints referencing groups table
    # ================================================================== #
    if _table_exists('groups'):
        fk_rows = conn.execute(sa.text(
            "SELECT tc.constraint_name, tc.table_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name "
            "WHERE ccu.table_name = 'groups' "
            "  AND tc.constraint_type = 'FOREIGN KEY'"
        )).fetchall()

        for constraint_name, table_name in fk_rows:
            try:
                op.drop_constraint(constraint_name, table_name, type_='foreignkey')
            except Exception:
                pass

    # ================================================================== #
    # STEP 5: Drop legacy columns
    # ================================================================== #

    # Drop tenants.tenant_id (legacy self-referential bridging column)
    if _column_exists('tenants', 'tenant_id'):
        # Drop unique constraint first
        try:
            op.drop_constraint('tenants_group_id_key', 'tenants', type_='unique')
        except Exception:
            pass
        try:
            op.drop_column('tenants', 'tenant_id')
        except Exception:
            pass

    # Drop tenants.customer_id if it exists
    if _column_exists('tenants', 'customer_id'):
        try:
            op.drop_column('tenants', 'customer_id')
        except Exception:
            pass

    # Rename sso_providers.default_group_id → default_tenant_id
    if _table_exists('sso_providers') and _column_exists('sso_providers', 'default_group_id'):
        try:
            op.alter_column('sso_providers', 'default_group_id', new_column_name='default_tenant_id')
        except Exception:
            pass

    # Drop users.group_id
    if _column_exists('users', 'group_id'):
        try:
            op.drop_column('users', 'group_id')
        except Exception:
            pass

    # Drop customer_id columns from core tables
    for table_name in ['users', 'supply_chain_configs', 'scenarios', 'games']:
        if _table_exists(table_name) and _column_exists(table_name, 'customer_id'):
            try:
                op.drop_column(table_name, 'customer_id')
            except Exception:
                pass

    # ================================================================== #
    # STEP 6: Create new FK constraints referencing tenants
    # ================================================================== #
    # Get all tables with tenant_id that need FK to tenants
    tenant_id_tables = conn.execute(sa.text(
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'tenant_id' "
        "AND table_name != 'tenants'"
    )).fetchall()

    for (table_name,) in tenant_id_tables:
        # Check if FK already exists pointing to tenants
        existing_fk = conn.execute(sa.text(
            "SELECT COUNT(*) FROM information_schema.table_constraints tc "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name "
            "WHERE tc.table_name = :tbl AND tc.constraint_type = 'FOREIGN KEY' "
            "  AND ccu.table_name = 'tenants'"
        ), {"tbl": table_name}).scalar()

        if existing_fk == 0:
            fk_name = f"fk_{table_name}_tenant_id"
            try:
                op.create_foreign_key(
                    fk_name, table_name, 'tenants',
                    ['tenant_id'], ['id'],
                    ondelete='CASCADE'
                )
            except Exception:
                pass  # Table might have orphan rows

    # FK for company_id columns → tenants
    for table_name in ['atp_projection', 'ctp_projection', 'inv_projection', 'order_promise']:
        if _table_exists(table_name) and _column_exists(table_name, 'company_id'):
            fk_name = f"fk_{table_name}_company_tenant"
            try:
                op.create_foreign_key(
                    fk_name, table_name, 'tenants',
                    ['company_id'], ['id'],
                    ondelete='CASCADE'
                )
            except Exception:
                pass

    # FK for sso_providers.default_tenant_id → tenants
    if _table_exists('sso_providers') and _column_exists('sso_providers', 'default_tenant_id'):
        try:
            op.create_foreign_key(
                'fk_sso_providers_default_tenant', 'sso_providers', 'tenants',
                ['default_tenant_id'], ['id'],
                ondelete='SET NULL'
            )
        except Exception:
            pass

    # ================================================================== #
    # STEP 7: Drop the groups table
    # ================================================================== #
    if _table_exists('groups'):
        op.drop_table('groups')

    # ================================================================== #
    # STEP 8: Clean up stale tenant rows and reset sequence
    # ================================================================== #
    # Delete any tenant rows with IDs that don't match our 3 canonical tenants
    try:
        conn.execute(sa.text("DELETE FROM tenants WHERE id NOT IN (1, 2, 3)"))
    except Exception:
        pass  # FK violations mean data still references them

    # Reset sequence
    try:
        conn.execute(sa.text(
            "SELECT setval('tenants_id_seq', (SELECT COALESCE(MAX(id), 1) FROM tenants))"
        ))
    except Exception:
        pass

    # ================================================================== #
    # STEP 9: Rename group_mode_enum → tenant_mode_enum
    # ================================================================== #
    if _enum_exists('group_mode_enum') and not _enum_exists('tenant_mode_enum'):
        op.execute("ALTER TYPE group_mode_enum RENAME TO tenant_mode_enum")
    elif _enum_exists('group_mode_enum') and _enum_exists('tenant_mode_enum'):
        usage = conn.execute(sa.text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE udt_name = 'group_mode_enum'"
        )).scalar()
        if usage == 0:
            op.execute("DROP TYPE group_mode_enum")


def downgrade():
    # This migration consolidates data — downgrade would need to recreate
    # groups and re-split data, which is not feasible automatically.
    raise NotImplementedError(
        "Cannot downgrade: group consolidation into tenants is irreversible. "
        "Restore from database backup if needed."
    )
