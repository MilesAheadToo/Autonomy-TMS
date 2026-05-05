"""
Integration test: shadow-price + BSC re-ranker composition.

Exercises the four cells of the (shadow_price ∈ {off, on}) ×
(bsc ∈ {off, on}) matrix on an IntermodalTransferTRM-style decision
and reports whether the two layers compose cleanly or fight.

Synthetic state — no DB writes, no real shipments. The point is
to verify that:

  1. Shadow-price alone shifts the heuristic's cost ranking
     (already verified upstream in 1efbd76; confirm here).
  2. BSC alone produces a sensible multi-perspective score.
  3. With both layers, BSC sees the shadow-price-adjusted cost
     as the FINANCIAL axis input and the original service
     attributes as the CUSTOMER axis input. The two layers
     compose because they target different axes, not the same
     one.
  4. The TMS shadow-price miss-cost can plausibly flip
     intermodal ACCEPT→REJECT while BSC simultaneously prefers
     intermodal on Customer (faster truck for high-priority
     order).

Smoke-only — does not require a tenant with seeded BSC weights
(uses tenant_id=None and the fallback path).
"""
from __future__ import annotations

import dataclasses
import sys
from typing import Optional

from autonomy_tms_heuristics.library.base import (
    IntermodalTransferState,
)
from autonomy_tms_heuristics.library.dispatch import (
    compute_tms_decision,
)
from azirella_data_model.intersections.supply_transport.shadow_prices import (
    ShadowPriceVector, compute_miss_cost,
)
from azirella_data_model.governance import rerank_with_bsc


def _base_state(*, generous: bool = False) -> IntermodalTransferState:
    """`generous=False` (default) is the borderline case used in
    Cells A-E: marginal cost savings, tight transit window — heuristic
    rejects. `generous=True` is a clearly favourable intermodal case
    used in Cells F-G: 30% savings, ample window — heuristic accepts.
    """
    if generous:
        return IntermodalTransferState(
            shipment_id=43,
            current_mode="FTL",
            candidate_mode="RAIL_INTERMODAL",
            origin_to_ramp_miles=40.0,
            ramp_to_ramp_miles=1800.0,
            ramp_to_dest_miles=60.0,
            total_truck_miles=1900.0,
            truck_rate=4500.0,
            intermodal_rate=3000.0,        # 33% savings
            drayage_rate_origin=200.0,
            drayage_rate_dest=300.0,
            truck_transit_days=4.0,
            intermodal_transit_days=5.5,   # 1.5d penalty
            delivery_window_days=10.0,     # ample slack
            rail_capacity_available=True,
            ramp_congestion_level=0.05,
            intermodal_reliability_pct=0.92,
            weather_risk_score=0.05,
            is_hazmat=False,
            is_temperature_controlled=False,
            origin_ramp_distance_miles=40.0,
            dest_ramp_distance_miles=60.0,
        )
    return IntermodalTransferState(
        shipment_id=42,
        current_mode="FTL",
        candidate_mode="RAIL_INTERMODAL",
        origin_to_ramp_miles=80.0,
        ramp_to_ramp_miles=1500.0,
        ramp_to_dest_miles=120.0,
        total_truck_miles=1700.0,
        truck_rate=3500.0,
        intermodal_rate=3200.0,            # ~8.5% savings
        drayage_rate_origin=400.0,
        drayage_rate_dest=600.0,
        truck_transit_days=3.0,
        intermodal_transit_days=5.0,       # 2-day penalty
        delivery_window_days=3.0,          # fits, but close
        rail_capacity_available=True,
        ramp_congestion_level=0.2,
        intermodal_reliability_pct=0.88,
        weather_risk_score=0.1,
        is_hazmat=False,
        is_temperature_controlled=False,
        origin_ramp_distance_miles=80.0,
        dest_ramp_distance_miles=120.0,
    )


def _apply_shadow_price(
    state: IntermodalTransferState,
    miss_per_unit_per_day: float,
    required_qty: float,
) -> IntermodalTransferState:
    """Mirror what intermodal_transfer_trm._apply_shadow_prices does:
    bend intermodal_rate up by miss_cost so the heuristic's cost
    compare reflects the SCP-side service-miss penalty."""
    spv = ShadowPriceVector(
        miss_per_unit_per_day=miss_per_unit_per_day,
        earliness_per_unit_per_day=0.0,
        priority=2,
    )
    days_late = state.transit_time_penalty_days()
    miss_cost = compute_miss_cost(spv, qty=required_qty, days_late=days_late)
    return dataclasses.replace(
        state,
        intermodal_rate=state.intermodal_rate + miss_cost,
    )


def _bsc_actuals(state: IntermodalTransferState, action_name: str) -> dict:
    """Approximate per-action actuals for BSC scoring."""
    if action_name == "ACCEPT":  # use intermodal
        return {
            "total_cost": state.intermodal_rate,
            "service_level": min(
                1.0, state.delivery_window_days
                / max(1.0, state.intermodal_transit_days),
            ),
            "transit_days": state.intermodal_transit_days,
        }
    # REJECT → use truck
    return {
        "total_cost": state.truck_rate,
        "service_level": min(
            1.0, state.delivery_window_days
            / max(1.0, state.truck_transit_days),
        ),
        "transit_days": state.truck_transit_days,
    }


def _run_cell(
    label: str,
    *,
    apply_shadow: bool,
    apply_bsc: bool,
    miss_price: float = 0.0,
    qty: float = 100.0,
    generous: bool = False,
):
    state = _base_state(generous=generous)
    if apply_shadow:
        state = _apply_shadow_price(state, miss_price, qty)

    # Heuristic decision against the (possibly shadow-adjusted) state.
    decision = compute_tms_decision("intermodal_transfer", state)
    action_name = {0: "ACCEPT", 1: "REJECT", 2: "DEFER", 3: "ESCALATE"}.get(
        decision.action, "UNKNOWN"
    )

    bsc_pick = None
    if apply_bsc:
        # Build the two-candidate set: ACCEPT (intermodal) vs REJECT
        # (truck). Use the same shadow-adjusted state so the BSC
        # FINANCIAL axis sees the same numbers the heuristic did.
        cands = [
            ("ACCEPT", _bsc_actuals(state, "ACCEPT")),
            ("REJECT", _bsc_actuals(state, "REJECT")),
        ]
        # No tenant seeded → fallback_score_fn picks. Use a service-
        # weighted score so BSC layer mimics a service-leaning
        # tenant.
        result = rerank_with_bsc(
            db=None,
            tenant_id=None,
            candidates=cands,
            fallback_score_fn=(
                lambda lbl, ax: 100.0 * ax["service_level"] - 0.01 * ax["total_cost"]
            ),
        )
        bsc_pick = result.chosen.action

    print(f"--- {label} ---")
    print(f"  intermodal_rate (after shadow): {state.intermodal_rate:.0f}")
    print(f"  truck_rate:                     {state.truck_rate:.0f}")
    print(f"  cost_savings_pct:               {state.cost_savings_pct():.1%}")
    print(f"  heuristic decision:             {action_name}")
    if apply_bsc:
        print(f"  BSC re-rank pick:               {bsc_pick}")
        composes_cleanly = (
            action_name == bsc_pick
            or action_name in ("DEFER", "ESCALATE")
        )
        print(f"  composes cleanly:               "
              f"{'YES' if composes_cleanly else 'NO (BSC overrides heuristic)'}")
    print()


print("=" * 60)
print("Composition matrix: shadow-price × BSC re-ranker")
print("=" * 60, "\n")

_run_cell("Cell A: shadow=off, bsc=off (pure heuristic baseline)",
          apply_shadow=False, apply_bsc=False)

_run_cell("Cell B: shadow=on,  bsc=off (current TMS behaviour, high SP)",
          apply_shadow=True, apply_bsc=False, miss_price=20.0, qty=100.0)

_run_cell("Cell C: shadow=off, bsc=on  (BSC service-leaning fallback)",
          apply_shadow=False, apply_bsc=True)

_run_cell("Cell D: shadow=on,  bsc=on  (composed)",
          apply_shadow=True, apply_bsc=True, miss_price=20.0, qty=100.0)

# Boundary case: very small SP — should NOT flip the heuristic.
_run_cell("Cell E: shadow=on (low SP),  bsc=on",
          apply_shadow=True, apply_bsc=True, miss_price=0.5, qty=100.0)

# Generous case: heuristic ACCEPTs intermodal. Now test what
# happens with a service-leaning BSC on top.
_run_cell("Cell F: generous, shadow=off, bsc=off",
          apply_shadow=False, apply_bsc=False, generous=True)

_run_cell("Cell G: generous, shadow=off, bsc=on (service-leaning)",
          apply_shadow=False, apply_bsc=True, generous=True)

_run_cell("Cell H: generous, shadow=on (high SP), bsc=on",
          apply_shadow=True, apply_bsc=True,
          miss_price=20.0, qty=100.0, generous=True)
