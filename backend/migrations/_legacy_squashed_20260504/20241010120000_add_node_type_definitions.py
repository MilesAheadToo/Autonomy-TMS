"""Add node_type_definitions column to supply_chain_configs."""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20241010120000"
down_revision = "20241001120000"
branch_labels = None
depends_on = None

DEFAULT_DEFINITIONS = [
    {"type": "market_supply", "label": "Market Supply", "order": 0, "is_required": True},
    {"type": "manufacturer", "label": "Manufacturer", "order": 1, "is_required": False},
    {"type": "distributor", "label": "Distributor", "order": 2, "is_required": False},
    {"type": "wholesaler", "label": "Wholesaler", "order": 3, "is_required": False},
    {"type": "retailer", "label": "Retailer", "order": 4, "is_required": False},
    {"type": "market_demand", "label": "Market Demand", "order": 5, "is_required": True},
]


def upgrade() -> None:
    op.add_column(
        "supply_chain_configs",
        sa.Column(
            "node_type_definitions",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )

    default_payload = json.dumps(DEFAULT_DEFINITIONS)
    # Cast to json so the comparison works on both JSON and JSONB columns.
    update_stmt = sa.text(
        "UPDATE supply_chain_configs "
        "SET node_type_definitions = CAST(:payload AS json) "
        "WHERE node_type_definitions IS NULL "
        "   OR node_type_definitions::text = '[]'"
    )
    op.execute(update_stmt.bindparams(payload=default_payload))

    op.alter_column(
        "supply_chain_configs",
        "node_type_definitions",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("supply_chain_configs", "node_type_definitions")
