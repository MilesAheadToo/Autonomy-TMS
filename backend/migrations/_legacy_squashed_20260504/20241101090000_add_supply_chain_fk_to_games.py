"""Add supply chain foreign key to games table.

Revision ID: 20241101090000
Revises: 20241020120000
Create Date: 2024-11-01 09:00:00.000000
"""

from __future__ import annotations

import json

from typing import Any, Dict, Optional

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision = "20241101090000"
down_revision = "20241020120000"
branch_labels = None
depends_on = None


def _inspector() -> Inspector:
    return inspect(op.get_bind())


def _table_exists(name: str) -> bool:
    insp = _inspector()
    return name in insp.get_table_names()


def _column_exists(table: str, column: str) -> bool:
    insp = _inspector()
    if table not in insp.get_table_names():
        return False
    return column in {col["name"] for col in insp.get_columns(table)}


def _index_exists(table: str, index: str) -> bool:
    insp = _inspector()
    try:
        return index in {idx["name"] for idx in insp.get_indexes(table)}
    except sa.exc.NoSuchTableError:
        return False


def _fk_exists(table: str, fk_name: str) -> bool:
    insp = _inspector()
    try:
        return any(fk.get("name") == fk_name for fk in insp.get_foreign_keys(table))
    except sa.exc.NoSuchTableError:
        return False


def upgrade() -> None:
    if not (_table_exists("games") and _table_exists("supply_chain_configs")):
        return

    bind = op.get_bind()

    if not _column_exists("games", "supply_chain_config_id"):
        op.add_column(
            "games",
            sa.Column("supply_chain_config_id", sa.Integer(), nullable=True),
        )

    if not _index_exists("games", "ix_games_supply_chain_config_id"):
        op.create_index(
            "ix_games_supply_chain_config_id",
            "games",
            ["supply_chain_config_id"],
        )

    if not _fk_exists("games", "fk_games_supply_chain_config_id"):
        op.create_foreign_key(
            "fk_games_supply_chain_config_id",
            "games",
            "supply_chain_configs",
            ["supply_chain_config_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    games = bind.execute(
        text("SELECT id, config, supply_chain_config_id FROM games")
    ).fetchall()

    for row in games:
        if row["supply_chain_config_id"] is not None:
            continue
        config_payload = row["config"]
        config_dict: Optional[Dict[str, Any]] = None
        if isinstance(config_payload, dict):
            config_dict = config_payload
        elif isinstance(config_payload, str) and config_payload.strip():
            try:
                config_dict = json.loads(config_payload)
            except json.JSONDecodeError:
                config_dict = None

        if not config_dict:
            continue

        config_id = config_dict.get("supply_chain_config_id")
        if config_id is None:
            continue

        try:
            config_id_int = int(config_id)
        except (TypeError, ValueError):
            continue

        bind.execute(
            text(
                "UPDATE games SET supply_chain_config_id = :cfg WHERE id = :gid"
            ),
            {"cfg": config_id_int, "gid": row["id"]},
        )

    remaining = bind.execute(
        text("SELECT COUNT(*) FROM games WHERE supply_chain_config_id IS NULL")
    ).scalar_one()

    if remaining == 0:
        op.alter_column(
            "games",
            "supply_chain_config_id",
            existing_type=sa.Integer(),
            nullable=False,
        )


def downgrade() -> None:
    if _fk_exists("games", "fk_games_supply_chain_config_id"):
        op.drop_constraint("fk_games_supply_chain_config_id", "games", type_="foreignkey")

    if _index_exists("games", "ix_games_supply_chain_config_id"):
        op.drop_index("ix_games_supply_chain_config_id", table_name="games")

    if _column_exists("games", "supply_chain_config_id"):
        op.drop_column("games", "supply_chain_config_id")
