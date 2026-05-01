"""Scaffolding + parametric-distribution tests.

PR-1: subpackage import, dataclass construction, simulator + adapter
shells (NotImplementedError sentinels), Phase-1 stub returns
contract-valid envelope.

PR-2: Phase-1 parametric distribution — horizon × tier bucketing,
P10/P50/P90 monotonicity, determinism, seasonal envelope modulation,
flat fallback when no envelope is registered, base-volume overrides,
unit selection.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.digital_twin import (
    CarrierProfile,
    EquipmentProfile,
    LaneFlowAction,
    LaneFlowObservation,
    LaneFlowReward,
    LaneFlowSimulator,
    LaneFlowStepAdapter,
    LaneFlowTransition,
    LanePhysicsParams,
    PARAMETRIC_STUB_PRODUCER_SIGNATURE,
    Phase1ShipmentGenerator,
    ShipmentGenerator,
    lane_series_key,
)
from azirella_data_model.stochastic import (
    FitMetadata,
    SeasonalEnvelope,
    TriangularDistribution,
)
from azirella_demand_planning_contract import Tier
from azirella_transfer_order_envelope_contract import (
    PhaseIndicator,
    TransferOrderEnvelope,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _make_observation(period: int = 0) -> LaneFlowObservation:
    return LaneFlowObservation(
        transportation_lane_id="lane:1->2",
        period=period,
        in_flight_loads=0,
        arrivals_this_period=0,
        carrier_capacity_remaining=10.0,
        equipment_available=4,
        dock_queue_depth=0,
        on_time_pct_trailing=1.0,
        cost_per_load_trailing=0.0,
    )


def _make_action() -> LaneFlowAction:
    return LaneFlowAction(
        carrier_id="carrier:acme",
        equipment_kind="dry_van_53",
        dispatch_offset_hours=0.0,
    )


def _make_reward() -> LaneFlowReward:
    return LaneFlowReward(total=0.0)


def _make_lane_params() -> LanePhysicsParams:
    """Minimal valid LanePhysicsParams for adapter / simulator tests."""
    return LanePhysicsParams(
        origin_site_id="site:1",
        destination_site_id="site:2",
        product_id="sku:A",
        transit_buckets=1,
        initial_equipment=4,
        dock_capacity_per_bucket=20,
        carriers={
            "carrier:acme": CarrierProfile(
                carrier_id="carrier:acme",
                cost_per_load=100.0,
                on_time_rate=0.95,
                capacity_per_bucket=4,
            ),
        },
        equipment_kinds={
            "dry_van_53": EquipmentProfile(
                equipment_kind="dry_van_53",
                load_capacity_units=10.0,
            ),
        },
        cost_target_per_load=100.0,
    )


def _make_simulator(*, generator: Phase1ShipmentGenerator | None = None) -> LaneFlowSimulator:
    return LaneFlowSimulator(
        generator=generator or Phase1ShipmentGenerator(),
        tenant_id=1,
        config_id=10,
        lane_params=_make_lane_params(),
    )


def _make_envelope(
    *,
    series_key: str,
    p_low: list[float],
    p_mid: list[float],
    p_high: list[float],
    period: int = 52,
) -> SeasonalEnvelope:
    """Construct a SeasonalEnvelope directly without fitting from history.

    Tests only need a usable envelope, not a faithful one.
    """
    assert len(p_low) == len(p_mid) == len(p_high) == period
    return SeasonalEnvelope(
        series_kind="other",
        series_key=series_key,
        period=period,
        period_indices=list(range(period)),
        p_low=p_low,
        p_mid=p_mid,
        p_high=p_high,
        residual_distribution=TriangularDistribution(min=-1.0, mode=0.0, max=1.0),
        fit_metadata=FitMetadata(
            n_observations=period * 2,
            n_full_periods=2.0,
            fit_method="centered_mean",
            residual_std=1.0,
        ),
    )


def _peaked_envelope(series_key: str, *, peak_week: int = 26) -> SeasonalEnvelope:
    """Envelope with a single high week and uniform spread elsewhere."""
    p_mid = [10.0] * 52
    p_mid[peak_week] = 30.0
    p_low = [v * 0.7 for v in p_mid]
    p_high = [v * 1.3 for v in p_mid]
    return _make_envelope(
        series_key=series_key,
        p_low=p_low,
        p_mid=p_mid,
        p_high=p_high,
    )


# ── Dataclass construction (PR-1) ─────────────────────────────────────


def test_observation_constructs_with_required_fields_only():
    obs = _make_observation()
    assert obs.transportation_lane_id == "lane:1->2"
    assert obs.plan_date is None and obs.as_of is None


def test_action_constructs_with_optional_reposition():
    action = LaneFlowAction(
        carrier_id="carrier:acme",
        equipment_kind="reefer_48",
        dispatch_offset_hours=2.0,
        reposition_to_site_id="site:42",
    )
    assert action.reposition_to_site_id == "site:42"
    assert action.confidence == 1.0


def test_transition_carries_done_flag_and_metadata():
    transition = LaneFlowTransition(
        observation=_make_observation(0),
        action=_make_action(),
        reward=_make_reward(),
        next_observation=_make_observation(1),
        done=False,
        metadata={"bucket": "TACTICAL"},
    )
    assert transition.metadata["bucket"] == "TACTICAL"
    assert transition.done is False


# ── Phase-1 generator: protocol + envelope-shape (PR-1, PR-2) ─────────


def test_phase1_generator_satisfies_protocol():
    generator = Phase1ShipmentGenerator()
    assert isinstance(generator, ShipmentGenerator)


def test_phase1_generator_envelope_metadata_is_valid():
    generator = Phase1ShipmentGenerator(
        candidate_lanes=[("site:1", "site:2")],
        candidate_products=["sku:A"],
    )
    envelope = generator.generate_envelope(
        tenant_id=1,
        config_id=10,
        tier=Tier.TACTICAL,
    )
    assert isinstance(envelope, TransferOrderEnvelope)
    assert envelope.phase_indicator is PhaseIndicator.PARAMETRIC_STUB
    assert envelope.produced_by == PARAMETRIC_STUB_PRODUCER_SIGNATURE
    assert envelope.upstream_supply_plan_signature is None


def test_phase1_producer_signature_format():
    parts = PARAMETRIC_STUB_PRODUCER_SIGNATURE.split(":")
    assert len(parts) == 3
    assert parts[0] == "tms"
    assert parts[2].startswith("v")


def test_phase1_generator_with_no_candidates_emits_zero_rows():
    generator = Phase1ShipmentGenerator()
    envelope = generator.generate_envelope(
        tenant_id=1,
        config_id=10,
        tier=Tier.TACTICAL,
    )
    assert envelope.rows == []


# ── Phase-1 generator: distribution shape (PR-2) ──────────────────────


def test_phase1_generator_emits_one_row_per_channel_per_bucket():
    generator = Phase1ShipmentGenerator(
        candidate_lanes=[("site:1", "site:2"), ("site:3", "site:4")],
        candidate_products=["sku:A", "sku:B", "sku:C"],
    )
    envelope = generator.generate_envelope(
        tenant_id=1,
        config_id=10,
        tier=Tier.TACTICAL,
        horizon_buckets=4,
    )
    # 2 lanes × 3 products × 4 buckets = 24 rows.
    assert len(envelope.rows) == 24


def test_phase1_generator_p10_p50_p90_monotonic():
    generator = Phase1ShipmentGenerator(
        candidate_lanes=[("site:1", "site:2")],
        candidate_products=["sku:A"],
    )
    envelope = generator.generate_envelope(
        tenant_id=1,
        config_id=10,
        tier=Tier.TACTICAL,
        horizon_buckets=8,
    )
    for row in envelope.rows:
        assert row.p10 <= row.p50 <= row.p90, (
            f"non-monotonic row: {row.p10}, {row.p50}, {row.p90}"
        )
        assert row.p10 >= 0.0


@pytest.mark.parametrize(
    "tier,expected_days",
    [(Tier.STRATEGIC, 30), (Tier.TACTICAL, 7), (Tier.EXECUTION, 1)],
)
def test_phase1_generator_tier_controls_bucket_spacing(tier, expected_days):
    anchor = date(2026, 1, 5)  # Monday
    generator = Phase1ShipmentGenerator(
        candidate_lanes=[("site:1", "site:2")],
        candidate_products=["sku:A"],
    )
    envelope = generator.generate_envelope(
        tenant_id=1,
        config_id=10,
        tier=tier,
        horizon_buckets=3,
        anchor_date=anchor,
    )
    starts = sorted({row.bucket_start for row in envelope.rows})
    assert starts == [
        anchor,
        anchor + timedelta(days=expected_days),
        anchor + timedelta(days=expected_days * 2),
    ]


def test_phase1_generator_anchor_date_controls_first_bucket():
    anchor = date(2026, 3, 15)
    generator = Phase1ShipmentGenerator(
        candidate_lanes=[("site:1", "site:2")],
        candidate_products=["sku:A"],
    )
    envelope = generator.generate_envelope(
        tenant_id=1,
        config_id=10,
        tier=Tier.TACTICAL,
        horizon_buckets=2,
        anchor_date=anchor,
    )
    assert envelope.rows[0].bucket_start == anchor


def test_phase1_generator_horizon_buckets_validation():
    generator = Phase1ShipmentGenerator(
        candidate_lanes=[("site:1", "site:2")],
        candidate_products=["sku:A"],
    )
    with pytest.raises(ValueError, match="horizon_buckets"):
        generator.generate_envelope(
            tenant_id=1,
            config_id=10,
            tier=Tier.TACTICAL,
            horizon_buckets=0,
        )


def test_phase1_generator_deterministic_with_identical_inputs():
    anchor = date(2026, 1, 5)
    kwargs = dict(
        candidate_lanes=[("site:1", "site:2")],
        candidate_products=["sku:A"],
        seed=99,
    )
    g1 = Phase1ShipmentGenerator(**kwargs)
    g2 = Phase1ShipmentGenerator(**kwargs)
    e1 = g1.generate_envelope(
        tenant_id=1, config_id=10, tier=Tier.TACTICAL,
        anchor_date=anchor, horizon_buckets=4,
    )
    e2 = g2.generate_envelope(
        tenant_id=1, config_id=10, tier=Tier.TACTICAL,
        anchor_date=anchor, horizon_buckets=4,
    )
    assert [(r.p10, r.p50, r.p90) for r in e1.rows] == \
           [(r.p10, r.p50, r.p90) for r in e2.rows]


def test_phase1_generator_different_seeds_diverge():
    anchor = date(2026, 1, 5)
    kwargs = dict(
        candidate_lanes=[("site:1", "site:2")],
        candidate_products=["sku:A"],
    )
    e1 = Phase1ShipmentGenerator(seed=1, **kwargs).generate_envelope(
        tenant_id=1, config_id=10, tier=Tier.TACTICAL,
        anchor_date=anchor, horizon_buckets=4,
    )
    e2 = Phase1ShipmentGenerator(seed=2, **kwargs).generate_envelope(
        tenant_id=1, config_id=10, tier=Tier.TACTICAL,
        anchor_date=anchor, horizon_buckets=4,
    )
    assert [(r.p10, r.p50, r.p90) for r in e1.rows] != \
           [(r.p10, r.p50, r.p90) for r in e2.rows]


# ── Phase-1 generator: seasonal envelope adoption (PR-2) ──────────────


def test_lane_series_key_format():
    assert lane_series_key("site:1", "site:2") == "lane:site:1->site:2"


def test_phase1_generator_no_envelope_uses_flat_spread():
    generator = Phase1ShipmentGenerator(
        candidate_lanes=[("site:1", "site:2")],
        candidate_products=["sku:A"],
        base_volumes={("site:1", "site:2", "sku:A"): 100.0},
        envelope_spread=(0.5, 1.5),
        seed=0,
    )
    envelope = generator.generate_envelope(
        tenant_id=1, config_id=10, tier=Tier.TACTICAL,
        anchor_date=date(2026, 6, 1), horizon_buckets=1,
    )
    row = envelope.rows[0]
    # base * jitter is constant for this single channel; ratio of
    # p10/p50 and p90/p50 must match envelope_spread.
    assert row.p50 > 0
    assert row.p10 == pytest.approx(row.p50 * 0.5, rel=1e-6)
    assert row.p90 == pytest.approx(row.p50 * 1.5, rel=1e-6)


def test_phase1_generator_seasonal_envelope_modulates_p50_across_buckets():
    lane_key = lane_series_key("site:1", "site:2")
    envelope_obj = _peaked_envelope(lane_key, peak_week=26)
    generator = Phase1ShipmentGenerator(
        candidate_lanes=[("site:1", "site:2")],
        candidate_products=["sku:A"],
        base_volumes={("site:1", "site:2", "sku:A"): 100.0},
        seasonal_envelopes={lane_key: envelope_obj},
        seed=0,
    )
    # Anchor on iso week 1; horizon = 52 buckets at TACTICAL — covers a year.
    envelope = generator.generate_envelope(
        tenant_id=1, config_id=10, tier=Tier.TACTICAL,
        anchor_date=date(2026, 1, 5), horizon_buckets=52,
    )
    # The peak bucket's P50 is materially higher than the trough.
    p50s = [row.p50 for row in envelope.rows]
    assert max(p50s) > 2.0 * min(p50s)


def test_phase1_generator_envelope_spread_present_in_rows():
    """Envelope's per-bucket spread propagates to the row's p10/p90 ratio."""
    lane_key = lane_series_key("site:1", "site:2")
    # Wide envelope: p_low = 0.5 * p_mid, p_high = 1.5 * p_mid.
    p_mid = [10.0] * 52
    envelope_obj = _make_envelope(
        series_key=lane_key,
        p_low=[v * 0.5 for v in p_mid],
        p_mid=p_mid,
        p_high=[v * 1.5 for v in p_mid],
    )
    generator = Phase1ShipmentGenerator(
        candidate_lanes=[("site:1", "site:2")],
        candidate_products=["sku:A"],
        base_volumes={("site:1", "site:2", "sku:A"): 100.0},
        seasonal_envelopes={lane_key: envelope_obj},
        seed=0,
    )
    envelope = generator.generate_envelope(
        tenant_id=1, config_id=10, tier=Tier.TACTICAL,
        anchor_date=date(2026, 1, 5), horizon_buckets=4,
    )
    row = envelope.rows[0]
    # p_mid is flat → mean(p_mid) = p_mid[i] → normalised mid = 1.0;
    # p10 ratio should be 0.5 of p50, p90 should be 1.5 of p50.
    assert row.p10 == pytest.approx(row.p50 * 0.5, rel=1e-6)
    assert row.p90 == pytest.approx(row.p50 * 1.5, rel=1e-6)


# ── Phase-1 generator: base volumes + unit selection (PR-2) ───────────


def test_phase1_generator_base_volumes_override_default():
    generator = Phase1ShipmentGenerator(
        candidate_lanes=[("site:1", "site:2"), ("site:3", "site:4")],
        candidate_products=["sku:A"],
        base_volumes={("site:1", "site:2", "sku:A"): 1000.0},
        default_base_volume=10.0,
        seed=0,
    )
    envelope = generator.generate_envelope(
        tenant_id=1, config_id=10, tier=Tier.TACTICAL,
        anchor_date=date(2026, 1, 5), horizon_buckets=1,
    )
    by_lane = {(r.origin_site_id, r.destination_site_id): r.p50 for r in envelope.rows}
    # Configured lane is ~100x the default lane (jitter is bounded).
    assert by_lane[("site:1", "site:2")] > 50.0 * by_lane[("site:3", "site:4")]


def test_phase1_generator_unit_overrides_take_priority():
    generator = Phase1ShipmentGenerator(
        candidate_lanes=[("site:1", "site:2")],
        candidate_products=["sku:A", "sku:B"],
        candidate_units=["each"],
        product_unit_overrides={"sku:B": "case"},
    )
    envelope = generator.generate_envelope(
        tenant_id=1, config_id=10, tier=Tier.TACTICAL,
        anchor_date=date(2026, 1, 5), horizon_buckets=1,
    )
    units_by_product = {r.product_id: r.unit for r in envelope.rows}
    assert units_by_product["sku:A"] == "each"
    assert units_by_product["sku:B"] == "case"


def test_phase1_generator_envelope_spread_validation():
    with pytest.raises(ValueError, match="envelope_spread"):
        Phase1ShipmentGenerator(envelope_spread=(1.5, 0.5))


# ── Step adapter (PR-1 contracts; physics tests live in test_lane_flow_simulator.py) ──


def test_step_adapter_rejects_step_before_reset():
    adapter = LaneFlowStepAdapter(simulator=_make_simulator())
    with pytest.raises(RuntimeError, match="reset"):
        adapter.step(_make_action())


def test_step_adapter_starts_with_empty_trajectory():
    adapter = LaneFlowStepAdapter(simulator=_make_simulator())
    assert adapter.trajectory == []
