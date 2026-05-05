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

## Where this library lives — and why

This is the **single authoritative source** of TMS conservative-default
heuristics, used by three consumer paths:

1. **Internal TRM runtime fallback** — every TMS TRM (``LoadBuildTRM``,
   ``FreightProcurementTRM``, ``ShipmentTrackingTRM``, …) calls
   :func:`compute_tms_decision` when its trained BC checkpoint is
   unavailable. The function it calls IS the function below.
2. **Internal training corpus generation** —
   ``backend/scripts/pretraining/generate_tms_corpus.py`` and
   ``trm_curriculum.py`` use :func:`compute_tms_decision` as the
   teacher policy that produces BC-training labels. Same function;
   training labels and runtime fallback are guaranteed to agree.
3. **Cross-plane HEURISTIC-tier handlers** —
   :mod:`autonomy_tms_heuristics.handlers` builds the appropriate
   state dataclass from a cross-plane skill input + plane_config
   defaults, then dispatches via :func:`compute_tms_decision`. The
   cross-plane response shape is the result of mapping the returned
   :class:`TMSHeuristicDecision` back to the skill's payload contract.

This file used to live at
``backend/app/services/powell/tms_heuristic_library/`` (relocated here
2026-05-04 to make the package the canonical home; the prior in-repo
location duplicated math against ``eta.py`` in this same package, which
is the drift the consolidation prevents).

The library is **single-implementation, ERP-source-agnostic** —
transportation operations follow standard industry practice across
SAP TM / Manhattan / Blue Yonder / Oracle TM / MercuryGate. (Contrast
with SCP, where heuristics branch by ERP source — SAP / D365 / Odoo —
because supply-chain field shapes diverge per system.)
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
from .dispatch import Actions, compute_segmented_loads, compute_tms_decision
from .lane_eta import BUILT_IN_DEFAULTS, ETAResult, estimate_eta

__all__ = [
    "Actions",
    "BUILT_IN_DEFAULTS",
    "BrokerRoutingState",
    "CapacityBufferState",
    "CapacityPromiseState",
    "DemandSensingState",
    "DockSchedulingState",
    "ETAResult",
    "EquipmentRepositionState",
    "ExceptionManagementState",
    "FreightProcurementState",
    "IntermodalTransferState",
    "LaneVolumeForecastState",
    "LoadBuildState",
    "ShipmentTrackingState",
    "TMSHeuristicDecision",
    "compute_segmented_loads",
    "compute_tms_decision",
    "estimate_eta",
]
