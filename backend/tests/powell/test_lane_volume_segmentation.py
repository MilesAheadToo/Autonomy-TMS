"""§3.36 — Lane-volume forecast segmentation tests.

Industry-norm segmentation: forecast aggregate loads at the lane level,
then split by EWMA-smoothed historical share (mode + equipment-within-FTL).
Service level is a planning constraint, not a forecast facet.

Tests cover:
- Mode mix application (FTL / LTL / PARCEL / INTERMODAL)
- Equipment mix application within FTL only
- "no_segmentation" fallback when histories are empty
- "single_mode_passthrough" when one mode dominates ≥ 95%
- Secondary tonnage / cube derivation (P50-only per industry norm)
- Segmentation rides through every action path (DEFER / ESCALATE / MODIFY / ACCEPT)
"""

from __future__ import annotations

import pytest

from autonomy_tms_heuristics.library import (
    LaneVolumeForecastState,
    compute_segmented_loads,
    compute_tms_decision,
)


# ---------------------------------------------------------------------------
# Direct compute_segmented_loads tests
# ---------------------------------------------------------------------------


def _healthy_state(**overrides) -> LaneVolumeForecastState:
    """A SMOOTH-class lane with 26w history — no escalation triggers."""
    base = dict(
        lane_id=1,
        weeks_of_history=26,
        mean_demand=100.0,
        demand_std=10.0,
        avg_demand_interval=1.0,
        squared_cv=0.04,
        nonzero_period_pct=1.0,
        trailing_mape=0.10,
        conformal_coverage_p80=0.82,
        forecast_interval_width_pct=0.30,
        proposed_forecast_p50=120.0,
        proposed_forecast_p10=110.0,
        proposed_forecast_p90=130.0,
        last_period_actual=110.0,
    )
    base.update(overrides)
    return LaneVolumeForecastState(**base)


def test_no_segmentation_when_histories_empty() -> None:
    """No mode_history / equipment_history → method='no_segmentation' and
    the aggregate is the only signal."""
    state = _healthy_state()
    out = compute_segmented_loads(state, aggregate_loads_p50=120.0)
    assert out["segmentation_method"] == "no_segmentation"
    assert out["forecast_loads_p50"] == 120.0
    assert out["mode_mix"] == {}
    assert out["mode_loads_p50"] == {"unsegmented": 120.0}
    assert out["equipment_mix"] == {}
    assert out["equipment_loads_p50"] == {}


def test_single_mode_passthrough() -> None:
    """When one mode is ≥ 95% of the lane → 'single_mode_passthrough'."""
    state = _healthy_state(mode_history={"FTL": 0.97, "LTL": 0.03})
    out = compute_segmented_loads(state, aggregate_loads_p50=120.0)
    assert out["segmentation_method"] == "single_mode_passthrough"
    assert out["mode_mix"] == {"FTL": 1.0}
    assert out["mode_loads_p50"] == {"FTL": 120.0}


def test_ewma_share_history_multi_mode() -> None:
    """Mixed mode history → 'ewma_share_history' with per-mode loads."""
    state = _healthy_state(
        mode_history={"FTL": 0.60, "LTL": 0.30, "PARCEL": 0.10},
    )
    out = compute_segmented_loads(state, aggregate_loads_p50=100.0)
    assert out["segmentation_method"] == "ewma_share_history"
    assert out["mode_mix"]["FTL"] == pytest.approx(0.60, abs=1e-3)
    assert out["mode_mix"]["LTL"] == pytest.approx(0.30, abs=1e-3)
    assert out["mode_mix"]["PARCEL"] == pytest.approx(0.10, abs=1e-3)
    # Per-mode loads sum to aggregate
    assert sum(out["mode_loads_p50"].values()) == pytest.approx(100.0)
    assert out["mode_loads_p50"]["FTL"] == pytest.approx(60.0)


def test_mix_history_renormalises_when_sum_not_one() -> None:
    """If history doesn't sum to 1, the helper renormalises."""
    state = _healthy_state(mode_history={"FTL": 6.0, "LTL": 4.0})  # sums to 10
    out = compute_segmented_loads(state, aggregate_loads_p50=50.0)
    assert out["mode_mix"]["FTL"] == pytest.approx(0.60)
    assert out["mode_mix"]["LTL"] == pytest.approx(0.40)
    assert out["mode_loads_p50"]["FTL"] == pytest.approx(30.0)
    assert out["mode_loads_p50"]["LTL"] == pytest.approx(20.0)


def test_equipment_segmentation_within_ftl_only() -> None:
    """Equipment mix is applied to the FTL share only, not the aggregate."""
    state = _healthy_state(
        mode_history={"FTL": 0.70, "LTL": 0.30},
        equipment_history={"DRY_VAN": 0.60, "REEFER": 0.30, "FLATBED": 0.10},
    )
    out = compute_segmented_loads(state, aggregate_loads_p50=100.0)
    # FTL loads = 70, equipment splits inside FTL
    assert out["mode_loads_p50"]["FTL"] == pytest.approx(70.0)
    assert out["equipment_mix"]["DRY_VAN"] == pytest.approx(0.60, abs=1e-3)
    assert out["equipment_loads_p50"]["DRY_VAN"] == pytest.approx(42.0)
    assert out["equipment_loads_p50"]["REEFER"] == pytest.approx(21.0)
    assert out["equipment_loads_p50"]["FLATBED"] == pytest.approx(7.0)
    # Equipment loads sum to FTL loads, not aggregate
    assert sum(out["equipment_loads_p50"].values()) == pytest.approx(70.0)


def test_equipment_segmentation_skipped_when_no_ftl() -> None:
    """LTL-only lane with equipment_history → equipment segmentation skipped."""
    state = _healthy_state(
        mode_history={"LTL": 1.0},
        equipment_history={"DRY_VAN": 1.0},  # set but irrelevant
    )
    out = compute_segmented_loads(state, aggregate_loads_p50=50.0)
    assert out["mode_mix"] == {"LTL": 1.0}
    # No FTL loads → no equipment segmentation
    assert out["equipment_mix"] == {}
    assert out["equipment_loads_p50"] == {}


def test_secondary_weight_uses_proposed_when_provided() -> None:
    """Caller-provided proposed_weight_kg_p50 wins over the
    mean-weight-per-load derivation."""
    state = _healthy_state(
        mean_weight_kg_per_load=18000.0,  # would derive 1,800,000 kg for 100 loads
        proposed_weight_kg_p50=1500000.0,  # caller overrides
    )
    out = compute_segmented_loads(state, aggregate_loads_p50=100.0)
    assert out["forecast_weight_kg_p50"] == 1500000.0


def test_secondary_weight_falls_back_to_per_load_mean() -> None:
    """Without proposed weight, derive from mean_weight_kg_per_load."""
    state = _healthy_state(mean_weight_kg_per_load=18000.0)
    out = compute_segmented_loads(state, aggregate_loads_p50=100.0)
    assert out["forecast_weight_kg_p50"] == 1800000.0


def test_secondary_weight_zero_when_no_signal() -> None:
    """Neither proposed nor mean → 0.0, not an error."""
    state = _healthy_state()
    out = compute_segmented_loads(state, aggregate_loads_p50=100.0)
    assert out["forecast_weight_kg_p50"] == 0.0


def test_secondary_volume_derivation() -> None:
    """Volume mirrors the weight-derivation pattern."""
    state = _healthy_state(mean_volume_m3_per_load=70.0)
    out = compute_segmented_loads(state, aggregate_loads_p50=100.0)
    assert out["forecast_volume_m3_p50"] == 7000.0


# ---------------------------------------------------------------------------
# Segmentation rides through every action path
# ---------------------------------------------------------------------------


def _segmented_state(**overrides) -> LaneVolumeForecastState:
    """A state with both segmentation axes populated."""
    return _healthy_state(
        mode_history={"FTL": 0.70, "LTL": 0.30},
        equipment_history={"DRY_VAN": 0.80, "REEFER": 0.20},
        mean_weight_kg_per_load=18000.0,
        mean_volume_m3_per_load=70.0,
        **overrides,
    )


def test_segmentation_rides_through_accept() -> None:
    state = _segmented_state()
    decision = compute_tms_decision("lane_volume_forecast", state)
    assert decision.params_used["segmentation_method"] == "ewma_share_history"
    assert "FTL" in decision.params_used["mode_loads_p50"]
    assert "DRY_VAN" in decision.params_used["equipment_loads_p50"]
    assert decision.params_used["forecast_weight_kg_p50"] > 0


def test_segmentation_rides_through_defer() -> None:
    """DEFER path (insufficient history) still carries segmentation."""
    state = _segmented_state(weeks_of_history=2)
    decision = compute_tms_decision("lane_volume_forecast", state)
    # action == DEFER (2)
    assert decision.action == 2
    assert decision.params_used["segmentation_method"] == "ewma_share_history"
    assert "FTL" in decision.params_used["mode_loads_p50"]


def test_segmentation_rides_through_escalate() -> None:
    """ESCALATE (cold-start NEW class) still carries segmentation."""
    state = _segmented_state(weeks_of_history=4)  # < 8 → NEW
    decision = compute_tms_decision("lane_volume_forecast", state)
    # action == ESCALATE (3)
    assert decision.action == 3
    assert decision.params_used["segmentation_method"] == "ewma_share_history"


def test_segmentation_rides_through_modify_signal() -> None:
    """MODIFY (signal overlay) still carries segmentation."""
    state = _segmented_state(
        signal_type="PROMO_LIFT",
        signal_magnitude=0.20,
        signal_confidence=0.80,
    )
    decision = compute_tms_decision("lane_volume_forecast", state)
    # action == MODIFY (4)
    assert decision.action == 4
    assert decision.params_used["segmentation_method"] == "ewma_share_history"
    # MODIFY adds signal-specific params alongside segmentation
    assert decision.params_used["signal_type"] == "PROMO_LIFT"
    assert "FTL" in decision.params_used["mode_loads_p50"]


# ---------------------------------------------------------------------------
# Determinism + idempotency
# ---------------------------------------------------------------------------


def test_compute_segmented_loads_is_deterministic() -> None:
    state = _segmented_state()
    a = compute_segmented_loads(state, aggregate_loads_p50=100.0)
    b = compute_segmented_loads(state, aggregate_loads_p50=100.0)
    assert a == b


def test_compute_segmented_loads_pure_no_state_mutation() -> None:
    state = _segmented_state()
    original_mode_history = dict(state.mode_history)
    compute_segmented_loads(state, aggregate_loads_p50=100.0)
    assert state.mode_history == original_mode_history
