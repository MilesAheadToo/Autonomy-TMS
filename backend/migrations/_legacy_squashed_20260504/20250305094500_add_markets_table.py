"""Introduce markets and migrate market demand relationships.

Revision ID: 20250305094500
Revises: 20250302093000
Create Date: 2025-03-05 09:45:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column
from sqlalchemy import Integer, String, Text
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "20250305094500"
down_revision = "20250302093000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "markets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("company", sa.String(length=100), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["config_id"], ["supply_chain_configs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("config_id", "name", name="uq_market_name_per_config"),
    )

    with op.batch_alter_table("market_demands", schema=None) as batch_op:
        batch_op.add_column(sa.Column("market_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_market_demands_market_id",
            "markets",
            ["market_id"],
            ["id"],
            ondelete="CASCADE",
        )

    connection = op.get_bind()
    market_table = table(
        "markets",
        column("id", Integer),
        column("config_id", Integer),
        column("name", String),
        column("description", Text),
    )

    config_rows = connection.execute(sa.text("SELECT DISTINCT config_id FROM market_demands")).fetchall()
    for (config_id,) in config_rows:
        if config_id is None:
            continue
        connection.execute(
            market_table.insert().values(
                config_id=config_id,
                name="Default Market",
                description="Migrated market",
            )
        )
        market_id = connection.execute(
            sa.text(
                "SELECT id FROM markets WHERE config_id = :config_id ORDER BY id DESC LIMIT 1"
            ),
            {"config_id": config_id},
        ).scalar()
        if market_id is not None:
            connection.execute(
                sa.text(
                    "UPDATE market_demands SET market_id = :market_id WHERE config_id = :config_id"
                ),
                {"market_id": market_id, "config_id": config_id},
            )

    insp = inspect(bind)
    existing_fks = {fk["name"] for fk in insp.get_foreign_keys("market_demands")}
    existing_ucs = {uc["name"] for uc in insp.get_unique_constraints("market_demands")}

    with op.batch_alter_table("market_demands", schema=None) as batch_op:
        if "market_demands_ibfk_3" in existing_fks:
            batch_op.drop_constraint("market_demands_ibfk_3", type_="foreignkey")
        if "market_demands_ibfk_2" in existing_fks:
            batch_op.drop_constraint("market_demands_ibfk_2", type_="foreignkey")
        if "market_demands_ibfk_1" in existing_fks:
            batch_op.drop_constraint("market_demands_ibfk_1", type_="foreignkey")
        if "_market_demand_uc" in existing_ucs:
            batch_op.drop_constraint("_market_demand_uc", type_="unique")

        batch_op.drop_column("retailer_id")
        batch_op.alter_column("market_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_unique_constraint(
            "uq_market_demand_item_market",
            ["item_id", "market_id", "config_id"],
        )
        batch_op.create_foreign_key(
            "market_demands_ibfk_1",
            "supply_chain_configs",
            ["config_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "market_demands_ibfk_2",
            "items",
            ["item_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("market_demands", schema=None) as batch_op:
        batch_op.drop_constraint("uq_market_demand_item_market", type_="unique")
        batch_op.add_column(sa.Column("retailer_id", sa.Integer(), nullable=True))
        batch_op.alter_column("market_id", existing_type=sa.Integer(), nullable=True)
        batch_op.drop_constraint("fk_market_demands_market_id", type_="foreignkey")
        batch_op.drop_constraint("market_demands_ibfk_2", type_="foreignkey")
        batch_op.drop_constraint("market_demands_ibfk_1", type_="foreignkey")
        batch_op.create_unique_constraint(
            "_market_demand_uc",
            ["item_id", "retailer_id", "config_id"],
        )
        batch_op.create_foreign_key(
            "market_demands_ibfk_1",
            "supply_chain_configs",
            ["config_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "market_demands_ibfk_2",
            "items",
            ["item_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "market_demands_ibfk_3",
            "nodes",
            ["retailer_id"],
            ["id"],
            ondelete="CASCADE",
        )

    op.drop_table("markets")
