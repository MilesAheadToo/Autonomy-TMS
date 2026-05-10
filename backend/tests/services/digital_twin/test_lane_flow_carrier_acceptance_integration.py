"""LaneFlowSimulator × CarrierAcceptance integration (PR-3.A).

Covers the opt-in feature flag:

  - Default behaviour (carrier_acceptance_model=None) is unchanged from
    pre-PR-3.A — all capacity-feasible loads are tendered AND accepted.
  - Opt-in path attaches a CarrierAcceptanceModel; tender outcomes
    affect equipment usage, dock-queue depth, and OutcomeEvent emission.
  - tender_declined events from the model carry reason_code +
    p_accept; capacity-rejected events carry the legacy reason.
  - reset() re-seeds the attached physics model alongside the simulator.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.digital_twin import (
    CarrierProfile,
    EquipmentProfile,
    LaneFlowAction,
    LaneFlowSimulator,
    LanePhysicsParams,
)
from app.services.digital_twin.physics import (
    CarrierAcceptanceModel,
    CarrierAcceptanceParams,
)
from app.services.digital_twin.shipment_generator import Phase1ShipmentGenerator
from azirella_data_model.digital_twin.twin_interface import TwinMode
from azirella_data_model.ml.outcome import OutcomeEvent
from azirella_demand_planning_contract import Tier


# ── Fixtures (matched to test_lane_flow_simulator.py) ────────────────


def _carriers() -> dict[str, CarrierProfile]:
    return {
        "spot-budget": CarrierProfile(  # naming convention → SPOT
            carrier_id="spot-budget",
            cost_per_load=80.0,
            on_time_rate=0.70,
            capacity_per_bucket=8,
        ),
        "carrier:acme": CarrierProfile(  # default → CONTRACTED
            carrier_id="carrier:acme",
            cost_per_load=120.0,
            on_time_rate=0.95,
            capacity_per_bucket=8,
        ),
    }


def _equipment() -> dict[str, EquipmentProfile]:
    return {
        "dry_van_53": EquipmentProfile(
            equipment_kind="dry_van_53", load_capacity_units=10.0,
        ),
    }


def _lane_params(
    initial_equipment: int = 20,
    transit_buckets: int = 1,
) -> LanePhysicsParams:
    return LanePhysicsParams(
        origin_site_id="site:1",
        destination_site_id="site:2",
        product_id="sku:A",
        transit_buckets=transit_buckets,
        initial_equipment=initial_equipment,
        dock_capacity_per_bucket=20,
        carriers=_carriers(),
        equipment_kinds=_equipment(),
        cost_target_per_load=100.0,
    )


def _generator() -> Phase1ShipmentGenerator:
    # High-volume generator so capacity isn't the binding constraint;
    # the test wants tender-acceptance to dominate dispatch.
    return Phase1ShipmentGenerator(
        candidate_lanes=[("site:1", "site:2")],
        candidate_products=["sku:A"],
        base_volumes={("site:1", "site:2", "sku:A"): 60.0},
        seed=42,
    )


def _make_sim(*, carrier_acceptance_model=None, scenario_market_tightness=0.0):
    return LaneFlowSimulator(
        generator=_generator(),
        tenant_id=1,
        config_id=1,
        lane_params=_lane_params(),
        tier=Tier.TACTICAL,
        horizon_buckets=4,
        mode=TwinMode.TRAINING,
        carrier_acceptance_model=carrier_acceptance_model,
        scenario_market_tightness=scenario_market_tightness,
    )


# ── Default path: backward compatibility ─────────────────────────────


def test_default_no_model_preserves_legacy_behaviour():
    """No CarrierAcceptanceModel attached → all tenders accepted within
    capacity. Behaviour identical to pre-PR-3.A."""
    events: list[OutcomeEvent] = []
    sim = LaneFlowSimulator(
        generator=_generator(),
        tenant_id=1, config_id=1,
        lane_params=_lane_params(),
        tier=Tier.TACTICAL,
        horizon_buckets=4,
        mode=TwinMode.TRAINING,
        outcome_sink=events.append,
        # carrier_acceptance_model is intentionally omitted (None)
    )
    sim.reset(scenario_seed=42, anchor_date=date(2026, 5, 3))
    action = LaneFlowAction(
        carrier_id="spot-budget", equipment_kind="dry_van_53",
        dispatch_offset_hours=0.0,
    )
    sim.step(action)

    # All declined events should be capacity-driven (legacy reason).
    declined = [e for e in events if e.outcome_kind == "tender_declined"]
    for e in declined:
        assert e.payload.get("reason") == "capacity_or_equipment_exhausted"
        # Legacy events don't carry p_accept or reason_code.
        assert "p_accept" not in e.payload
        assert "reason_code" not in e.payload


# ── Opt-in path: CarrierAcceptanceModel attached ─────────────────────


def test_attached_model_emits_p_accept_on_accepted_events():
    events: list[OutcomeEvent] = []
    model = CarrierAcceptanceModel(CarrierAcceptanceParams())
    sim = LaneFlowSimulator(
        generator=_generator(),
        tenant_id=1, config_id=1,
        lane_params=_lane_params(),
        tier=Tier.TACTICAL,
        horizon_buckets=4,
        mode=TwinMode.TRAINING,
        carrier_acceptance_model=model,
        outcome_sink=events.append,
    )
    sim.reset(scenario_seed=42, anchor_date=date(2026, 5, 3))
    action = LaneFlowAction(
        carrier_id="carrier:acme",  # contracted by naming convention
        equipment_kind="dry_van_53",
        dispatch_offset_hours=0.0,
    )
    sim.step(action)

    accepted = [e for e in events if e.outcome_kind == "tender_accepted"]
    assert len(accepted) > 0, "expected at least one accepted tender"
    for e in accepted:
        assert "p_accept" in e.payload
        assert "reason_code" in e.payload
        assert 0.0 <= e.payload["p_accept"] <= 1.0


def test_attached_model_distinguishes_tender_vs_capacity_rejects():
    """tender_declined events from the model carry reason='tender_rejected'
    + reason_code; capacity-rejected events carry the legacy reason."""
    events: list[OutcomeEvent] = []
    # Constructed so the spot carrier (low base 0.55) gets some rejections.
    model = CarrierAcceptanceModel(CarrierAcceptanceParams())
    sim = LaneFlowSimulator(
        generator=_generator(),
        tenant_id=1, config_id=1,
        lane_params=_lane_params(),
        tier=Tier.TACTICAL,
        horizon_buckets=4,
        mode=TwinMode.TRAINING,
        carrier_acceptance_model=model,
        outcome_sink=events.append,
        scenario_market_tightness=0.5,
    )
    sim.reset(scenario_seed=7, anchor_date=date(2026, 5, 3))
    # Use the spot carrier for a higher rejection rate.
    action = LaneFlowAction(
        carrier_id="spot-budget",
        equipment_kind="dry_van_53",
        dispatch_offset_hours=0.0,
    )
    # Run several steps to accumulate reject events.
    for _ in range(4):
        try:
            sim.step(action)
        except StopIteration:
            break

    declined = [e for e in events if e.outcome_kind == "tender_declined"]
    tender_rejects = [
        e for e in declined if e.payload.get("reason") == "tender_rejected"
    ]
    # We should see at least some tender-rejected events with this seed.
    assert tender_rejects, (
        "expected at least one tender_rejected event from a 4-step "
        "spot-carrier run with tightness=0.5"
    )
    for e in tender_rejects:
        assert "reason_code" in e.payload
        assert "p_accept" in e.payload


def test_attached_model_equipment_only_consumed_by_accepted_loads():
    """When the model rejects a tender, equipment is NOT consumed
    (the load was never dispatched).

    Uses ``transit_buckets=2`` deliberately: at ``transit_buckets=1`` a
    load dispatched in step ``t`` arrives in step ``t+1`` *within the
    same step() call* (the simulator does dispatch then arrive-resolve
    sequentially), so equipment leaves and returns inside one step and
    the post-step ``equipment_available`` always equals the initial
    pool — masking whether rejected loads consumed it. With
    ``transit_buckets=2`` the dispatched loads are still in flight at
    the post-step observation, so the assertion measures what it
    means to.
    """
    events: list[OutcomeEvent] = []
    model = CarrierAcceptanceModel(CarrierAcceptanceParams())
    sim = LaneFlowSimulator(
        generator=_generator(),
        tenant_id=1, config_id=1,
        lane_params=_lane_params(initial_equipment=20, transit_buckets=2),
        tier=Tier.TACTICAL,
        horizon_buckets=4,
        mode=TwinMode.TRAINING,
        carrier_acceptance_model=model,
        outcome_sink=events.append,
        scenario_market_tightness=0.5,
    )
    sim.reset(scenario_seed=99, anchor_date=date(2026, 5, 3))
    action = LaneFlowAction(
        carrier_id="spot-budget",
        equipment_kind="dry_van_53",
        dispatch_offset_hours=0.0,
    )
    obs, _, _, _ = sim.step(action)

    accepted_count = sum(
        1 for e in events if e.outcome_kind == "tender_accepted"
    )
    expected_equipment = 20 - accepted_count
    # Loads tender-rejected by model should NOT have decremented equipment.
    # Equipment tracked via _SimulatorState.equipment_available; verify
    # via observation surface.
    assert obs.equipment_available == expected_equipment, (
        f"equipment expected {expected_equipment} (initial 20 minus "
        f"{accepted_count} accepted), got {obs.equipment_available}"
    )
    # Rejected loads also produced tender_declined events, which is
    # what the model is supposed to do. Co-asserting here so the test
    # documents the full reject path.
    declined_count = sum(
        1 for e in events if e.outcome_kind == "tender_declined"
    )
    assert declined_count > 0, (
        "expected at least one tender_declined event under "
        "scenario_market_tightness=0.5 + spot carrier"
    )


def test_determinism_with_attached_model():
    """Same seed → identical accept/reject outcomes across runs."""
    def _run() -> list[bool]:
        events: list[OutcomeEvent] = []
        model = CarrierAcceptanceModel(CarrierAcceptanceParams())
        sim = LaneFlowSimulator(
            generator=_generator(),
            tenant_id=1, config_id=1,
            lane_params=_lane_params(),
            tier=Tier.TACTICAL,
            horizon_buckets=4,
            mode=TwinMode.TRAINING,
            carrier_acceptance_model=model,
            outcome_sink=events.append,
            scenario_market_tightness=0.3,
        )
        sim.reset(scenario_seed=42, anchor_date=date(2026, 5, 3))
        action = LaneFlowAction(
            carrier_id="spot-budget",
            equipment_kind="dry_van_53",
            dispatch_offset_hours=0.0,
        )
        sim.step(action)
        return [
            e.outcome_kind == "tender_accepted"
            for e in events
            if e.outcome_kind in ("tender_accepted", "tender_declined")
        ]

    a = _run()
    b = _run()
    assert a == b, "same-seed runs should produce identical tender outcomes"
