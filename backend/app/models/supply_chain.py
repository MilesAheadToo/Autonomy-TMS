"""TMS supply_chain shim — re-exports canonical from Core.

The canonical `ScenarioUserInventory`, `Order`, `ScenarioPeriod`,
`ScenarioUserPeriod` plus the `PeriodPhase` and `UpstreamOrderType`
enums live in `azirella_data_model.simulation.supply_chain`
(promoted 2026-05-14 per MIGRATION_REGISTER §3.73 Step 2). Both SCP
and TMS carried byte-near-identical copies before promotion.

The back-relations (`ScenarioUser.inventory`, `ScenarioUser.orders`,
`ScenarioUser.scenario_user_periods`, `Scenario.supply_chain_periods`)
also moved to Core in the same change — see
`azirella_data_model.simulation.scenario_user` and
`azirella_data_model.simulation.scenario`. Plane-side monkey-patches
for these specific attributes were removed.
"""
from azirella_data_model.simulation.supply_chain import (  # noqa: F401
    Order,
    PeriodPhase,
    ScenarioPeriod,
    ScenarioUserInventory,
    ScenarioUserPeriod,
    UpstreamOrderType,
)


# Backward-compatibility aliases for callsites still on the pre-2026
# Participant naming. To be removed once all callers are migrated.
ParticipantInventory = ScenarioUserInventory
ParticipantPeriod = ScenarioUserPeriod


__all__ = [
    "Order",
    "PeriodPhase",
    "ScenarioPeriod",
    "ScenarioUserInventory",
    "ScenarioUserPeriod",
    "UpstreamOrderType",
    "ParticipantInventory",
    "ParticipantPeriod",
]
