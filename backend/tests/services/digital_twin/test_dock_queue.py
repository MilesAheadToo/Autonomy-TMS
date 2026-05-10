"""DockQueue physics + simulator integration (PR-3.C).

Covers:
  - validation (AppointmentContext + DockQueueParams)
  - bootstrap-prior means per appointment type (live_load, live_unload,
    drop_hook); empirical sample mean ≈ α × β
  - detention math: dwell − free_time within tolerance
  - PLAN_PRODUCTION returns Gamma mean (no RNG draws)
  - determinism: same seed → same dwell sequence
  - simulator integration: opt-in via constructor; default path
    preserves legacy behaviour; dwell metadata reaches outcome events
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
    AppointmentContext,
    AppointmentType,
    DockQueueModel,
    DockQueueParams,
    PhysicsModel,
)
from app.services.digital_twin.shipment_generator import Phase1ShipmentGenerator
from azirella_data_model.digital_twin.twin_interface import TwinMode
from azirella_data_model.ml.outcome import OutcomeEvent
from azirella_demand_planning_contract import Tier


# ── Validation ───────────────────────────────────────────────────────


def test_context_rejects_empty_carrier():
    with pytest.raises(ValueError, match="carrier_id"):
        AppointmentContext(
            carrier_id="", equipment_kind="dry_van",
            appointment_type=AppointmentType.LIVE_LOAD,
        )


def test_context_rejects_empty_equipment():
    with pytest.raises(ValueError, match="equipment_kind"):
        AppointmentContext(
            carrier_id="acme", equipment_kind="",
            appointment_type=AppointmentType.LIVE_LOAD,
        )


def test_context_rejects_negative_free_time():
    with pytest.raises(ValueError, match="free_time_minutes"):
        AppointmentContext(
            carrier_id="acme", equipment_kind="dry_van",
            appointment_type=AppointmentType.LIVE_LOAD,
            free_time_minutes=-1.0,
        )


def test_context_rejects_negative_detention_rate():
    with pytest.raises(ValueError, match="detention_rate"):
        AppointmentContext(
            carrier_id="acme", equipment_kind="dry_van",
            appointment_type=AppointmentType.LIVE_LOAD,
            detention_rate_per_hour=-1.0,
        )


def test_params_rejects_zero_alpha_or_beta():
    with pytest.raises(ValueError):
        DockQueueParams(live_load_alpha=0.0)
    with pytest.raises(ValueError):
        DockQueueParams(drop_hook_beta=0.0)


# ── Protocol compliance ──────────────────────────────────────────────


def test_protocol_compliance():
    assert isinstance(DockQueueModel(), PhysicsModel)


def test_step_before_reset_raises():
    model = DockQueueModel()
    ctx = AppointmentContext(
        carrier_id="a", equipment_kind="d",
        appointment_type=AppointmentType.LIVE_LOAD,
    )
    with pytest.raises(RuntimeError, match="reset"):
        model.step(ctx)


# ── Bootstrap-prior means per type ───────────────────────────────────


def _empirical_mean(model, ctx, n=2000):
    return sum(model.step(ctx).dwell_minutes for _ in range(n)) / n


def test_live_load_mean_matches_prior():
    """Gamma(α=2, β=45) → mean = 90 minutes."""
    model = DockQueueModel()
    model.reset(scenario_seed=42)
    ctx = AppointmentContext(
        carrier_id="a", equipment_kind="dry_van",
        appointment_type=AppointmentType.LIVE_LOAD,
    )
    mean = _empirical_mean(model, ctx, n=2000)
    assert 85 < mean < 95, f"live_load empirical mean {mean:.1f} ≠ ~90"


def test_live_unload_mean_matches_prior():
    """Gamma(α=2, β=30) → mean = 60 minutes."""
    model = DockQueueModel()
    model.reset(scenario_seed=42)
    ctx = AppointmentContext(
        carrier_id="a", equipment_kind="dry_van",
        appointment_type=AppointmentType.LIVE_UNLOAD,
    )
    mean = _empirical_mean(model, ctx, n=2000)
    assert 56 < mean < 64, f"live_unload mean {mean:.1f} ≠ ~60"


def test_drop_hook_mean_matches_prior():
    """Gamma(α=3, β=15) → mean = 45 minutes."""
    model = DockQueueModel()
    model.reset(scenario_seed=42)
    ctx = AppointmentContext(
        carrier_id="a", equipment_kind="dry_van",
        appointment_type=AppointmentType.DROP_HOOK,
    )
    mean = _empirical_mean(model, ctx, n=2000)
    assert 42 < mean < 48, f"drop_hook mean {mean:.1f} ≠ ~45"


# ── Detention math ───────────────────────────────────────────────────


def test_detention_math_within_free_time_zero_cost():
    """Dwell = 50 min (well within 120 min free time) → 0 detention."""
    model = DockQueueModel()
    model.reset(scenario_seed=42)
    # PLAN_PRODUCTION returns Gamma mean; for live_unload that's 60 min.
    class _PP:
        value = "plan_production"
    model.reset(scenario_seed=42, twin_mode=_PP())
    ctx = AppointmentContext(
        carrier_id="a", equipment_kind="dry_van",
        appointment_type=AppointmentType.LIVE_UNLOAD,
        free_time_minutes=120.0, detention_rate_per_hour=50.0,
    )
    out = model.step(ctx)
    assert out.dwell_minutes == 60.0
    assert out.detention_minutes_over_free == 0.0
    assert out.detention_cost == 0.0


def test_detention_math_over_free_time_cost_correct():
    class _PP:
        value = "plan_production"
    model = DockQueueModel()
    model.reset(scenario_seed=42, twin_mode=_PP())
    # live_load mean = 90 min, free_time 60 min → 30 min detention →
    # 30 / 60 × $80/hr = $40 detention cost.
    ctx = AppointmentContext(
        carrier_id="a", equipment_kind="dry_van",
        appointment_type=AppointmentType.LIVE_LOAD,
        free_time_minutes=60.0, detention_rate_per_hour=80.0,
    )
    out = model.step(ctx)
    assert out.dwell_minutes == 90.0
    assert out.detention_minutes_over_free == pytest.approx(30.0)
    assert out.detention_cost == pytest.approx(40.0)


# ── TwinMode + determinism ───────────────────────────────────────────


def test_plan_production_returns_mean_no_rng():
    class _PP:
        value = "plan_production"
    model = DockQueueModel()
    model.reset(scenario_seed=999, twin_mode=_PP())
    ctx = AppointmentContext(
        carrier_id="a", equipment_kind="dry_van",
        appointment_type=AppointmentType.LIVE_LOAD,
    )
    out1 = model.step(ctx)
    out2 = model.step(ctx)
    assert out1.dwell_minutes == out2.dwell_minutes == out1.mean_minutes


def test_determinism_same_seed():
    def _draws() -> list[float]:
        m = DockQueueModel()
        m.reset(scenario_seed=12345)
        ctx = AppointmentContext(
            carrier_id="a", equipment_kind="dry_van",
            appointment_type=AppointmentType.LIVE_LOAD,
        )
        return [m.step(ctx).dwell_minutes for _ in range(50)]

    assert _draws() == _draws()


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


def test_simulator_default_no_dock_model_no_dwell_metadata():
    """No model attached → shipment_delivered events don't carry dwell fields."""
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
    # Run two steps so loads have time to arrive.
    for _ in range(2):
        sim.step(LaneFlowAction(
            carrier_id="carrier:acme", equipment_kind="dry_van_53",
            dispatch_offset_hours=0.0,
        ))
    delivered = [
        e for e in events
        if e.outcome_kind in ("shipment_delivered", "shipment_late")
    ]
    assert delivered, "expected at least one delivered/late event"
    for e in delivered:
        assert "dwell_minutes" not in e.payload
        assert "detention_cost" not in e.payload


def test_simulator_attached_dock_model_emits_dwell_metadata():
    events: list[OutcomeEvent] = []
    sim = LaneFlowSimulator(
        generator=_generator(),
        tenant_id=1, config_id=1,
        lane_params=_lane_params(),
        tier=Tier.TACTICAL,
        horizon_buckets=4,
        mode=TwinMode.TRAINING,
        dock_queue_model=DockQueueModel(),
        scenario_appointment_type="live_unload",
        outcome_sink=events.append,
    )
    sim.reset(scenario_seed=42, anchor_date=date(2026, 5, 3))
    for _ in range(2):
        sim.step(LaneFlowAction(
            carrier_id="carrier:acme", equipment_kind="dry_van_53",
            dispatch_offset_hours=0.0,
        ))
    delivered = [
        e for e in events
        if e.outcome_kind in ("shipment_delivered", "shipment_late")
    ]
    assert delivered
    for e in delivered:
        assert "dwell_minutes" in e.payload
        assert "dwell_mean_minutes" in e.payload
        assert "detention_minutes_over_free" in e.payload
        assert "detention_cost" in e.payload
        assert e.payload["appointment_type"] == "live_unload"
        # live_unload mean should be ~60 min; realised values can vary
        # widely but must be positive.
        assert e.payload["dwell_minutes"] > 0
