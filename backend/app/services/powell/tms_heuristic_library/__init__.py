"""TMS Heuristic Library — deterministic decision rules for transportation TRMs.

Each TMS TRM has a corresponding heuristic that encodes industry best practice.
The heuristic is the bootstrap policy used to seed (state, action, reward)
trajectories for TRM training, and the deterministic fallback when the
neural-network model is unavailable.

The library is **single-implementation, ERP-source-agnostic** — transportation
operations follow standard industry practice across SAP TM / Manhattan /
Blue Yonder / Oracle TM / MercuryGate. (Contrast with SCP, where heuristics
branch by ERP source — SAP / D365 / Odoo — because supply-chain field shapes
diverge per system.)

## Where this library used to live

This module was moved here from Core (``azirella_data_model.powell.tms.heuristic_library``)
on 2026-05-02 to satisfy the plane-module invariant in
``Autonomy-Core/CLAUDE.md``: product repos own *only* their decision-policy
modules; plane-specific TRMs and their heuristic policies belong in the
product repo, not Core. See ``Autonomy-Core/docs/MIGRATION_REGISTER.md``
§3.34 for the move-back rationale and §1.13 for the original placement-violation
context.
"""

from .base import (
    BrokerRoutingState,
    CapacityBufferState,
    CapacityPromiseState,
    DemandSensingState,
    DockSchedulingState,
    EquipmentRepositionState,
    ExceptionManagementState,
    FreightProcurementState,
    IntermodalTransferState,
    LaneVolumeForecastState,
    LoadBuildState,
    ShipmentTrackingState,
    TMSHeuristicDecision,
)
from .dispatch import compute_segmented_loads, compute_tms_decision

__all__ = [
    "TMSHeuristicDecision",
    "CapacityPromiseState",
    "ShipmentTrackingState",
    "DemandSensingState",
    "CapacityBufferState",
    "ExceptionManagementState",
    "FreightProcurementState",
    "BrokerRoutingState",
    "DockSchedulingState",
    "LoadBuildState",
    "IntermodalTransferState",
    "EquipmentRepositionState",
    "LaneVolumeForecastState",
    "compute_segmented_loads",
    "compute_tms_decision",
]
