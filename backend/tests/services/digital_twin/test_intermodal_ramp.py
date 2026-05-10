"""IntermodalRampModel physics tests (PR-3.G).

Covers:
  - parameter / context validation
  - Protocol compliance + step-before-reset guard
  - acceptance probability vs. congestion_level + accepting_today
  - rail transit bootstrap mean ≈ truck × 1.5
  - rail conformal bands bracket the mean (P10 < mean < P90)
  - season modulation (winter > summer)
  - cost decomposition (drayage / ramp / rail / ramp / drayage)
  - PLAN_PRODUCTION determinism
  - same-seed determinism
"""
from __future__ import annotations

import pytest

from app.services.digital_twin.physics import (
    IntermodalContext,
    IntermodalOutcome,
    IntermodalRampModel,
    IntermodalRampParams,
    PhysicsModel,
)
from azirella_data_model.digital_twin.twin_interface import TwinMode


# ── Validation ───────────────────────────────────────────────────────


def test_context_rejects_empty_origin_ramp():
    with pytest.raises(ValueError, match="origin_ramp_id"):
        IntermodalContext(origin_ramp_id="", destination_ramp_id="r2")


def test_context_rejects_empty_destination_ramp():
    with pytest.raises(ValueError, match="destination_ramp_id"):
        IntermodalContext(origin_ramp_id="r1", destination_ramp_id="")


def test_context_rejects_out_of_range_congestion():
    with pytest.raises(ValueError, match="congestion_level"):
        IntermodalContext(
            origin_ramp_id="r1", destination_ramp_id="r2", congestion_level=1.5
        )


def test_context_rejects_zero_truck_transit():
    with pytest.raises(ValueError, match="truck_transit_buckets"):
        IntermodalContext(
            origin_ramp_id="r1", destination_ramp_id="r2", truck_transit_buckets=0
        )


def test_context_rejects_negative_drayage():
    with pytest.raises(ValueError, match="drayage_origin_miles"):
        IntermodalContext(
            origin_ramp_id="r1", destination_ramp_id="r2",
            drayage_origin_miles=-5.0,
        )


def test_params_rejects_p_accept_above_one():
    with pytest.raises(ValueError, match="base_p_accept"):
        IntermodalRampParams(base_p_accept=1.5)


def test_params_rejects_zero_rail_to_truck_ratio():
    with pytest.raises(ValueError, match="rail_to_truck_ratio"):
        IntermodalRampParams(rail_to_truck_ratio=0.0)


def test_params_rejects_negative_drayage_rate():
    with pytest.raises(ValueError, match="drayage_rate_per_mile"):
        IntermodalRampParams(drayage_rate_per_mile=-0.5)


# ── Protocol compliance ──────────────────────────────────────────────


def test_protocol_compliance():
    model = IntermodalRampModel()
    assert isinstance(model, PhysicsModel)


def test_step_before_reset_raises():
    model = IntermodalRampModel()
    with pytest.raises(RuntimeError, match="reset"):
        model.step(IntermodalContext(origin_ramp_id="r1", destination_ramp_id="r2"))


# ── Acceptance behaviour ─────────────────────────────────────────────


def _accept_rate(model: IntermodalRampModel, ctx: IntermodalContext, n: int = 2000) -> float:
    fires = sum(1 for _ in range(n) if model.step(ctx).accepted)
    return fires / n


def test_baseline_accept_rate_matches_p_baseline():
    model = IntermodalRampModel()
    model.reset(scenario_seed=42)
    rate = _accept_rate(
        model,
        IntermodalContext(origin_ramp_id="r1", destination_ramp_id="r2"),
    )
    assert 0.89 < rate < 0.95, f"expected ~0.92, got {rate:.4f}"


def test_high_congestion_drops_accept_rate():
    model = IntermodalRampModel()
    model.reset(scenario_seed=42)
    rate = _accept_rate(
        model,
        IntermodalContext(
            origin_ramp_id="r1", destination_ramp_id="r2",
            congestion_level=0.8,
        ),
    )
    # p_accept = 0.92 * (1 - 0.8) = 0.184
    assert 0.15 < rate < 0.22, f"expected ~0.184, got {rate:.4f}"


def test_closed_ramp_returns_zero_accepts():
    model = IntermodalRampModel()
    model.reset(scenario_seed=42)
    for _ in range(50):
        out = model.step(
            IntermodalContext(
                origin_ramp_id="r1", destination_ramp_id="r2",
                accepting_today=False,
            )
        )
        assert out.accepted is False
        assert out.reason_code == "rejected:closed"
        assert out.p_accept == 0.0
        assert out.cost is None


def test_rejected_outcome_carries_no_cost():
    """Rejected tenders surface ``cost=None`` (caller falls back to truck)."""
    model = IntermodalRampModel()
    model.reset(scenario_seed=42)
    rejections = []
    for _ in range(500):
        out = model.step(
            IntermodalContext(
                origin_ramp_id="r1", destination_ramp_id="r2",
                congestion_level=0.99,
            )
        )
        if not out.accepted:
            rejections.append(out)
    assert rejections
    for r in rejections:
        assert r.cost is None
        assert r.rail_transit_realised_buckets == 0


# ── Rail transit ─────────────────────────────────────────────────────


def test_rail_transit_mean_matches_ratio():
    """At zero congestion + day_of_year=0, rail mean ≈ truck × ratio."""
    model = IntermodalRampModel()
    model.reset(scenario_seed=42)
    accepted = []
    for _ in range(500):
        out = model.step(
            IntermodalContext(
                origin_ramp_id="r1", destination_ramp_id="r2",
                truck_transit_buckets=4,
            )
        )
        if out.accepted:
            accepted.append(out)
    assert accepted
    # All accepted draws share the same mean (deterministic per ctx).
    means = {o.rail_transit_mean_buckets for o in accepted}
    assert len(means) == 1
    expected = 4 * 1.5  # truck × rail_to_truck_ratio
    assert abs(next(iter(means)) - expected) < 1e-9


def test_rail_conformal_bands_bracket_mean():
    """For every accepted outcome, P10 ≤ mean ≤ P90."""
    model = IntermodalRampModel()
    model.reset(scenario_seed=42)
    out = None
    for _ in range(50):
        candidate = model.step(
            IntermodalContext(
                origin_ramp_id="r1", destination_ramp_id="r2",
                truck_transit_buckets=4,
            )
        )
        if candidate.accepted:
            out = candidate
            break
    assert out is not None
    assert out.rail_transit_p10_buckets <= out.rail_transit_mean_buckets
    assert out.rail_transit_mean_buckets <= out.rail_transit_p90_buckets


def test_rail_transit_realised_unbiased_at_no_modulation():
    """Empirical mean of realised draws ≈ analytical mean."""
    model = IntermodalRampModel()
    model.reset(scenario_seed=42)
    realised = []
    for _ in range(2000):
        out = model.step(
            IntermodalContext(
                origin_ramp_id="r1", destination_ramp_id="r2",
                truck_transit_buckets=4,
            )
        )
        if out.accepted:
            realised.append(out.rail_transit_realised_buckets)
    assert realised
    empirical = sum(realised) / len(realised)
    expected = 4 * 1.5
    # Loose bound — small lognormal sigma + integer rounding makes
    # the mean cluster near 6.
    assert 5.5 < empirical < 6.5, (
        f"expected ~{expected}, got {empirical:.3f}"
    )


def test_winter_season_slows_rail_relative_to_summer():
    """Mean rail transit is higher in winter (day 15) vs summer (day 196)."""
    model = IntermodalRampModel()
    model.reset(scenario_seed=42)
    winter = model.step(
        IntermodalContext(
            origin_ramp_id="r1", destination_ramp_id="r2",
            truck_transit_buckets=10,
            day_of_year=15,  # winter peak
        )
    )
    summer = model.step(
        IntermodalContext(
            origin_ramp_id="r1", destination_ramp_id="r2",
            truck_transit_buckets=10,
            day_of_year=196,  # summer trough
        )
    )
    # If both got rejected by chance, retry — but probability is tiny.
    assert winter.accepted and summer.accepted
    assert winter.rail_transit_mean_buckets > summer.rail_transit_mean_buckets


# ── Cost decomposition ───────────────────────────────────────────────


def test_cost_decomposition_components_match_inputs():
    """Drayage cost = miles × rate; ramp fees = 2 × $50; rail = realised × rate."""
    model = IntermodalRampModel()
    model.reset(scenario_seed=42)
    # Use zero-congestion to maximise accept probability + drive deterministic
    # cost shape modulo rail draw.
    out = None
    for _ in range(50):
        candidate = model.step(
            IntermodalContext(
                origin_ramp_id="r1", destination_ramp_id="r2",
                drayage_origin_miles=10.0,
                drayage_destination_miles=15.0,
                truck_transit_buckets=2,
            )
        )
        if candidate.accepted:
            out = candidate
            break
    assert out is not None and out.cost is not None
    # 10 mi × $4.50 = $45
    assert out.cost.drayage_origin == pytest.approx(45.0)
    # 15 mi × $4.50 = $67.50
    assert out.cost.drayage_destination == pytest.approx(67.5)
    assert out.cost.ramp_fee_origin == pytest.approx(50.0)
    assert out.cost.ramp_fee_destination == pytest.approx(50.0)
    # rail = realised × 200/bucket
    expected_rail = out.rail_transit_realised_buckets * 200.0
    assert out.cost.rail == pytest.approx(expected_rail)
    # all_in property sums correctly.
    assert out.cost.all_in == pytest.approx(
        45.0 + 50.0 + expected_rail + 50.0 + 67.5
    )


def test_long_drayage_makes_intermodal_expensive():
    """A 100-mile origin drayage shifts the cost mix to drayage-heavy."""
    model = IntermodalRampModel()
    model.reset(scenario_seed=42)
    out = None
    for _ in range(50):
        candidate = model.step(
            IntermodalContext(
                origin_ramp_id="r1", destination_ramp_id="r2",
                drayage_origin_miles=100.0,
                drayage_destination_miles=10.0,
                truck_transit_buckets=2,
            )
        )
        if candidate.accepted:
            out = candidate
            break
    assert out is not None and out.cost is not None
    # 100 × $4.50 = $450 origin drayage — should dominate the breakdown.
    assert out.cost.drayage_origin == pytest.approx(450.0)
    # The ramp fee is now a small fraction of the all-in.
    assert out.cost.ramp_fee_origin / out.cost.all_in < 0.10


# ── PLAN_PRODUCTION mode ─────────────────────────────────────────────


def test_plan_production_acceptance_thresholds_at_half():
    """PLAN_PRODUCTION threshold-rounds: p_accept >= 0.5 → accepted."""
    model = IntermodalRampModel()
    model.reset(scenario_seed=42, twin_mode=TwinMode.PLAN_PRODUCTION)
    # Baseline 0.92 → above threshold → always accept.
    for _ in range(20):
        out = model.step(
            IntermodalContext(origin_ramp_id="r1", destination_ramp_id="r2")
        )
        assert out.accepted is True


def test_plan_production_high_congestion_always_rejects():
    """0.92 × (1 - 0.6) = 0.368 → below threshold → always reject."""
    model = IntermodalRampModel()
    model.reset(scenario_seed=42, twin_mode=TwinMode.PLAN_PRODUCTION)
    for _ in range(20):
        out = model.step(
            IntermodalContext(
                origin_ramp_id="r1", destination_ramp_id="r2",
                congestion_level=0.6,
            )
        )
        assert out.accepted is False


def test_plan_production_uses_mean_rail_transit():
    """No lognormal draw — realised == round(mean)."""
    model = IntermodalRampModel()
    model.reset(scenario_seed=42, twin_mode=TwinMode.PLAN_PRODUCTION)
    out = model.step(
        IntermodalContext(
            origin_ramp_id="r1", destination_ramp_id="r2",
            truck_transit_buckets=4,
        )
    )
    assert out.accepted
    # 4 × 1.5 = 6 → realised should be exactly 6.
    assert out.rail_transit_realised_buckets == 6


# ── Determinism ──────────────────────────────────────────────────────


def test_same_seed_same_trajectory():
    model_a = IntermodalRampModel()
    model_a.reset(scenario_seed=99)
    a = [
        model_a.step(
            IntermodalContext(origin_ramp_id="r1", destination_ramp_id="r2")
        ).accepted
        for _ in range(200)
    ]
    model_b = IntermodalRampModel()
    model_b.reset(scenario_seed=99)
    b = [
        model_b.step(
            IntermodalContext(origin_ramp_id="r1", destination_ramp_id="r2")
        ).accepted
        for _ in range(200)
    ]
    assert a == b


def test_different_seeds_diverge():
    out_a = []
    out_b = []
    for seed, sink in [(1, out_a), (12345, out_b)]:
        m = IntermodalRampModel()
        m.reset(scenario_seed=seed)
        for _ in range(500):
            o = m.step(
                IntermodalContext(
                    origin_ramp_id="r1", destination_ramp_id="r2",
                    truck_transit_buckets=4,
                )
            )
            sink.append((o.accepted, o.rail_transit_realised_buckets))
    assert out_a != out_b
