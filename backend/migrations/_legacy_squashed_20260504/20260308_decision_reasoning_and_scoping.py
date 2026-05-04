"""Add decision_reasoning + missing reason columns + hierarchy FK population

Revision ID: 20260308_reasoning_scope
Revises: 20260307_powell_exec
Create Date: 2026-03-08

Two changes:
1. Add `decision_reasoning` (Text) to all 11 powell_*_decisions tables,
   plus powell_allocations (tGNN) and powell_policy_parameters (GraphSAGE).
2. Add `reason` (String 100) to 4 tables that lack it: atp, order_exceptions,
   mo_decisions, maintenance_decisions.
3. Populate site_hierarchy_node.site_id FKs and fix product_hierarchy_node.product_id.
4. Fix user scope values to reference actual hierarchy codes.
"""

revision = "20260308_reasoning_scope"
down_revision = "20260307_powell_exec"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


# All 11 powell_*_decisions tables that inherit HiveSignalMixin
DECISION_TABLES = [
    "powell_atp_decisions",
    "powell_rebalance_decisions",
    "powell_po_decisions",
    "powell_order_exceptions",
    "powell_mo_decisions",
    "powell_to_decisions",
    "powell_quality_decisions",
    "powell_maintenance_decisions",
    "powell_subcontracting_decisions",
    "powell_forecast_adjustment_decisions",
    "powell_buffer_decisions",
]

# Tables that need a `reason` column added
TABLES_NEEDING_REASON = [
    "powell_atp_decisions",
    "powell_order_exceptions",
    "powell_mo_decisions",
    "powell_maintenance_decisions",
]


def upgrade():
    conn = op.get_bind()

    # ── 1. Add decision_reasoning to all 11 decision tables ──────────
    for table in DECISION_TABLES:
        # Check if column exists first (idempotent)
        result = conn.execute(sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = 'decision_reasoning'"
        ), {"t": table})
        if result.fetchone() is None:
            op.add_column(table, sa.Column("decision_reasoning", sa.Text(), nullable=True))

    # Also add to powell_allocations (tGNN reasoning) and powell_policy_parameters (GraphSAGE reasoning)
    for table in ["powell_allocations", "powell_policy_parameters"]:
        result = conn.execute(sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = 'decision_reasoning'"
        ), {"t": table})
        if result.fetchone() is None:
            op.add_column(table, sa.Column("decision_reasoning", sa.Text(), nullable=True))

    # ── 2. Add reason column to 4 tables that lack it ────────────────
    for table in TABLES_NEEDING_REASON:
        result = conn.execute(sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = 'reason'"
        ), {"t": table})
        if result.fetchone() is None:
            op.add_column(table, sa.Column("reason", sa.String(100), nullable=True))

    # ── 3. Populate site_hierarchy_node.site_id FKs ──────────────────
    # Match SITE-level hierarchy nodes to site table records.
    # Hierarchy code format: DF_SITE_USFOODS_DC → site.name = FOODDIST_DC
    # We match by: strip 'DF_SITE_' prefix from code, then find matching site
    # using the last segment of hierarchy_path (which IS the site identifier).
    #
    # hierarchy_path examples:
    #   DF_COMPANY/DF_CENTRAL/DF_US/USFOODS_DC → last segment = USFOODS_DC
    # site.name examples: FOODDIST_DC, CUST_PDX, etc.
    #
    # Since naming conventions differ, use a mapping approach:
    # Match hierarchy nodes to sites in same tenant's config by checking
    # if the hierarchy path's last segment appears as a substring in site.name
    # or vice versa. For now, do a direct name-based mapping via config tenant.

    # First, get all SITE-level hierarchy nodes with null site_id
    result = conn.execute(sa.text("""
        SELECT shn.id, shn.code, shn.hierarchy_path, shn.tenant_id, shn.name
        FROM site_hierarchy_node shn
        WHERE shn.hierarchy_level = 'site' AND shn.site_id IS NULL
    """))
    site_nodes = result.fetchall()

    for node_id, code, path, tenant_id, node_name in site_nodes:
        # Try to match by site name within the same tenant's configs
        # Strategy: find sites from configs owned by this tenant
        match_result = conn.execute(sa.text("""
            SELECT s.id, s.name FROM site s
            JOIN supply_chain_configs c ON s.config_id = c.id
            WHERE c.tenant_id = :tid AND s.name = :sname
            LIMIT 1
        """), {"tid": tenant_id, "sname": node_name})
        row = match_result.fetchone()

        if not row:
            # Try matching the last segment of hierarchy_path
            path_segment = path.rsplit("/", 1)[-1] if "/" in path else path
            match_result = conn.execute(sa.text("""
                SELECT s.id, s.name FROM site s
                JOIN supply_chain_configs c ON s.config_id = c.id
                WHERE c.tenant_id = :tid AND s.name = :sname
                LIMIT 1
            """), {"tid": tenant_id, "sname": path_segment})
            row = match_result.fetchone()

        if row:
            conn.execute(sa.text(
                "UPDATE site_hierarchy_node SET site_id = :sid WHERE id = :nid"
            ), {"sid": row[0], "nid": node_id})

    # ── 4. Fix product_hierarchy_node.product_id to include config prefix ──
    # Current: product_id = 'FP001' but product.id = 'CFG22_FP001'
    # Fix: update to match actual product IDs
    result = conn.execute(sa.text("""
        SELECT phn.id, phn.product_id, phn.tenant_id
        FROM product_hierarchy_node phn
        WHERE phn.hierarchy_level = 'product' AND phn.product_id IS NOT NULL
    """))
    product_nodes = result.fetchall()

    for node_id, current_pid, tenant_id in product_nodes:
        # Check if current product_id actually exists in product table
        exists = conn.execute(sa.text(
            "SELECT 1 FROM product WHERE id = :pid LIMIT 1"
        ), {"pid": current_pid})
        if exists.fetchone():
            continue  # Already correct

        # Try finding with config prefix (CFG{config_id}_{pid})
        match_result = conn.execute(sa.text("""
            SELECT p.id FROM product p
            JOIN supply_chain_configs c ON p.config_id = c.id
            WHERE c.tenant_id = :tid AND p.id LIKE :pattern
            LIMIT 1
        """), {"tid": tenant_id, "pattern": f"%_{current_pid}"})
        row = match_result.fetchone()
        if row:
            conn.execute(sa.text(
                "UPDATE product_hierarchy_node SET product_id = :pid WHERE id = :nid"
            ), {"pid": row[0], "nid": node_id})

    # ── 5. Fix user scope values to use actual hierarchy codes ────────
    # Current: site_scope=['REGION_Central', 'SITE_DC-Chicago']
    # Needed: site_scope=['DF_CENTRAL', 'DF_SITE_USFOODS_DC']
    # Current: product_scope=['CATEGORY_Frozen', 'CATEGORY_Refrigerated']
    # Needed: product_scope=['DF_CAT_FROZEN', 'DF_CAT_REFRIGERATED']
    #
    # Build mapping from old-style keys to actual hierarchy codes

    # Site scope mapping
    site_scope_map = {}
    result = conn.execute(sa.text("""
        SELECT code, hierarchy_level, name FROM site_hierarchy_node
    """))
    for code, level, name in result.fetchall():
        # Map LEVEL_Name -> code (e.g., REGION_Central -> DF_CENTRAL if name matches)
        old_key = f"{level.upper()}_{name}"
        site_scope_map[old_key] = code

    # Product scope mapping
    product_scope_map = {}
    result = conn.execute(sa.text("""
        SELECT code, hierarchy_level, name FROM product_hierarchy_node
    """))
    for code, level, name in result.fetchall():
        old_key = f"{level.upper()}_{name}"
        product_scope_map[old_key] = code

    # Update users with site_scope
    result = conn.execute(sa.text(
        "SELECT id, site_scope, product_scope FROM users WHERE site_scope IS NOT NULL OR product_scope IS NOT NULL"
    ))
    users = result.fetchall()

    for user_id, site_scope, product_scope in users:
        updated = False

        if site_scope:
            new_scope = []
            for key in site_scope:
                mapped = site_scope_map.get(key)
                if mapped:
                    new_scope.append(mapped)
                else:
                    new_scope.append(key)  # Keep unmapped keys as-is
            if new_scope != site_scope:
                conn.execute(sa.text(
                    "UPDATE users SET site_scope = :scope WHERE id = :uid"
                ), {"scope": sa.type_coerce(new_scope, sa.JSON), "uid": user_id})
                updated = True

        if product_scope:
            new_scope = []
            for key in product_scope:
                mapped = product_scope_map.get(key)
                if mapped:
                    new_scope.append(mapped)
                else:
                    new_scope.append(key)
            if new_scope != product_scope:
                conn.execute(sa.text(
                    "UPDATE users SET product_scope = :scope WHERE id = :uid"
                ), {"scope": sa.type_coerce(new_scope, sa.JSON), "uid": user_id})


def downgrade():
    # Remove decision_reasoning from all tables
    for table in DECISION_TABLES + ["powell_allocations", "powell_policy_parameters"]:
        try:
            op.drop_column(table, "decision_reasoning")
        except Exception:
            pass

    # Remove reason from 4 tables
    for table in TABLES_NEEDING_REASON:
        try:
            op.drop_column(table, "reason")
        except Exception:
            pass
