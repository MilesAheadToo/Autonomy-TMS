"""TMS digital twin — carrier-flow plane.

Per Autonomy-Core/docs/architecture/TWIN_AND_ENVELOPES.md, the TMS twin
is split into:

  - shipment_generator: emits the exogenous TransferOrderEnvelope stream
    (line-item arrivals per origin/destination/product/bucket).
  - lane_flow_simulator: transition function over generator output —
    lane queues, dock dynamics, carrier capacity, equipment flow.
  - step_adapter: (observe, step, record) façade producing RL trajectories.

PR-1 is scaffolding — interfaces and dataclasses, no physics yet. PR-2
fills in the parametric shipment generator; PR-3 fills in the simulator.
"""
from __future__ import annotations

from .lane_flow_simulator import (
    CarrierProfile,
    EquipmentProfile,
    LaneFlowSimulator,
    LanePhysicsParams,
    RewardFn,
)
from .observations import (
    LaneFlowAction,
    LaneFlowObservation,
    LaneFlowReward,
    LaneFlowTransition,
)
from .phase2_fitter import (
    PHASE2_TENANT_CALIBRATED_PRODUCER_SIGNATURE,
    FitParameters,
    HistoricalShipment,
    fit_phase2_for_config,
    fit_phase2_shipment_generator,
    load_history_for_config,
    load_history_for_config_sync,
    load_phase2_generator_for_config,
)
from .shipment_generator import (
    PARAMETRIC_STUB_PRODUCER_SIGNATURE,
    Phase1ShipmentGenerator,
    ShipmentGenerator,
    lane_series_key,
)
from .step_adapter import LaneFlowStepAdapter

__all__ = [
    "CarrierProfile",
    "EquipmentProfile",
    "FitParameters",
    "HistoricalShipment",
    "LaneFlowAction",
    "LaneFlowObservation",
    "LaneFlowReward",
    "LaneFlowSimulator",
    "LaneFlowStepAdapter",
    "LaneFlowTransition",
    "LanePhysicsParams",
    "PARAMETRIC_STUB_PRODUCER_SIGNATURE",
    "PHASE2_TENANT_CALIBRATED_PRODUCER_SIGNATURE",
    "Phase1ShipmentGenerator",
    "RewardFn",
    "ShipmentGenerator",
    "fit_phase2_for_config",
    "fit_phase2_shipment_generator",
    "lane_series_key",
    "load_history_for_config",
    "load_history_for_config_sync",
    "load_phase2_generator_for_config",
]
