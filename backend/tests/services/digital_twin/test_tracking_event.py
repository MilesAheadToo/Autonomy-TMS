"""TrackingEventModel physics tests (PR-3.G).

Covers:
  - parameter / context validation
  - Protocol compliance + step-before-reset guard
  - expected event count by carrier tier (premium 4hr; budget 12hr)
  - jitter distribution width (~ N(0, 30 min))
  - drop rate empirical ≈ p_drop
  - per-event location_pct progresses 0 → 1
  - rate_per_hour_override takes priority over tier
  - PLAN_PRODUCTION emits uniform deterministic schedule, no drops, no jitter
  - same-seed determinism
"""
from __future__ import annotations

import statistics

import pytest

from app.services.digital_twin.physics import (
    CarrierTrackingTier,
    PhysicsModel,
    TrackingContext,
    TrackingEvent,
    TrackingEventModel,
    TrackingEventParams,
    TrackingOutcome,
)
from azirella_data_model.digital_twin.twin_interface import TwinMode


# ── Validation ───────────────────────────────────────────────────────


def test_context_rejects_empty_carrier():
    with pytest.raises(ValueError, match="carrier_id"):
        TrackingContext(carrier_id="")


def test_context_rejects_zero_transit_hours():
    with pytest.raises(ValueError, match="transit_hours"):
        TrackingContext(carrier_id="c1", transit_hours=0.0)


def test_context_rejects_zero_rate_override():
    with pytest.raises(ValueError, match="rate_per_hour_override"):
        TrackingContext(carrier_id="c1", rate_per_hour_override=0.0)


def test_params_rejects_empty_tier_rates():
    with pytest.raises(ValueError, match="tier_rates_per_hour"):
        TrackingEventParams(tier_rates_per_hour={})


def test_params_rejects_negative_jitter():
    with pytest.raises(ValueError, match="jitter_std_minutes"):
        TrackingEventParams(jitter_std_minutes=-1.0)


def test_params_rejects_p_drop_one():
    with pytest.raises(ValueError, match="p_drop"):
        TrackingEventParams(p_drop=1.0)


def test_params_rejects_negative_p_drop():
    with pytest.raises(ValueError, match="p_drop"):
        TrackingEventParams(p_drop=-0.1)


# ── Protocol compliance ──────────────────────────────────────────────


def test_protocol_compliance():
    model = TrackingEventModel()
    assert isinstance(model, PhysicsModel)


def test_step_before_reset_raises():
    model = TrackingEventModel()
    with pytest.raises(RuntimeError, match="reset"):
        model.step(TrackingContext(carrier_id="c1"))


# ── Expected event count by tier ─────────────────────────────────────


def test_premium_carrier_expected_count_matches_4hr_cadence():
    """48 hour transit / 4 hour cadence = 12 expected events."""
    model = TrackingEventModel()
    model.reset(scenario_seed=42)
    out = model.step(
        TrackingContext(
            carrier_id="carrier:fedex",
            tracking_tier=CarrierTrackingTier.PREMIUM,
            transit_hours=48.0,
        )
    )
    assert out.expected_count == 12
    assert out.rate_per_hour == pytest.approx(0.25)


def test_budget_carrier_expected_count_matches_12hr_cadence():
    """48 hour transit / 12 hour cadence = 4 expected events."""
    model = TrackingEventModel()
    model.reset(scenario_seed=42)
    out = model.step(
        TrackingContext(
            carrier_id="carrier:budget",
            tracking_tier=CarrierTrackingTier.BUDGET,
            transit_hours=48.0,
        )
    )
    assert out.expected_count == 4
    assert out.rate_per_hour == pytest.approx(1.0 / 12.0)


def test_short_transit_can_emit_zero_events():
    """A 2-hour transit at 4-hour cadence yields zero scheduled events."""
    model = TrackingEventModel()
    model.reset(scenario_seed=42)
    out = model.step(
        TrackingContext(
            carrier_id="c1",
            tracking_tier=CarrierTrackingTier.PREMIUM,
            transit_hours=2.0,
        )
    )
    assert out.expected_count == 0
    assert out.events == ()


def test_rate_override_takes_priority_over_tier():
    """rate_per_hour_override beats tracking_tier."""
    model = TrackingEventModel()
    model.reset(scenario_seed=42)
    out = model.step(
        TrackingContext(
            carrier_id="c1",
            tracking_tier=CarrierTrackingTier.PREMIUM,  # 0.25/hr default
            rate_per_hour_override=1.0,                 # but override → 1/hr
            transit_hours=10.0,
        )
    )
    assert out.expected_count == 10
    assert out.rate_per_hour == 1.0


# ── Drop rate ────────────────────────────────────────────────────────


def test_drop_rate_empirical_matches_p_drop():
    """Over many shipments, drop rate ≈ p_drop."""
    model = TrackingEventModel()
    model.reset(scenario_seed=42)
    expected_total = 0
    received_total = 0
    for _ in range(500):
        out = model.step(
            TrackingContext(
                carrier_id="c1",
                tracking_tier=CarrierTrackingTier.PREMIUM,
                transit_hours=48.0,
            )
        )
        expected_total += out.expected_count
        received_total += len(out.events)
    drop_rate = (expected_total - received_total) / expected_total
    # default p_drop = 0.02; expect within 0.5pp at n=6000 events.
    assert 0.012 < drop_rate < 0.030, f"expected ~0.02, got {drop_rate:.4f}"


def test_zero_drop_rate_yields_full_event_count():
    model = TrackingEventModel(TrackingEventParams(p_drop=0.0))
    model.reset(scenario_seed=42)
    out = model.step(
        TrackingContext(
            carrier_id="c1",
            tracking_tier=CarrierTrackingTier.PREMIUM,
            transit_hours=48.0,
        )
    )
    assert len(out.events) == out.expected_count


# ── Jitter ───────────────────────────────────────────────────────────


def test_jitter_distribution_centred_at_zero_with_30min_std():
    """Empirical std of (emitted - expected) ≈ jitter_std_minutes / 60 hours."""
    model = TrackingEventModel(TrackingEventParams(p_drop=0.0))  # disable drops
    model.reset(scenario_seed=42)
    deltas: list[float] = []
    for _ in range(200):
        out = model.step(
            TrackingContext(
                carrier_id="c1",
                tracking_tier=CarrierTrackingTier.PREMIUM,
                transit_hours=48.0,
            )
        )
        for ev in out.events:
            deltas.append(ev.emitted_at_hours - ev.expected_at_hours)
    assert deltas
    mean = statistics.mean(deltas)
    stdev = statistics.stdev(deltas)
    # Mean ≈ 0; tolerance loose because clipping at 0 induces a slight
    # positive bias on early events.
    assert abs(mean) < 0.10, f"mean {mean:.4f}"
    # Stdev ≈ 30 min = 0.5 hr.
    assert 0.40 < stdev < 0.60, f"stdev {stdev:.4f}"


def test_zero_jitter_yields_emitted_eq_expected():
    model = TrackingEventModel(TrackingEventParams(jitter_std_minutes=0.0, p_drop=0.0))
    model.reset(scenario_seed=42)
    out = model.step(
        TrackingContext(
            carrier_id="c1",
            tracking_tier=CarrierTrackingTier.PREMIUM,
            transit_hours=48.0,
        )
    )
    for ev in out.events:
        assert ev.emitted_at_hours == ev.expected_at_hours


def test_emitted_at_never_negative():
    """Jitter clamped at 0 so events can't 'arrive before dispatch'."""
    model = TrackingEventModel(
        TrackingEventParams(jitter_std_minutes=300.0, p_drop=0.0)
    )
    model.reset(scenario_seed=42)
    for _ in range(50):
        out = model.step(
            TrackingContext(
                carrier_id="c1",
                tracking_tier=CarrierTrackingTier.PREMIUM,
                transit_hours=48.0,
            )
        )
        for ev in out.events:
            assert ev.emitted_at_hours >= 0.0


# ── Location progression ─────────────────────────────────────────────


def test_location_pct_in_unit_interval():
    model = TrackingEventModel()
    model.reset(scenario_seed=42)
    out = model.step(
        TrackingContext(
            carrier_id="c1",
            tracking_tier=CarrierTrackingTier.PREMIUM,
            transit_hours=48.0,
        )
    )
    for ev in out.events:
        assert 0.0 <= ev.location_pct <= 1.0


def test_location_pct_monotone_non_decreasing():
    """Events emitted in order along the lane (no teleporting backwards)."""
    model = TrackingEventModel(TrackingEventParams(p_drop=0.0))
    model.reset(scenario_seed=42)
    out = model.step(
        TrackingContext(
            carrier_id="c1",
            tracking_tier=CarrierTrackingTier.PREMIUM,
            transit_hours=48.0,
        )
    )
    pcts = [ev.location_pct for ev in out.events]
    assert pcts == sorted(pcts)


# ── PLAN_PRODUCTION mode ─────────────────────────────────────────────


def test_plan_production_no_drops_no_jitter():
    model = TrackingEventModel()
    model.reset(scenario_seed=42, twin_mode=TwinMode.PLAN_PRODUCTION)
    out = model.step(
        TrackingContext(
            carrier_id="c1",
            tracking_tier=CarrierTrackingTier.PREMIUM,
            transit_hours=48.0,
        )
    )
    assert len(out.events) == out.expected_count
    for ev in out.events:
        assert ev.emitted_at_hours == ev.expected_at_hours


def test_plan_production_repeated_runs_yield_identical_outputs():
    """No RNG state should affect PLAN_PRODUCTION output."""
    runs: list[tuple[float, ...]] = []
    for seed in (0, 1, 2, 999):
        m = TrackingEventModel()
        m.reset(scenario_seed=seed, twin_mode=TwinMode.PLAN_PRODUCTION)
        out = m.step(
            TrackingContext(
                carrier_id="c1",
                tracking_tier=CarrierTrackingTier.PREMIUM,
                transit_hours=48.0,
            )
        )
        runs.append(tuple(ev.emitted_at_hours for ev in out.events))
    # All runs identical.
    assert len(set(runs)) == 1


# ── Determinism ──────────────────────────────────────────────────────


def test_same_seed_same_event_stream():
    def _draw(seed: int) -> tuple[TrackingEvent, ...]:
        m = TrackingEventModel()
        m.reset(scenario_seed=seed)
        return m.step(
            TrackingContext(
                carrier_id="c1",
                tracking_tier=CarrierTrackingTier.PREMIUM,
                transit_hours=48.0,
            )
        ).events
    assert _draw(99) == _draw(99)


def test_different_seeds_diverge():
    def _draw(seed: int) -> tuple[TrackingEvent, ...]:
        m = TrackingEventModel()
        m.reset(scenario_seed=seed)
        return m.step(
            TrackingContext(
                carrier_id="c1",
                tracking_tier=CarrierTrackingTier.PREMIUM,
                transit_hours=100.0,
            )
        ).events
    assert _draw(1) != _draw(99999)


# ── Outcome shape ────────────────────────────────────────────────────


def test_drop_count_property_consistent():
    model = TrackingEventModel()
    model.reset(scenario_seed=42)
    out = model.step(
        TrackingContext(
            carrier_id="c1",
            tracking_tier=CarrierTrackingTier.PREMIUM,
            transit_hours=100.0,
        )
    )
    assert out.drop_count == out.expected_count - len(out.events)


def test_outcome_carries_resolved_rate_per_hour():
    """rate_per_hour reflects the resolved rate (override, then tier)."""
    model = TrackingEventModel()
    model.reset(scenario_seed=42)

    out_default = model.step(
        TrackingContext(
            carrier_id="c1",
            tracking_tier=CarrierTrackingTier.BUDGET,
            transit_hours=24.0,
        )
    )
    assert out_default.rate_per_hour == pytest.approx(1.0 / 12.0)

    out_override = model.step(
        TrackingContext(
            carrier_id="c1",
            tracking_tier=CarrierTrackingTier.BUDGET,
            rate_per_hour_override=2.0,
            transit_hours=24.0,
        )
    )
    assert out_override.rate_per_hour == pytest.approx(2.0)
