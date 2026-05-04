"""Rename Default TBG manufacturer DAG node to Factory with inventory master type.

Revision ID: 20260322093000
Revises: 20260315090000
Create Date: 2026-03-22 09:30:00.000000
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260322093000"
down_revision = "20260315090000"
branch_labels = None
depends_on = None


def _load_definitions(defs: Any) -> Tuple[Any, bool]:
    """Normalize node type definitions to include a Factory entry and canonical ordering."""

    try:
        working: List[Dict[str, Any]]
        if isinstance(defs, str):
            working = json.loads(defs)
        else:
            working = json.loads(json.dumps(defs))
    except Exception:
        return defs, False

    updated = False
    normalized: Dict[str, Dict[str, Any]] = {}
    for entry in working:
        entry_type = str(entry.get("type") or "").strip().lower()
        canonical_type = "factory" if entry_type in {"manufacturer", "case_mfg", "factory"} else entry_type
        normalized_entry = dict(entry)
        normalized_entry["type"] = canonical_type
        if canonical_type == "factory":
            normalized_entry["label"] = "Factory"
            normalized_entry["master_type"] = "inventory"
        existing = normalized.get(canonical_type)
        if existing is None:
            normalized[canonical_type] = normalized_entry
        else:
            updated = True
            normalized[canonical_type].update(normalized_entry)

    desired_order = [
        ("market_demand", "Market Demand", "market_demand", True),
        ("retailer", "Retailer", "inventory", False),
        ("wholesaler", "Wholesaler", "inventory", False),
        ("distributor", "Distributor", "inventory", False),
        ("factory", "Factory", "inventory", False),
        ("market_supply", "Market Supply", "market_supply", True),
    ]

    result: List[Dict[str, Any]] = []
    for idx, (type_key, label, master, required) in enumerate(desired_order):
        entry = normalized.get(type_key, {})
        merged = {
            "type": type_key,
            "label": entry.get("label", label),
            "order": idx,
            "is_required": entry.get("is_required", required),
            "master_type": entry.get("master_type", master),
        }
        if entry != merged:
            updated = True
        result.append(merged)

    extra_entries = [v for k, v in normalized.items() if k not in {key for key, *_ in desired_order}]
    if extra_entries:
        updated = True
        for entry in extra_entries:
            result.append(entry)

    return result, updated


def _store_definitions(bind, config_id: int, defs: Any) -> None:
    payload = defs if isinstance(defs, str) else json.dumps(defs)
    bind.execute(
        sa.text(
            "UPDATE supply_chain_configs SET node_type_definitions = :defs WHERE id = :id"
        ),
        {"defs": payload, "id": config_id},
    )


def _canonical_node_key(row: Dict[str, Any]) -> str:
    return str(row.get("dag_type") or row.get("type") or row.get("name") or "").strip().lower()


def _promote_factory_node(bind, config_id: int) -> None:
    nodes = (
        bind.execute(
            sa.text(
                """
                SELECT id, name, type, dag_type, master_type
                FROM nodes
                WHERE config_id = :config_id
                """
            ),
            {"config_id": config_id},
        )
        .mappings()
        .all()
    )

    factory_like = [row for row in nodes if _canonical_node_key(row) in {"manufacturer", "case_mfg", "factory"}]
    if factory_like:
        factory_like.sort(key=lambda row: row["id"] or 0)
        keep_id = factory_like[0]["id"]
        bind.execute(
            sa.text(
                """
                UPDATE nodes
                   SET name = 'Factory',
                       type = 'factory',
                       dag_type = 'factory',
                       master_type = 'inventory'
                 WHERE id = :id
                """
            ),
            {"id": keep_id},
        )

        for duplicate in factory_like[1:]:
            dup_id = duplicate["id"]
            bind.execute(
                sa.text(
                    "UPDATE lanes SET upstream_node_id = :keep WHERE upstream_node_id = :dup"
                ),
                {"keep": keep_id, "dup": dup_id},
            )
            bind.execute(
                sa.text(
                    "UPDATE lanes SET downstream_node_id = :keep WHERE downstream_node_id = :dup"
                ),
                {"keep": keep_id, "dup": dup_id},
            )
            bind.execute(
                sa.text(
                    "UPDATE item_node_configs SET node_id = :keep WHERE node_id = :dup"
                ),
                {"keep": keep_id, "dup": dup_id},
            )
            bind.execute(sa.text("DELETE FROM nodes WHERE id = :dup"), {"dup": dup_id})
        return

    bind.execute(
        sa.text(
            """
            INSERT INTO nodes (config_id, name, type, dag_type, master_type)
            VALUES (:config_id, 'Factory', 'factory', 'factory', 'inventory')
            """
        ),
        {"config_id": config_id},
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind is None:
        return

    config_ids = [
        row.id
        for row in bind.execute(
            sa.text("SELECT id FROM supply_chain_configs WHERE LOWER(name) = 'default tbg'")
        )
    ]
    if not config_ids:
        return

    for config_id in config_ids:
        result = bind.execute(
            sa.text(
                "SELECT node_type_definitions FROM supply_chain_configs WHERE id = :id"
            ),
            {"id": config_id},
        ).fetchone()
        if result:
            updated_defs, changed = _load_definitions(result[0])
            if changed:
                _store_definitions(bind, config_id, updated_defs)

        _promote_factory_node(bind, config_id)


def _revert_definitions(defs: Any) -> Tuple[Any, bool]:
    try:
        working: List[Dict[str, Any]]
        if isinstance(defs, str):
            working = json.loads(defs)
        else:
            working = json.loads(json.dumps(defs))
    except Exception:
        return defs, False

    updated = False
    normalized: Dict[str, Dict[str, Any]] = {}
    for entry in working:
        entry_type = str(entry.get("type") or "").strip().lower()
        canonical_type = "manufacturer" if entry_type in {"factory", "manufacturer", "case_mfg"} else entry_type
        normalized_entry = dict(entry)
        normalized_entry["type"] = canonical_type
        if canonical_type == "manufacturer":
            normalized_entry["label"] = "Manufacturer"
            normalized_entry["master_type"] = "manufacturer"
        existing = normalized.get(canonical_type)
        if existing is None:
            normalized[canonical_type] = normalized_entry
        else:
            updated = True
            normalized[canonical_type].update(normalized_entry)

    desired_order = [
        ("market_demand", "Market Demand", "market_demand", True),
        ("retailer", "Retailer", "inventory", False),
        ("wholesaler", "Wholesaler", "inventory", False),
        ("distributor", "Distributor", "inventory", False),
        ("manufacturer", "Manufacturer", "manufacturer", False),
        ("market_supply", "Market Supply", "market_supply", True),
    ]

    result: List[Dict[str, Any]] = []
    for idx, (type_key, label, master, required) in enumerate(desired_order):
        entry = normalized.get(type_key, {})
        merged = {
            "type": type_key,
            "label": entry.get("label", label),
            "order": idx,
            "is_required": entry.get("is_required", required),
            "master_type": entry.get("master_type", master),
        }
        if entry != merged:
            updated = True
        result.append(merged)

    extra_entries = [v for k, v in normalized.items() if k not in {key for key, *_ in desired_order}]
    if extra_entries:
        updated = True
        for entry in extra_entries:
            result.append(entry)

    return result, updated


def _demote_factory_node(bind, config_id: int) -> None:
    nodes = (
        bind.execute(
            sa.text(
                """
                SELECT id, name, type, dag_type, master_type
                FROM nodes
                WHERE config_id = :config_id
                """
            ),
            {"config_id": config_id},
        )
        .mappings()
        .all()
    )

    manufacturer_like = [row for row in nodes if _canonical_node_key(row) in {"factory", "manufacturer", "case_mfg"}]
    if manufacturer_like:
        manufacturer_like.sort(key=lambda row: row["id"] or 0)
        keep_id = manufacturer_like[0]["id"]
        bind.execute(
            sa.text(
                """
                UPDATE nodes
                   SET name = 'Manufacturer',
                       type = 'manufacturer',
                       dag_type = 'manufacturer',
                       master_type = 'manufacturer'
                 WHERE id = :id
                """
            ),
            {"id": keep_id},
        )

        for duplicate in manufacturer_like[1:]:
            dup_id = duplicate["id"]
            bind.execute(
                sa.text(
                    "UPDATE lanes SET upstream_node_id = :keep WHERE upstream_node_id = :dup"
                ),
                {"keep": keep_id, "dup": dup_id},
            )
            bind.execute(
                sa.text(
                    "UPDATE lanes SET downstream_node_id = :keep WHERE downstream_node_id = :dup"
                ),
                {"keep": keep_id, "dup": dup_id},
            )
            bind.execute(
                sa.text(
                    "UPDATE item_node_configs SET node_id = :keep WHERE node_id = :dup"
                ),
                {"keep": keep_id, "dup": dup_id},
            )
            bind.execute(sa.text("DELETE FROM nodes WHERE id = :dup"), {"dup": dup_id})
        return

    bind.execute(
        sa.text(
            """
            INSERT INTO nodes (config_id, name, type, dag_type, master_type)
            VALUES (:config_id, 'Manufacturer', 'manufacturer', 'manufacturer', 'manufacturer')
            """
        ),
        {"config_id": config_id},
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind is None:
        return

    config_ids = [
        row.id
        for row in bind.execute(
            sa.text("SELECT id FROM supply_chain_configs WHERE LOWER(name) = 'default tbg'")
        )
    ]
    if not config_ids:
        return

    for config_id in config_ids:
        result = bind.execute(
            sa.text(
                "SELECT node_type_definitions FROM supply_chain_configs WHERE id = :id"
            ),
            {"id": config_id},
        ).fetchone()
        if result:
            updated_defs, changed = _revert_definitions(result[0])
            if changed:
                _store_definitions(bind, config_id, updated_defs)

        _demote_factory_node(bind, config_id)
