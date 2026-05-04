"""Add Case/Six-Pack/Bottle TBG node-type definitions

Revision ID: 20260115090000_add_manufacturer_group_node_types
Revises: 20251220090000_rename_order_lead_time_to_demand_lead_time
Create Date: 2026-01-15 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260115090000_add_manufacturer_group_node_types"
down_revision = "20251220090000"
branch_labels = None
depends_on = None

CASE_NODE_TYPE_DEFINITIONS = (
    '[{"type":"market_demand","label":"Market Demand","order":0,"is_required":true,"master_type":"market_demand"},'
    '{"type":"retailer","label":"Retailer","order":1,"is_required":false,"master_type":"inventory"},'
    '{"type":"wholesaler","label":"Wholesaler","order":2,"is_required":false,"master_type":"inventory"},'
    '{"type":"distributor","label":"Distributor","order":3,"is_required":false,"master_type":"inventory"},'
    '{"type":"case_mfg","label":"Case Mfg","order":4,"is_required":false,"master_type":"manufacturer"},'
    '{"type":"market_supply","label":"Market Supply","order":5,"is_required":true,"master_type":"market_supply"}]'
)

SIX_PACK_NODE_TYPE_DEFINITIONS = (
    '[{"type":"market_demand","label":"Market Demand","order":0,"is_required":true,"master_type":"market_demand"},'
    '{"type":"retailer","label":"Retailer","order":1,"is_required":false,"master_type":"inventory"},'
    '{"type":"wholesaler","label":"Wholesaler","order":2,"is_required":false,"master_type":"inventory"},'
    '{"type":"distributor","label":"Distributor","order":3,"is_required":false,"master_type":"inventory"},'
    '{"type":"case_mfg","label":"Case Mfg","order":4,"is_required":false,"master_type":"manufacturer"},'
    '{"type":"six_pack_mfg","label":"Six-Pack Mfg","order":5,"is_required":false,"master_type":"manufacturer"},'
    '{"type":"market_supply","label":"Market Supply","order":6,"is_required":true,"master_type":"market_supply"}]'
)

BOTTLE_NODE_TYPE_DEFINITIONS = (
    '[{"type":"market_demand","label":"Market Demand","order":0,"is_required":true,"master_type":"market_demand"},'
    '{"type":"retailer","label":"Retailer","order":1,"is_required":false,"master_type":"inventory"},'
    '{"type":"wholesaler","label":"Wholesaler","order":2,"is_required":false,"master_type":"inventory"},'
    '{"type":"distributor","label":"Distributor","order":3,"is_required":false,"master_type":"inventory"},'
    '{"type":"case_mfg","label":"Case Mfg","order":4,"is_required":false,"master_type":"manufacturer"},'
    '{"type":"six_pack_mfg","label":"Six-Pack Mfg","order":5,"is_required":false,"master_type":"manufacturer"},'
    '{"type":"bottle_mfg","label":"Bottle Mfg","order":6,"is_required":false,"master_type":"manufacturer"},'
    '{"type":"market_supply","label":"Market Supply","order":7,"is_required":true,"master_type":"market_supply"}]'
)

DEFAULT_TBG_NODE_TYPE_DEFINITIONS = (
    '[{"type":"market_demand","label":"Market Demand","order":0,"is_required":true,"master_type":"market_demand"},'
    '{"type":"retailer","label":"Retailer","order":1,"is_required":false,"master_type":"inventory"},'
    '{"type":"wholesaler","label":"Wholesaler","order":2,"is_required":false,"master_type":"inventory"},'
    '{"type":"distributor","label":"Distributor","order":3,"is_required":false,"master_type":"inventory"},'
    '{"type":"manufacturer","label":"Manufacturer","order":4,"is_required":false,"master_type":"inventory"},'
    '{"type":"market_supply","label":"Market Supply","order":5,"is_required":true,"master_type":"market_supply"}]'
)

LEGACY_TBG_NODE_TYPE_DEFINITIONS = (
    '[{"type":"market_supply","label":"Market Supply","order":0,"is_required":true,"master_type":"market_supply"},'
    '{"type":"manufacturer","label":"Manufacturer","order":1,"is_required":false,"master_type":"manufacturer"},'
    '{"type":"distributor","label":"Distributor","order":2,"is_required":false,"master_type":"inventory"},'
    '{"type":"wholesaler","label":"Wholesaler","order":3,"is_required":false,"master_type":"inventory"},'
    '{"type":"retailer","label":"Retailer","order":4,"is_required":false,"master_type":"inventory"},'
    '{"type":"market_demand","label":"Market Demand","order":5,"is_required":true,"master_type":"market_demand"}]'
)


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE supply_chain_configs "
            "SET node_type_definitions = :defs "
            "WHERE name = 'Case TBG'"
        ).bindparams(defs=CASE_NODE_TYPE_DEFINITIONS)
    )
    op.execute(
        sa.text(
            "UPDATE supply_chain_configs "
            "SET node_type_definitions = :defs "
            "WHERE name = 'Six-Pack TBG'"
        ).bindparams(defs=SIX_PACK_NODE_TYPE_DEFINITIONS)
    )
    op.execute(
        sa.text(
            "UPDATE supply_chain_configs "
            "SET node_type_definitions = :defs "
            "WHERE name = 'Bottle TBG'"
        ).bindparams(defs=BOTTLE_NODE_TYPE_DEFINITIONS)
    )
    op.execute(
        sa.text(
            "UPDATE supply_chain_configs "
            "SET node_type_definitions = :defs "
            "WHERE name = 'Default TBG'"
        ).bindparams(defs=DEFAULT_TBG_NODE_TYPE_DEFINITIONS)
    )


def downgrade() -> None:
    for name in ("Case TBG", "Six-Pack TBG", "Bottle TBG"):
        op.execute(
            sa.text(
                "UPDATE supply_chain_configs "
                "SET node_type_definitions = :defs "
                "WHERE name = :name"
            ).bindparams(defs=LEGACY_TBG_NODE_TYPE_DEFINITIONS, name=name)
        )
    op.execute(
        sa.text(
            "UPDATE supply_chain_configs "
            "SET node_type_definitions = :defs "
            "WHERE name = 'Default TBG'"
        ).bindparams(defs=LEGACY_TBG_NODE_TYPE_DEFINITIONS)
    )
