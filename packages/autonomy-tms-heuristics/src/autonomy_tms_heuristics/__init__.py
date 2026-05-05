"""autonomy-tms-heuristics — TMS-team-owned heuristic policies (AD-12).

This package is the **single authoritative source** of TMS conservative-
default heuristic math, used by three consumer paths (PR-LOCK, 2026-05-04):

1. **Internal TRM runtime fallback** — every TMS TRM in
   ``backend/app/services/powell/`` calls
   :func:`compute_tms_decision` (re-exported below) when its trained
   BC checkpoint is unavailable.
2. **Internal training corpus generation** — TMS pretraining scripts
   (``backend/scripts/pretraining/generate_tms_corpus.py``,
   ``trm_curriculum.py``) use :func:`compute_tms_decision` as the BC
   teacher policy. Same function as the runtime fallback; training
   labels and runtime fallback are guaranteed to agree.
3. **Cross-plane HEURISTIC-tier dispatch** by ``azirella-router`` —
   :data:`HEURISTIC_HANDLERS` is the entry-point bundle. Each handler
   builds the corresponding state dataclass from a cross-plane skill
   input, dispatches via :func:`compute_tms_decision`, and maps the
   result back to the cross-plane response shape.

This package depends only on:
- ``azirella-data-model`` (for ``ConformalBand``, geofence haversine math)
- ``azirella-heuristics-common`` (for the four-place warning regime
  + exceptions)

It does NOT depend on the TMS backend app — the TMS backend pip-installs
it like SCP/DP backends do, so the same import path
``autonomy_tms_heuristics.library.compute_tms_decision`` works in
every consumer environment.
"""
from .handlers import (
    HEURISTIC_HANDLERS,
    HEURISTIC_PRODUCER_SIGNATURE,
    HEURISTIC_WRITE_SKILLS,
    estimate_lane_eta,
    evaluate_consolidation,
    recommend_carrier,
    refuse_write,
)
from .library import (
    Actions,
    BUILT_IN_DEFAULTS,
    BrokerRoutingState,
    CapacityBufferState,
    CapacityPromiseState,
    DemandSensingState,
    DockSchedulingState,
    ETAResult,
    EquipmentRepositionState,
    ExceptionManagementState,
    FreightProcurementState,
    IntermodalTransferState,
    LaneVolumeForecastState,
    LoadBuildState,
    ShipmentTrackingState,
    TMSHeuristicDecision,
    compute_segmented_loads,
    compute_tms_decision,
    estimate_eta,
)

__version__ = "0.1.0"

__all__ = [
    # Library — state dataclasses + dispatch
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
    # Cross-plane handler bundle
    "HEURISTIC_HANDLERS",
    "HEURISTIC_PRODUCER_SIGNATURE",
    "HEURISTIC_WRITE_SKILLS",
    "estimate_lane_eta",
    "evaluate_consolidation",
    "get_handler_bundle",
    "recommend_carrier",
    "refuse_write",
]


def get_handler_bundle():
    """Entry-point factory for ``azirella-router``'s plugin discovery.

    Wraps :data:`HEURISTIC_HANDLERS`, :data:`HEURISTIC_WRITE_SKILLS`,
    and :func:`refuse_write` into a :class:`HeuristicPlaneBundle` the
    router can consume. Lazy-imports ``azirella_router`` so this
    package can be imported without it (e.g. by training scripts using
    the handlers directly).
    """
    from azirella_router import HeuristicPlaneBundle
    return HeuristicPlaneBundle(
        plane="tms",
        handlers=HEURISTIC_HANDLERS,
        write_skills=HEURISTIC_WRITE_SKILLS,
        refuse_write=refuse_write,
        producer_signature=HEURISTIC_PRODUCER_SIGNATURE,
    )
