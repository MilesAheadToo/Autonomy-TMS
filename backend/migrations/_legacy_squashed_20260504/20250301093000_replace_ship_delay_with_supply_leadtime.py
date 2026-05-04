"""replace ship_delay keys with supply_leadtime in config blobs"""

from __future__ import annotations

import json
from typing import Any, Tuple

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20250301093000"
down_revision = "20250226091000"
branch_labels = None
depends_on = None


SHIP_DELAY_KEYS = {"ship_delay", "shipDelay", "shipping_delay"}
TARGET_TABLES = (
    ("games", "config"),
    ("rounds", "config"),
    ("agent_configs", "config"),
)


def _normalize_config(value: Any) -> Tuple[Any, bool]:
    changed = False

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        # If both ship_delay and supply_leadtime are present we prefer supply_leadtime.
        has_supply = "supply_leadtime" in value
        for key, raw_child in value.items():
            child, child_changed = _normalize_config(raw_child)
            changed = changed or child_changed

            if key in SHIP_DELAY_KEYS:
                changed = True
                if has_supply:
                    # Drop legacy alias entirely if canonical key already exists.
                    continue
                key = "supply_leadtime"
                has_supply = True

            result[key] = child
        return result, changed

    if isinstance(value, list):
        normalized_items = []
        for item in value:
            new_item, item_changed = _normalize_config(item)
            changed = changed or item_changed
            normalized_items.append(new_item)
        return normalized_items, changed

    return value, changed


def _load_json(data: Any) -> Tuple[Any, bool]:
    if data is None:
        return None, False

    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")

    if isinstance(data, str):
        data = data.strip()
        if not data:
            return None, False
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return data, False
        return parsed, True

    return data, False


def upgrade() -> None:
    conn = op.get_bind()

    for table, column in TARGET_TABLES:
        rows = conn.execute(
            sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")
        ).fetchall()
        for row in rows:
            record_id = row[0]
            raw_config = row[1]
            config_obj, _ = _load_json(raw_config)

            if config_obj is None:
                continue

            normalized, changed = _normalize_config(config_obj)
            if not changed:
                # No modifications were required.
                continue

            payload = normalized
            if isinstance(raw_config, str):
                payload = json.dumps(normalized)

            conn.execute(
                sa.text(f"UPDATE {table} SET {column} = :payload WHERE id = :id"),
                {"payload": payload, "id": record_id},
            )


def downgrade() -> None:
    conn = op.get_bind()

    for table, column in TARGET_TABLES:
        rows = conn.execute(
            sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")
        ).fetchall()
        for row in rows:
            record_id = row[0]
            raw_config = row[1]
            config_obj, _ = _load_json(raw_config)

            if not config_obj:
                continue

            reverted, changed = _reintroduce_ship_delay(config_obj)
            if not changed:
                continue

            payload = reverted
            if isinstance(raw_config, str):
                payload = json.dumps(reverted)

            conn.execute(
                sa.text(f"UPDATE {table} SET {column} = :payload WHERE id = :id"),
                {"payload": payload, "id": record_id},
            )


def _reintroduce_ship_delay(value: Any) -> Tuple[Any, bool]:
    changed = False

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, raw_child in value.items():
            child, child_changed = _reintroduce_ship_delay(raw_child)
            changed = changed or child_changed

            if key == "supply_leadtime":
                changed = True
                result[key] = child
                result["ship_delay"] = child
                continue

            result[key] = child
        return result, changed

    if isinstance(value, list):
        reverted_items = []
        for item in value:
            new_item, item_changed = _reintroduce_ship_delay(item)
            changed = changed or item_changed
            reverted_items.append(new_item)
        return reverted_items, changed

    return value, changed
