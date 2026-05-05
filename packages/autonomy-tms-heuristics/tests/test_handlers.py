"""Smoke tests for autonomy-tms-heuristics."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from azirella_heuristics_common import HeuristicWriteRefused
from autonomy_tms_heuristics import (
    Actions,
    BUILT_IN_DEFAULTS,
    HEURISTIC_HANDLERS,
    HEURISTIC_PRODUCER_SIGNATURE,
    HEURISTIC_WRITE_SKILLS,
    LoadBuildState,
    TMSHeuristicDecision,
    compute_tms_decision,
    estimate_eta,
    estimate_lane_eta,
    evaluate_consolidation,
    recommend_carrier,
    refuse_write,
    get_handler_bundle,
)


REQUIRED_MARKERS = {
    "producer_tier",
    "producer_signature",
    "heuristic_warning",
    "heuristic_plane",
}


def _assert_markers(payload: dict, expected_skill_id: str) -> None:
    assert REQUIRED_MARKERS.issubset(payload.keys())
    assert payload["producer_tier"] == "HEURISTIC"
    assert payload["producer_signature"].startswith(HEURISTIC_PRODUCER_SIGNATURE)
    assert "AZIRELLA-STUB-WARNING" in payload["heuristic_warning"]
    assert expected_skill_id in payload["heuristic_warning"]
    assert payload["heuristic_plane"] == "autonomy-tms-heuristics"


def test_estimate_lane_eta_with_explicit_coords() -> None:
    response = estimate_lane_eta(
        tenant_id=1,
        plane_config=BUILT_IN_DEFAULTS,
        inp={
            "from_site_id": "S001",
            "to_site_id": "S002",
            "departure_at": datetime(2026, 5, 4, 8, 0, tzinfo=timezone.utc),
            "from_lat": 41.8781, "from_lon": -87.6298,
            "to_lat": 32.7767, "to_lon": -96.7970,
        },
    )
    _assert_markers(response, "transport.lane.estimate_eta")
    assert response["p50_days"] >= 1.0
    assert response["p10_days"] < response["p50_days"] < response["p90_days"]


def test_estimate_lane_eta_missing_required_raises() -> None:
    with pytest.raises(ValueError, match="missing required input"):
        estimate_lane_eta(
            tenant_id=1, plane_config=BUILT_IN_DEFAULTS,
            inp={"from_site_id": "S001"},
        )


def test_estimate_lane_eta_no_coords_uses_default() -> None:
    response = estimate_lane_eta(
        tenant_id=1, plane_config=BUILT_IN_DEFAULTS,
        inp={
            "from_site_id": "S_unknown",
            "to_site_id": "S_also_unknown",
            "departure_at": "2026-05-04T08:00:00Z",
        },
    )
    assert response["p50_days"] == BUILT_IN_DEFAULTS["default_transit_days_p50"]


def test_evaluate_consolidation_always_ship_as_is() -> None:
    response = evaluate_consolidation(
        tenant_id=1, plane_config=BUILT_IN_DEFAULTS,
        inp={"config_id": 7, "shipment_ids": ["A", "B", "C"]},
    )
    _assert_markers(response, "transport.load.evaluate_consolidation")
    assert response["recommend_consolidation"] is False


def test_recommend_carrier_uses_tenant_default() -> None:
    response = recommend_carrier(
        tenant_id=1,
        plane_config={**BUILT_IN_DEFAULTS, "default_carrier_id": "carrier:fleet-A"},
        inp={"config_id": 7, "load_id": "L-99"},
    )
    _assert_markers(response, "transport.carrier.recommend")
    assert response["recommendations"][0]["carrier_id"] == "carrier:fleet-A"


def test_recommend_carrier_no_default_returns_unknown() -> None:
    response = recommend_carrier(
        tenant_id=1, plane_config=BUILT_IN_DEFAULTS,
        inp={"config_id": 7, "load_id": "L-99"},
    )
    assert response["recommendations"][0]["carrier_id"] == "unknown"


@pytest.mark.parametrize("skill_id", sorted(HEURISTIC_WRITE_SKILLS))
def test_refuse_write_raises(skill_id: str) -> None:
    with pytest.raises(HeuristicWriteRefused) as exc_info:
        refuse_write(skill_id)
    assert exc_info.value.plane == "tms"


def test_handler_registry_keys() -> None:
    assert set(HEURISTIC_HANDLERS) == {
        "transport.lane.estimate_eta",
        "transport.load.evaluate_consolidation",
        "transport.carrier.recommend",
    }


def test_get_handler_bundle_returns_registered_shape() -> None:
    """The entry-point factory must return a HeuristicPlaneBundle the
    router can consume. Skipped if azirella-router isn't installed."""
    pytest.importorskip("azirella_router")
    bundle = get_handler_bundle()
    assert bundle.plane == "tms"
    assert bundle.handlers is HEURISTIC_HANDLERS
    assert bundle.write_skills == HEURISTIC_WRITE_SKILLS
    assert bundle.refuse_write is refuse_write


# ─────────────────────────────────────────────────────────────────────────
# PR-LOCK pattern coverage — cross-plane handlers route through the
# canonical library dispatch, not duplicate math.
# ─────────────────────────────────────────────────────────────────────────


def test_library_state_dataclasses_exposed() -> None:
    """The package re-exports library entry points so internal TMS code
    can import from autonomy_tms_heuristics.library directly without
    the cross-plane handler surface."""
    # State dataclass + dispatcher both reachable at package top level.
    assert callable(compute_tms_decision)
    assert callable(estimate_eta)
    state = LoadBuildState(shipment_count=0)
    assert isinstance(state, LoadBuildState)


def test_evaluate_consolidation_dispatches_through_compute_tms_decision() -> None:
    """The cross-plane handler must route through ``compute_tms_decision``
    rather than re-implementing the math. This locks in the PR-LOCK
    pattern: training labels, runtime fallback, and cross-plane HEURISTIC
    dispatch all share one source."""
    seen_calls = []

    real_compute = compute_tms_decision

    def _spy(trm_type, state):
        seen_calls.append((trm_type, type(state).__name__))
        return real_compute(trm_type, state)

    with patch(
        "autonomy_tms_heuristics.handlers.compute_tms_decision",
        side_effect=_spy,
    ):
        response = evaluate_consolidation(
            tenant_id=1, plane_config=BUILT_IN_DEFAULTS,
            inp={"config_id": 7, "shipment_ids": ["A", "B", "C"]},
        )
    assert seen_calls == [("load_build", "LoadBuildState")]
    # And the result is still the always-no contract callers depend on.
    assert response["recommend_consolidation"] is False


def test_recommend_carrier_dispatches_when_default_set() -> None:
    """When a tenant default carrier exists, the handler dispatches
    ``compute_tms_decision('freight_procurement', ...)``. When none
    exists, no dispatch (no candidate to evaluate)."""
    real_compute = compute_tms_decision
    seen_calls = []

    def _spy(trm_type, state):
        seen_calls.append((trm_type, type(state).__name__))
        return real_compute(trm_type, state)

    with patch(
        "autonomy_tms_heuristics.handlers.compute_tms_decision",
        side_effect=_spy,
    ):
        # Default set → dispatched
        recommend_carrier(
            tenant_id=1,
            plane_config={**BUILT_IN_DEFAULTS, "default_carrier_id": "fleet-A"},
            inp={"config_id": 7, "load_id": "L-99"},
        )
        assert seen_calls == [("freight_procurement", "FreightProcurementState")]

        # No default → not dispatched
        seen_calls.clear()
        recommend_carrier(
            tenant_id=1, plane_config=BUILT_IN_DEFAULTS,
            inp={"config_id": 7, "load_id": "L-99"},
        )
        assert seen_calls == []


def test_estimate_lane_eta_dispatches_through_library_estimate_eta() -> None:
    """ETA calls the library's ``estimate_eta`` math primitive rather than
    a re-implementation in handlers."""
    real_estimate = estimate_eta
    seen_calls = []

    def _spy(*args, **kwargs):
        seen_calls.append(("estimate_eta", kwargs.get("from_site_id"), kwargs.get("to_site_id")))
        return real_estimate(*args, **kwargs)

    with patch(
        "autonomy_tms_heuristics.handlers.estimate_eta",
        side_effect=_spy,
    ):
        estimate_lane_eta(
            tenant_id=1, plane_config=BUILT_IN_DEFAULTS,
            inp={
                "from_site_id": "S001",
                "to_site_id": "S002",
                "departure_at": "2026-05-04T08:00:00Z",
                "from_lat": 41.8781, "from_lon": -87.6298,
                "to_lat": 32.7767, "to_lon": -96.7970,
            },
        )
    assert seen_calls == [("estimate_eta", "S001", "S002")]


def test_eta_module_lives_at_package_root() -> None:
    """Per §3.52 step 3, ``autonomy_tms_heuristics.eta`` stays at the
    package root rather than moving into ``library/`` — lane-ETA-pre-
    dispatch has no TMS internal TRM consumer to deduplicate against
    today. The module must be importable; ``estimate_eta`` is the
    canonical entry point."""
    import autonomy_tms_heuristics.eta as eta_module

    assert callable(eta_module.estimate_eta)
    assert hasattr(eta_module, "ETAResult")
    assert hasattr(eta_module, "BUILT_IN_DEFAULTS")
