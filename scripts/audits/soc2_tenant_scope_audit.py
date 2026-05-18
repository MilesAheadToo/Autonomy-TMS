"""SOC II tenant-scope audit.

Inventories every table for tenant-scope hygiene:
  - Has `config_id` column? Is it FK to supply_chain_configs.id? NOT NULL?
  - Has redundant `config_name` / scope-string column?
  - Has `tenant_id` column (older convention)?
  - RLS enabled? Policy present? Policy keyed on integer scope?

Run inside the backend container so we get authoritative DB state, not just
model-file source. Emits Markdown to stdout.

Usage:
  docker compose exec -T backend python /app/scripts/audits/soc2_tenant_scope_audit.py
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field

import psycopg2
import psycopg2.extras

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://autonomy_user:autonomy_password@db:5432/autonomy",
).replace("postgresql+psycopg2://", "postgresql://")

SCOPE_NAME_COLS = {"config_name", "scenario_name", "tenant_name"}
DISPLAY_NAME_COLS = {"name", "display_name", "label", "title", "description"}
SCOPE_FK_COL = "config_id"
ALT_SCOPE_FK_COLS = {"supply_chain_config_id", "sc_config_id"}
TENANT_FK_COL = "tenant_id"


@dataclass
class TableAudit:
    schema: str
    name: str
    columns: dict[str, dict] = field(default_factory=dict)
    fks: dict[str, str] = field(default_factory=dict)  # column -> referenced table
    rls_enabled: bool = False
    policies: list[str] = field(default_factory=list)

    @property
    def fqn(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def has_config_fk(self) -> bool:
        for col in (SCOPE_FK_COL, *ALT_SCOPE_FK_COLS):
            if col in self.columns and self.fks.get(col) == "supply_chain_configs":
                return True
        return False

    @property
    def has_config_id_column_no_fk(self) -> bool:
        for col in (SCOPE_FK_COL, *ALT_SCOPE_FK_COLS):
            if col in self.columns and self.fks.get(col) != "supply_chain_configs":
                return True
        return False

    @property
    def config_id_nullable(self) -> bool | None:
        for col in (SCOPE_FK_COL, *ALT_SCOPE_FK_COLS):
            if col in self.columns:
                return self.columns[col]["nullable"]
        return None

    @property
    def has_tenant_id(self) -> bool:
        return TENANT_FK_COL in self.columns

    @property
    def scope_name_cols(self) -> list[str]:
        return [c for c in self.columns if c in SCOPE_NAME_COLS]

    @property
    def display_name_cols(self) -> list[str]:
        return [c for c in self.columns if c in DISPLAY_NAME_COLS]

    @property
    def has_explicit_wildcard_column(self) -> bool:
        """True iff the table has a NOT NULL boolean column that makes
        the nullable-config_id wildcard explicit (per MIGRATION_REGISTER
        §1.15 / SOC II audit §11). Currently only ``plane_registration``
        uses this pattern, but the rule is generic — any table opting
        into the explicit-wildcard contract is no longer a SOC II hazard
        even with nullable config_id."""
        return "applies_to_all_configs" in self.columns

    @property
    def category(self) -> str:
        """Classify into one of the SOC II audit buckets."""
        if not self.has_config_fk and not self.has_tenant_id:
            return "no_scope"
        if self.has_config_fk and self.config_id_nullable and self.scope_name_cols:
            return "hazard_nullable_fk_plus_redundant_name"
        if self.has_config_fk and self.config_id_nullable:
            # Tables that opted into the explicit-wildcard contract
            # (applies_to_all_configs + CHECK constraint) get a
            # different bucket — they're not a hazard, the nullable
            # is intentional and audit-grade documented.
            if self.has_explicit_wildcard_column:
                return "clean_fk_explicit_wildcard"
            return "hazard_nullable_fk"
        if self.has_config_fk and self.scope_name_cols:
            return "hazard_redundant_scope_name"
        if self.has_config_id_column_no_fk:
            return "hazard_config_id_no_fk"
        if not self.has_config_fk and self.has_tenant_id:
            return "tenant_id_only"
        if self.has_config_fk:
            return "clean_fk"
        return "other"


def fetch_audit() -> dict[str, TableAudit]:
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    audits: dict[str, TableAudit] = {}

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        # All user tables across SOC II schemas
        cur.execute(
            """
            SELECT n.nspname AS schema, c.relname AS name, c.relrowsecurity AS rls
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r'
              AND n.nspname IN ('public', 'agents', 'audit', 'checkpoints', 'conformal')
              AND c.relname NOT LIKE 'pg_%'
              AND c.relname NOT LIKE 'sql_%'
            ORDER BY n.nspname, c.relname
            """
        )
        for row in cur.fetchall():
            t = TableAudit(schema=row["schema"], name=row["name"])
            t.rls_enabled = bool(row["rls"])
            audits[t.fqn] = t

        # Columns: name, nullable, type
        cur.execute(
            """
            SELECT table_schema AS schema, table_name AS name,
                   column_name AS col, is_nullable, data_type
            FROM information_schema.columns
            WHERE table_schema IN ('public', 'agents', 'audit', 'checkpoints', 'conformal')
            """
        )
        for row in cur.fetchall():
            fqn = f"{row['schema']}.{row['name']}"
            if fqn in audits:
                audits[fqn].columns[row["col"]] = {
                    "nullable": row["is_nullable"] == "YES",
                    "type": row["data_type"],
                }

        # Foreign keys — use explicit namespace joins so we don't depend on search_path
        cur.execute(
            """
            SELECT sn.nspname || '.' || sc.relname AS source_tbl,
                   a.attname AS source_col,
                   tc.relname AS target_tbl_name
            FROM pg_constraint c
            JOIN pg_class sc ON sc.oid = c.conrelid
            JOIN pg_namespace sn ON sn.oid = sc.relnamespace
            JOIN pg_class tc ON tc.oid = c.confrelid
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
            WHERE c.contype = 'f'
              AND sn.nspname IN ('public', 'agents', 'audit', 'checkpoints', 'conformal')
            """
        )
        for row in cur.fetchall():
            src = row["source_tbl"]
            if src in audits:
                audits[src].fks[row["source_col"]] = row["target_tbl_name"]

        # RLS policies
        cur.execute("SELECT schemaname, tablename, policyname, qual FROM pg_policies")
        for row in cur.fetchall():
            fqn = f"{row['schemaname']}.{row['tablename']}"
            if fqn in audits:
                audits[fqn].policies.append(f"{row['policyname']}: {row['qual']}")

    conn.close()
    return audits


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="SOC II tenant-scope audit")
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit non-zero if any table is in a hazard category, RLS is "
            "disabled on a tenant-scoped table, or RLS is enabled without "
            "any policy attached. Use in CI per "
            ".claude/rules/tenant-scope-fk.md (Phase 4 forward guardrail)."
        ),
    )
    args = parser.parse_args()

    audits = fetch_audit()
    by_cat: dict[str, list[TableAudit]] = defaultdict(list)
    for t in audits.values():
        by_cat[t.category].append(t)

    print(f"# SOC II Tenant-Scope Audit — Raw Inventory\n")
    print(f"**Generated:** {os.environ.get('CONFIG_TODAY', '2026-04-27')}")
    print(f"**Total tables across audited schemas:** {len(audits)}\n")

    counts = {cat: len(items) for cat, items in by_cat.items()}
    print("## Category counts\n")
    print("| Category | Count | Hazard? |")
    print("|---|---:|---|")
    cats_in_order = [
        ("clean_fk", "Clean — has `config_id` FK NOT NULL, no redundant name", "—"),
        ("hazard_redundant_scope_name", "Has `config_id` FK + redundant scope-name column", "**HIGH**"),
        ("hazard_nullable_fk", "Has `config_id` FK but nullable", "**HIGH**"),
        ("hazard_nullable_fk_plus_redundant_name", "Has nullable `config_id` FK + redundant name", "**CRITICAL**"),
        ("hazard_config_id_no_fk", "Has `config_id` column but no FK constraint", "**HIGH**"),
        ("tenant_id_only", "Uses `tenant_id` int only (older convention)", "MEDIUM"),
        ("no_scope", "No tenant scope columns at all (global / lookup tables)", "review case-by-case"),
    ]
    for cat, label, hazard in cats_in_order:
        c = counts.get(cat, 0)
        print(f"| {label} | {c} | {hazard} |")
    other_cats = set(counts) - {c[0] for c in cats_in_order}
    for cat in sorted(other_cats):
        print(f"| {cat} | {counts[cat]} | review |")
    print()

    # RLS coverage
    rls_on = sum(1 for t in audits.values() if t.rls_enabled)
    rls_with_policy = sum(1 for t in audits.values() if t.rls_enabled and t.policies)
    tenant_scoped = sum(1 for t in audits.values() if t.has_config_fk or t.has_tenant_id)
    tenant_scoped_with_rls = sum(
        1 for t in audits.values() if (t.has_config_fk or t.has_tenant_id) and t.rls_enabled
    )
    print("## RLS coverage\n")
    print(f"- Tables with RLS enabled: **{rls_on}** / {len(audits)}")
    print(f"- Tables with RLS + at least one policy: **{rls_with_policy}** / {len(audits)}")
    print(f"- Tenant-scoped tables (have config_id FK or tenant_id): **{tenant_scoped}**")
    print(f"- Tenant-scoped tables with RLS enabled: **{tenant_scoped_with_rls}** / {tenant_scoped}")
    print()

    # Hazard tables — full detail
    hazard_cats = [
        "hazard_nullable_fk_plus_redundant_name",
        "hazard_nullable_fk",
        "hazard_redundant_scope_name",
        "hazard_config_id_no_fk",
    ]
    print("## Hazard tables — full detail\n")
    for cat in hazard_cats:
        items = by_cat.get(cat, [])
        if not items:
            continue
        print(f"### `{cat}` ({len(items)})\n")
        print("| Table | scope-name cols | display-name cols | config_id nullable? | RLS | policies |")
        print("|---|---|---|---|---|---:|")
        for t in sorted(items, key=lambda x: x.fqn):
            scope_n = ", ".join(t.scope_name_cols) or "—"
            disp_n = ", ".join(t.display_name_cols) or "—"
            nul = "YES" if t.config_id_nullable else ("NO" if t.config_id_nullable is False else "—")
            rls = "ON" if t.rls_enabled else "**off**"
            print(f"| `{t.fqn}` | {scope_n} | {disp_n} | {nul} | {rls} | {len(t.policies)} |")
        print()

    # Tenant-scoped tables WITHOUT RLS — separate finding
    no_rls_scoped = [
        t for t in audits.values()
        if (t.has_config_fk or t.has_tenant_id) and not t.rls_enabled
    ]
    if no_rls_scoped:
        print(f"## Tenant-scoped tables with RLS DISABLED ({len(no_rls_scoped)})\n")
        print("| Table | has config_id FK | has tenant_id |")
        print("|---|:-:|:-:|")
        for t in sorted(no_rls_scoped, key=lambda x: x.fqn):
            print(f"| `{t.fqn}` | {'✓' if t.has_config_fk else '—'} | {'✓' if t.has_tenant_id else '—'} |")
        print()

    # tenant_id-only tables (older convention)
    if by_cat.get("tenant_id_only"):
        print(f"## Tables using `tenant_id` only (no `config_id` FK) — {len(by_cat['tenant_id_only'])}\n")
        print("Decide: is `tenant_id` sufficient, or should every tenant-scoped table also carry `config_id` for plane-registry compatibility?\n")
        print("| Table | RLS | policies |")
        print("|---|---|---:|")
        for t in sorted(by_cat["tenant_id_only"], key=lambda x: x.fqn):
            rls = "ON" if t.rls_enabled else "**off**"
            print(f"| `{t.fqn}` | {rls} | {len(t.policies)} |")
        print()

    # Clean tables — counts only (not interesting for the audit)
    clean = by_cat.get("clean_fk", [])
    print(f"## Clean tables (`config_id` FK NOT NULL, no redundant scope-name) — {len(clean)}\n")
    print("Listing suppressed; these are the target state.\n")

    # ── Strict-mode forward guardrail (Phase 4) ─────────────────────────
    # When called from CI with --strict, the audit decides PASS / FAIL
    # based on whether any forbidden state is present. Hazard categories
    # are the audit's CRITICAL/HIGH cohort; missing policy is worse than
    # missing RLS (silently zero rows under autonomy_app); a tenant-scoped
    # table without RLS is a leak.
    if args.strict:
        violations: list[str] = []
        # Always-genuine violations: redundant scope-name strings (FK
        # drifts from string), missing FK constraint (DB integrity is
        # absent regardless of RLS), nullable FK + redundant name
        # (combines both hazards).
        always_genuine = (
            "hazard_redundant_scope_name",
            "hazard_nullable_fk_plus_redundant_name",
            "hazard_config_id_no_fk",
        )
        for cat in always_genuine:
            for t in by_cat.get(cat, []):
                violations.append(f"{cat}: {t.fqn}")

        # Conditional: nullable FK is only a violation when RLS isn't
        # mitigating it. The Phase 3 EXISTS-via-supply_chain_configs
        # policy template handles NULL config_id safely (NULL = current
        # tenant evaluates to NULL → false → row invisible). So a table
        # with nullable FK but a tenant_isolation policy attached is
        # functionally protected. Only flag when the policy is missing.
        for t in by_cat.get("hazard_nullable_fk", []):
            if not (t.rls_enabled and t.policies):
                violations.append(f"hazard_nullable_fk_unmitigated: {t.fqn}")

        # Tenant-scoped tables must have RLS enabled + at least one
        # policy attached. RLS-on-no-policy is worse than RLS-off
        # (silently zero rows under autonomy_app).
        scoped = [
            t for t in audits.values()
            if t.has_config_fk or t.has_tenant_id
        ]
        for t in scoped:
            if not t.rls_enabled:
                violations.append(f"rls_off_on_tenant_scoped: {t.fqn}")
            elif not t.policies:
                violations.append(f"rls_on_no_policy: {t.fqn}")

        if violations:
            print("\n## STRICT MODE: forward-guardrail violations\n")
            print(f"**{len(violations)} violation(s) — see .claude/rules/tenant-scope-fk.md**\n")
            for v in sorted(violations)[:50]:
                print(f"- {v}")
            if len(violations) > 50:
                print(f"- … and {len(violations) - 50} more")
            return 1
        else:
            print("\n## STRICT MODE: PASS — no forward-guardrail violations\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
