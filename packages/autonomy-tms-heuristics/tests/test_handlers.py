"""Smoke tests for autonomy-tms-heuristics."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from azirella_heuristics_common import HeuristicWriteRefused
from autonomy_tms_heuristics import (
    BUILT_IN_DEFAULTS,
    HEURISTIC_HANDLERS,
    HEURISTIC_PRODUCER_SIGNATURE,
    HEURISTIC_WRITE_SKILLS,
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
    # Queue #4 — the inner ConformalBand also stamps HEURISTIC, not STUB.
    # The four-place warning regime carries HEURISTIC on the outer
    # payload; the band on the wire-format ConformalBand must agree.
    assert response["eta_band"]["producer_tier"] == "HEURISTIC"


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
