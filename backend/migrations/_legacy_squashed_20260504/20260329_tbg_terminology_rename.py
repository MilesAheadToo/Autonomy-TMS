"""Rename TBG terminology to AWS SC DM standard

- scenarios.current_round → scenarios.current_period
- scenarios.max_rounds → scenarios.max_periods
- site.dag_type: 'market_demand' → 'customer', 'market_supply' → 'vendor'
- supply_chain_configs.site_type_definitions JSON: update type keys

Revision ID: 20260329_tbg_rename
Revises: a7630db18e62
Create Date: 2026-03-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '20260329_tbg_rename'
down_revision: Union[str, None] = 'a7630db18e62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0. Rename PostgreSQL enum type and values (gamestatus → scenariostatus)
    op.execute("ALTER TYPE gamestatus RENAME VALUE 'ROUND_IN_PROGRESS' TO 'PERIOD_IN_PROGRESS'")
    op.execute("ALTER TYPE gamestatus RENAME VALUE 'ROUND_COMPLETED' TO 'PERIOD_COMPLETED'")
    op.execute("ALTER TYPE gamestatus RENAME TO scenariostatus")

    # 1. Rename scenario columns
    op.alter_column('scenarios', 'current_round', new_column_name='current_period')
    op.alter_column('scenarios', 'max_rounds', new_column_name='max_periods')

    # 2. Update dag_type values in site table
    op.execute("UPDATE site SET dag_type = 'customer' WHERE dag_type = 'market_demand'")
    op.execute("UPDATE site SET dag_type = 'vendor' WHERE dag_type = 'market_supply'")

    # 3. Update master_type values (lowercase leftovers)
    op.execute("UPDATE site SET master_type = 'CUSTOMER' WHERE lower(master_type) = 'market_demand'")
    op.execute("UPDATE site SET master_type = 'VENDOR' WHERE lower(master_type) = 'market_supply'")

    # 4. Update site_type_definitions JSON in supply_chain_configs
    op.execute("""
        UPDATE supply_chain_configs
        SET site_type_definitions = REPLACE(
            REPLACE(
                REPLACE(
                    REPLACE(site_type_definitions::text,
                        '"market_demand"', '"customer"'),
                    '"market_supply"', '"vendor"'),
                '"Market Demand"', '"Customer"'),
            '"Market Supply"', '"Vendor"'
        )::jsonb
        WHERE site_type_definitions IS NOT NULL
        AND (site_type_definitions::text LIKE '%market_demand%'
             OR site_type_definitions::text LIKE '%market_supply%'
             OR site_type_definitions::text LIKE '%Market Demand%'
             OR site_type_definitions::text LIKE '%Market Supply%')
    """)

    # 5. Rename transfer_order and purchase_order columns that reference rounds
    op.alter_column('transfer_order', 'order_round', new_column_name='order_period')
    op.alter_column('transfer_order', 'arrival_round', new_column_name='arrival_period')
    op.alter_column('purchase_order', 'order_round', new_column_name='order_period')
    op.alter_column('purchase_order', 'arrival_round', new_column_name='arrival_period')


def downgrade() -> None:
    # Reverse enum rename
    op.execute("ALTER TYPE scenariostatus RENAME TO gamestatus")
    op.execute("ALTER TYPE gamestatus RENAME VALUE 'PERIOD_IN_PROGRESS' TO 'ROUND_IN_PROGRESS'")
    op.execute("ALTER TYPE gamestatus RENAME VALUE 'PERIOD_COMPLETED' TO 'ROUND_COMPLETED'")

    # Reverse column renames
    op.alter_column('scenarios', 'current_period', new_column_name='current_round')
    op.alter_column('scenarios', 'max_periods', new_column_name='max_rounds')
    op.alter_column('transfer_order', 'order_period', new_column_name='order_round')
    op.alter_column('transfer_order', 'arrival_period', new_column_name='arrival_round')
    op.alter_column('purchase_order', 'order_period', new_column_name='order_round')
    op.alter_column('purchase_order', 'arrival_period', new_column_name='arrival_round')

    # Reverse dag_type values
    op.execute("UPDATE site SET dag_type = 'market_demand' WHERE dag_type = 'customer'")
    op.execute("UPDATE site SET dag_type = 'market_supply' WHERE dag_type = 'vendor'")
