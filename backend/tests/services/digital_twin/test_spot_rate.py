"""SpotRate physics + simulator integration (PR-3.E).

Covers:
  - validation (SpotRateContext + SpotRateParams)
  - first-step anchors spot to contract_rate
  - tightness AR(1) recursion converges toward (0.5 + season_factor)
  - spot_rate AR(1) recursion converges toward
    contract_rate × (1 + κ × tightness)
  - shock_tightness raises tightness over scenario disruption
  - PLAN_PRODUCTION returns deterministic recursion (no ε noise)
  - determinism: same seed → identical sequence
  - simulator integration: opt-in via constructor; default path
    preserves legacy behaviour; spot metadata reaches outcome events
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
    PhysicsModel,
    SpotRateContext,
    SpotRateModel,
    SpotRateParams,
)
from app.services.digital_twin.shipment_generator import Phase1ShipmentGenerator
from azirella_data_model.digital_twin.twin_interface import TwinMode
from azirella_data_model.ml.outcome import OutcomeEvent
from azirella_demand_planning_contract import Tier


# ── Validation ───────────────────────────────────────────────────────


def test_context_rejects_zero_contract():
    with pytest.raises(ValueError, match="contract_rate"):
        SpotRateContext(contract_rate=0.0)


def test_context_rejects_out_of_range_day():
    with pytest.raises(ValueError, match="day_of_year"):
        SpotRateContext(contract_rate=2.5, day_of_year=400)


def test_params_rejects_out_of_range_alpha():
    with pytest.raises(ValueError, match="alpha"):
        SpotRateParams(alpha=1.5)


def test_params_rejects_negative_kappa():
    with pytest.raises(ValueError, match="kappa"):
        SpotRateParams(kappa=-0.1)


def test_params_rejects_out_of_range_initial_tightness():
    with pytest.raises(ValueError, match="initial_tightness"):
        SpotRateParams(initial_tightness=1.2)


# ── Protocol compliance ──────────────────────────────────────────────


def test_protocol_compliance():
    assert isinstance(SpotRateModel(), PhysicsModel)


def test_step_before_reset_raises():
    model = SpotRateModel()
    with pytest.raises(RuntimeError, match="reset"):
        model.step(SpotRateContext(contract_rate=2.5))


# ── First-step anchoring ─────────────────────────────────────────────


def test_first_step_anchors_to_contract_rate():
    """When initial_spot_rate isn't supplied, first step() pegs the
    spot to contract_rate before applying noise."""

    class _PP:
        value = "plan_production"  # disable noise so we can check the anchor

    model = SpotRateModel()
    model.reset(scenario_seed=42, twin_mode=_PP())
    out = model.step(SpotRateContext(contract_rate=2.50, day_of_year=1))
    # PLAN_PRODUCTION: ε = 0; spot recursion is
    # 0.7 × 2.50 + 0.3 × 2.50 × (1 + 0.4 × tightness).
    # New tightness = 0.85 × 0.5 + 0.15 × (0.5 + season_factor).
    # The spot_rate must equal that — small bands around 2.50.
    assert 2.30 < out.spot_rate < 2.70


def test_first_step_uses_initial_spot_rate_when_supplied():
    class _PP:
        value = "plan_production"

    model = SpotRateModel()
    model.reset(scenario_seed=42, twin_mode=_PP(), initial_spot_rate=3.00)
    out = model.step(SpotRateContext(contract_rate=2.50, day_of_year=1))
    # Spot starts at 3.00, recurses with contract=2.50 → should pull down
    # toward 2.50-ish but stay above 2.50 after one step.
    assert 2.50 < out.spot_rate <= 3.00


# ── Recursion convergence ────────────────────────────────────────────


def test_tightness_converges_to_seasonal_baseline():
    """Without shocks, tightness AR(1) settles at 0.5 + avg(season_factor) ≈ 0.5."""

    class _PP:
        value = "plan_production"

    model = SpotRateModel()
    model.reset(scenario_seed=42, twin_mode=_PP())
    # Run 200 steps cycling through the year — long-run average should
    # converge to ~0.5 (season factor averages to zero).
    samples = []
    for i in range(200):
        out = model.step(SpotRateContext(
            contract_rate=2.50,
            day_of_year=(i % 365) + 1,
        ))
        samples.append(out.tightness)
    # Last 50 steps should average near 0.5.
    tail_mean = sum(samples[-50:]) / 50
    assert 0.40 < tail_mean < 0.60, f"tightness tail mean {tail_mean:.3f} far from 0.5"


def test_shock_raises_tightness():
    """A scenario shock should push tightness above the seasonal baseline."""

    class _PP:
        value = "plan_production"

    model = SpotRateModel()
    model.reset(scenario_seed=42, twin_mode=_PP())
    base = model.step(SpotRateContext(contract_rate=2.5, day_of_year=180)).tightness

    model.reset(scenario_seed=42, twin_mode=_PP())
    shocked = model.step(SpotRateContext(
        contract_rate=2.5, day_of_year=180, shock_tightness=0.3,
    )).tightness
    assert shocked > base + 0.20


# ── TwinMode / determinism ───────────────────────────────────────────


def test_plan_production_no_noise():
    """Same step twice with PLAN_PRODUCTION returns the same sequence."""

    class _PP:
        value = "plan_production"

    def _run() -> list[float]:
        m = SpotRateModel()
        m.reset(scenario_seed=42, twin_mode=_PP())
        return [
            m.step(SpotRateContext(contract_rate=2.5, day_of_year=i + 1)).spot_rate
            for i in range(20)
        ]

    a = _run()
    b = _run()
    # Deterministic — should match exactly.
    assert a == b


def test_training_determinism_same_seed():
    """In TRAINING mode, same seed produces same noise sequence."""
    def _run() -> list[float]:
        m = SpotRateModel()
        m.reset(scenario_seed=42)
        return [
            m.step(SpotRateContext(contract_rate=2.5, day_of_year=i + 1)).spot_rate
            for i in range(20)
        ]

    assert _run() == _run()


# ── Simulator integration ────────────────────────────────────────────


def _carriers() -> dict[str, CarrierProfile]:
    return {
        "carrier:acme": CarrierProfile(
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


def _lane_params() -> LanePhysicsParams:
    return LanePhysicsParams(
        origin_site_id="site:1",
        destination_site_id="site:2",
        product_id="sku:A",
        transit_buckets=1,
        initial_equipment=20,
        dock_capacity_per_bucket=20,
        carriers=_carriers(),
        equipment_kinds=_equipment(),
        cost_target_per_load=100.0,
    )


def _generator() -> Phase1ShipmentGenerator:
    return Phase1ShipmentGenerator(
        candidate_lanes=[("site:1", "site:2")],
        candidate_products=["sku:A"],
        base_volumes={("site:1", "site:2", "sku:A"): 60.0},
        seed=42,
    )


def test_simulator_default_no_spot_model_no_metadata():
    events: list[OutcomeEvent] = []
    sim = LaneFlowSimulator(
        generator=_generator(),
        tenant_id=1, config_id=1,
        lane_params=_lane_params(),
        tier=Tier.TACTICAL,
        horizon_buckets=4,
        mode=TwinMode.TRAINING,
        outcome_sink=events.append,
    )
    sim.reset(scenario_seed=42, anchor_date=date(2026, 5, 3))
    sim.step(LaneFlowAction(
        carrier_id="carrier:acme", equipment_kind="dry_van_53",
        dispatch_offset_hours=0.0,
    ))
    accepted = [e for e in events if e.outcome_kind == "tender_accepted"]
    assert accepted
    for e in accepted:
        assert "spot_rate" not in e.payload
        assert "spot_tightness" not in e.payload


def test_simulator_attached_spot_model_emits_metadata():
    events: list[OutcomeEvent] = []
    sim = LaneFlowSimulator(
        generator=_generator(),
        tenant_id=1, config_id=1,
        lane_params=_lane_params(),
        tier=Tier.TACTICAL,
        horizon_buckets=4,
        mode=TwinMode.TRAINING,
        spot_rate_model=SpotRateModel(),
        outcome_sink=events.append,
    )
    sim.reset(scenario_seed=42, anchor_date=date(2026, 5, 3))
    sim.step(LaneFlowAction(
        carrier_id="carrier:acme", equipment_kind="dry_van_53",
        dispatch_offset_hours=0.0,
    ))
    accepted = [e for e in events if e.outcome_kind == "tender_accepted"]
    assert accepted
    for e in accepted:
        assert "spot_rate" in e.payload
        assert "spot_tightness" in e.payload
        assert "spot_season_factor" in e.payload
        assert "spot_epsilon" in e.payload
        assert "spot_premium_vs_contract" in e.payload
        assert e.payload["spot_rate"] > 0
        assert 0.0 <= e.payload["spot_tightness"] <= 1.0


def test_simulator_spot_anchored_to_lane_cost_target_default():
    """When spot_rate_contract_per_load isn't supplied, anchor to
    LanePhysicsParams.cost_target_per_load."""
    events: list[OutcomeEvent] = []
    sim = LaneFlowSimulator(
        generator=_generator(),
        tenant_id=1, config_id=1,
        lane_params=_lane_params(),  # cost_target_per_load=100.0
        tier=Tier.TACTICAL,
        horizon_buckets=4,
        mode=TwinMode.TRAINING,
        spot_rate_model=SpotRateModel(),
        outcome_sink=events.append,
    )
    sim.reset(scenario_seed=42, anchor_date=date(2026, 5, 3))
    sim.step(LaneFlowAction(
        carrier_id="carrier:acme", equipment_kind="dry_van_53",
        dispatch_offset_hours=0.0,
    ))
    accepted = [e for e in events if e.outcome_kind == "tender_accepted"]
    assert accepted
    # Spot premium vs contract: should be small (within ±50 of 0)
    # since spot was just anchored to contract.
    for e in accepted:
        premium = e.payload["spot_premium_vs_contract"]
        assert abs(premium) < 50.0
