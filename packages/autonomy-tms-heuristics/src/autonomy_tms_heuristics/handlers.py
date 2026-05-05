"""TMS heuristic handlers — direct-call API.

Three read-side skills + four write-side skills (refused). Each handler
takes ``(tenant_id, plane_config, inp)`` keyword args and returns a dict
already stamped with the four-place warning regime.

Pattern (locked in 2026-05-04 — "PR-LOCK"):

  1. Build the corresponding state dataclass from cross-plane inputs +
     conservative defaults from ``plane_config``.
  2. Dispatch via ``library.compute_tms_decision(trm_type, state)`` —
     the SAME function used by internal TRMs as runtime fallback and by
     the training corpus generator as the BC teacher policy. Single
     source of TMS conservative-defaults math; cross-plane and
     internal paths cannot drift.
  3. Map the returned ``TMSHeuristicDecision`` back to the cross-plane
     skill's payload contract.
  4. Stamp the four-place warning regime.

ETA is the exception: ``library.lane_eta.estimate_eta`` is a pure math
primitive (haversine + speed model) rather than a dispatch entry, since
no internal TRM consumes pre-dispatch ETA-from-coordinates as a fallback
today. It still lives in the same library so future internal consumers
can adopt it without duplicating math.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from azirella_heuristics_common import (
    HeuristicWriteRefused,
    stamp_heuristic_response,
)

from .library import (
    Actions,
    FreightProcurementState,
    LoadBuildState,
    compute_tms_decision,
    estimate_eta,
)

logger = logging.getLogger(__name__)


HEURISTIC_PRODUCER_SIGNATURE = "autonomy-tms-heuristics:v0.1.0"
_PLANE = "tms"

# Standard FTL trailer dimensions used as the LoadBuildState capacity
# baseline when the cross-plane caller hasn't supplied weight/volume —
# i.e. always, since cross-plane callers don't have shipment-level data.
_DEFAULT_MAX_WEIGHT_LBS = 44000.0
_DEFAULT_MAX_VOLUME_CUFT = 2700.0
_DEFAULT_MAX_PALLETS = 26


# ---------------------------------------------------------------------------
# transport.lane.estimate_eta
# ---------------------------------------------------------------------------


def estimate_lane_eta(
    *,
    tenant_id: int,
    plane_config: Dict[str, Any],
    inp: Dict[str, Any],
) -> Dict[str, Any]:
    """Heuristic ETA — haversine × speed × dispatch buffer.

    Calls ``library.lane_eta.estimate_eta`` directly. Math primitive,
    not dispatch-routed (no internal TRM consumes pre-dispatch ETA).
    """
    for required in ("from_site_id", "to_site_id", "departure_at"):
        if not inp.get(required):
            raise ValueError(f"missing required input: {required}")

    departure = _parse_datetime(inp["departure_at"])
    from_coords = _coords_from_input(inp, "from_lat", "from_lon")
    to_coords = _coords_from_input(inp, "to_lat", "to_lon")

    result = estimate_eta(
        from_site_id=str(inp["from_site_id"]),
        to_site_id=str(inp["to_site_id"]),
        departure_at=departure,
        plane_config=plane_config,
        from_coords=from_coords,
        to_coords=to_coords,
    )

    skill_signature = f"{HEURISTIC_PRODUCER_SIGNATURE}:lane.estimate_eta"
    band = result.as_band(producer_signature=skill_signature)
    payload = {
        "from_site_id": str(inp["from_site_id"]),
        "to_site_id": str(inp["to_site_id"]),
        **result.as_dict(),
        "eta_band": band.as_dict(),
    }
    return stamp_heuristic_response(
        payload,
        plane=_PLANE,
        skill_id="transport.lane.estimate_eta",
        producer_signature=skill_signature,
    )


# ---------------------------------------------------------------------------
# transport.load.evaluate_consolidation
# ---------------------------------------------------------------------------


def evaluate_consolidation(
    *,
    tenant_id: int,
    plane_config: Dict[str, Any],
    inp: Dict[str, Any],
) -> Dict[str, Any]:
    """Heuristic consolidation — dispatches ``_compute_load_build`` with
    conservative-default state.

    The cross-plane caller has only ``shipment_ids``; we don't know
    lane economics, weights, volumes, or compatibility flags. Build a
    state with always-no defaults (zero weight / volume / savings) so
    the library's heuristic never returns ``CONSOLIDATE`` — the honest
    answer at HEURISTIC tier with no upstream data.
    """
    config_id = inp.get("config_id")
    shipment_ids = inp.get("shipment_ids") or []
    if config_id is None:
        raise ValueError("missing required input: config_id")
    if not shipment_ids:
        raise ValueError(
            "missing required input: shipment_ids (non-empty list)"
        )

    state = LoadBuildState(
        # Stub IDs — internal int IDs aren't visible at the cross-plane
        # boundary; the library doesn't validate the values.
        shipment_ids=list(range(len(shipment_ids))),
        max_weight=_DEFAULT_MAX_WEIGHT_LBS,
        max_volume=_DEFAULT_MAX_VOLUME_CUFT,
        max_pallets=_DEFAULT_MAX_PALLETS,
        total_weight=0.0,
        total_volume=0.0,
        total_pallets=0,
        shipment_count=len(shipment_ids),
        # ↓↓↓ no savings → fails the consolidation gate
        consolidation_savings=0.0,
        ftl_rate=0.0,
        ltl_rate_sum=0.0,
        # No cross-plane visibility into compatibility flags; default to
        # "no conflict" so the function reaches the savings-gate path
        # rather than returning REJECT (which would surface as
        # informational reasoning the caller doesn't need).
        has_hazmat_conflict=False,
        has_temp_conflict=False,
    )
    decision = compute_tms_decision("load_build", state)

    payload = {
        "config_id": int(config_id),
        "shipment_ids": [str(s) for s in shipment_ids],
        "shipment_count": len(shipment_ids),
        "recommend_consolidation": decision.action == Actions.CONSOLIDATE,
        "score": (
            decision.confidence
            if decision.action == Actions.CONSOLIDATE
            else 0.0
        ),
        "utilisation_estimate": None,
        "consolidation_cost_savings_estimate": None,
        "reasoning": (
            "TMS heuristic tier (library dispatch via compute_tms_decision): "
            f"{decision.reasoning}. Cross-plane callers have no lane "
            "economics, equipment fit, customer-mix compatibility, or "
            "hold-back time data, so the conservative default is "
            "ship-as-is. Real consolidation analysis requires upstream "
            "data the heuristic does not have."
        ),
    }
    return stamp_heuristic_response(
        payload,
        plane=_PLANE,
        skill_id="transport.load.evaluate_consolidation",
        producer_signature=(
            f"{HEURISTIC_PRODUCER_SIGNATURE}:load.evaluate_consolidation"
        ),
    )


# ---------------------------------------------------------------------------
# transport.carrier.recommend
# ---------------------------------------------------------------------------


def recommend_carrier(
    *,
    tenant_id: int,
    plane_config: Dict[str, Any],
    inp: Dict[str, Any],
) -> Dict[str, Any]:
    """Heuristic carrier recommendation — dispatches
    ``_compute_freight_procurement`` against a conservative-defaults
    state representing the tenant's default carrier.

    When ``plane_config["default_carrier_id"]`` is set, the
    recommendation is that carrier with neutral confidence (the
    library's dispatch reasoning is included as audit text). When no
    default is configured, the response is ``"unknown"`` with score
    zero — no library dispatch is invoked since there's no candidate
    to evaluate.
    """
    config_id = inp.get("config_id")
    load_id = inp.get("load_id")
    if config_id is None or load_id is None:
        raise ValueError(
            "missing required input: config_id and load_id"
        )
    top_n = int(inp.get("top_n", 3))

    default_carrier_id = plane_config.get("default_carrier_id")

    if default_carrier_id:
        state = FreightProcurementState(
            # Stub identifiers — cross-plane caller doesn't have TMS-
            # internal int IDs, and the library doesn't validate values.
            load_id=0,
            primary_carrier_id=1,
            primary_carrier_rate=1.0,
            primary_carrier_acceptance_pct=0.85,
            lead_time_hours=48.0,  # standard, avoids <4h fast-path
            tender_attempt=1,
            max_tender_attempts=3,
            market_tightness=0.5,
            dat_benchmark_rate=1.0,
        )
        decision = compute_tms_decision("freight_procurement", state)
        recommendations = [{
            "carrier_id": str(default_carrier_id),
            "score": 0.5,  # neutral mid-confidence
            "reasoning": (
                "Tenant default carrier from heuristic config "
                "(library dispatch via compute_tms_decision: "
                f"{decision.reasoning}). No real procurement waterfall "
                "executed — cross-plane callers have no carrier "
                "performance / market / lane data."
            ),
        }]
    else:
        recommendations = [{
            "carrier_id": "unknown",
            "score": 0.0,
            "reasoning": (
                "No default carrier configured for tenant; heuristic "
                "tier cannot select a real carrier (no library "
                "dispatch — no candidate to evaluate)."
            ),
        }]

    payload = {
        "load_id": str(load_id),
        "config_id": int(config_id),
        "top_n": top_n,
        "recommendations": recommendations,
    }
    return stamp_heuristic_response(
        payload,
        plane=_PLANE,
        skill_id="transport.carrier.recommend",
        producer_signature=(
            f"{HEURISTIC_PRODUCER_SIGNATURE}:carrier.recommend"
        ),
    )


# ---------------------------------------------------------------------------
# Write-side refusal
# ---------------------------------------------------------------------------


def refuse_write(skill_id: str) -> None:
    raise HeuristicWriteRefused(skill_id=skill_id, plane=_PLANE)


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------


HEURISTIC_HANDLERS: Dict[str, Any] = {
    "transport.lane.estimate_eta": estimate_lane_eta,
    "transport.load.evaluate_consolidation": evaluate_consolidation,
    "transport.carrier.recommend": recommend_carrier,
}


HEURISTIC_WRITE_SKILLS = frozenset({
    "transport.load.dispatch",
    "transport.shipment.tender",
    "transport.dock.schedule",
    "transport.equipment.reposition",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_datetime(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    raise ValueError(
        f"departure_at must be ISO-8601 string or datetime; got "
        f"{type(raw).__name__}"
    )


def _coords_from_input(
    inp: Dict[str, Any], lat_key: str, lon_key: str,
) -> Optional[Dict[str, float]]:
    lat = inp.get(lat_key)
    lon = inp.get(lon_key)
    if lat is None or lon is None:
        return None
    return {"lat": float(lat), "lon": float(lon)}


__all__ = [
    "HEURISTIC_HANDLERS",
    "HEURISTIC_PRODUCER_SIGNATURE",
    "HEURISTIC_WRITE_SKILLS",
    "estimate_lane_eta",
    "evaluate_consolidation",
    "recommend_carrier",
    "refuse_write",
]
