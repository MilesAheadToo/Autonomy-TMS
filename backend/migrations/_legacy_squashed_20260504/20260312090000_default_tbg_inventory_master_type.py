"""Set Default TBG manufacturer master type to inventory.

Revision ID: 20260312090000
Revises: 20260120100000_align_node_type_with_dag
Create Date: 2026-03-12 09:00:00.000000
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260312090000"
down_revision = "20260120100000_align_node_type_with_dag"
branch_labels = None
depends_on = None


def _ensure_manufacturer_master(defs: Any, master_type: str) -> Any:
    """Return definitions with the manufacturer master_type normalized."""

    try:
        working: List[Dict[str, Any]]
        if isinstance(defs, str):
            working = json.loads(defs)
        else:
            working = json.loads(json.dumps(defs))
    except Exception:
        return defs

    target = master_type.strip().lower() or "inventory"
    for entry in working:
        if str(entry.get("type", "")).strip().lower() == "manufacturer":
            entry["master_type"] = target
            break
    else:
        working.append(
            {
                "type": "manufacturer",
                "label": "Manufacturer",
                "order": len(working),
                "is_required": False,
                "master_type": target,
            }
        )
    return working


def upgrade() -> None:
    bind = op.get_bind()
    if bind is None:
        return

    config_ids = [
        row.id
        for row in bind.execute(
            sa.text(
                "SELECT id FROM supply_chain_configs WHERE LOWER(name) = 'default tbg'"
            )
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
            updated_defs = _ensure_manufacturer_master(result[0], "inventory")
            payload = updated_defs if isinstance(updated_defs, str) else json.dumps(updated_defs)
            bind.execute(
                sa.text(
                    "UPDATE supply_chain_configs SET node_type_definitions = :defs WHERE id = :id"
                ),
                {"defs": payload, "id": config_id},
            )

        bind.execute(
            sa.text(
                """
                UPDATE nodes
                SET master_type = 'inventory'
                WHERE config_id = :config_id
                  AND LOWER(COALESCE(dag_type, type, name)) IN ('manufacturer', 'case_mfg')
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
            sa.text(
                "SELECT id FROM supply_chain_configs WHERE LOWER(name) = 'default tbg'"
            )
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
            updated_defs = _ensure_manufacturer_master(result[0], "manufacturer")
            payload = updated_defs if isinstance(updated_defs, str) else json.dumps(updated_defs)
            bind.execute(
                sa.text(
                    "UPDATE supply_chain_configs SET node_type_definitions = :defs WHERE id = :id"
                ),
                {"defs": payload, "id": config_id},
            )

        bind.execute(
            sa.text(
                """
                UPDATE nodes
                SET master_type = 'manufacturer'
                WHERE config_id = :config_id
                  AND LOWER(COALESCE(dag_type, type, name)) IN ('manufacturer', 'case_mfg')
                """
            ),
            {"config_id": config_id},
        )
