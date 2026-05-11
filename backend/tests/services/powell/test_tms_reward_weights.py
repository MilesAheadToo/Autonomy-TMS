"""Tests for native TMS TRM reward weights (Open Item #1 of
TMS_TRM_TRAINING_DATA_SPECIFICATION.md).

The module under test sits inside ``app.services.powell.*`` whose
package ``__init__.py`` transitively imports ``torch``. To keep these
tests runnable in a torch-less sandbox, we load the target module
directly via ``importlib.util`` with stubbed parent packages.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType

import pytest

from autonomy_tms_heuristics.library import (
    DockSchedulingState,
    EquipmentRepositionState,
    IntermodalTransferState,
    LoadBuildState,
    TMSHeuristicDecision,
)


# Stub the parent packages so the target module's ``app.services.powell.*``
# fully-qualified name resolves without triggering the real heavy
# ``__init__.py`` chain (which pulls in torch via metrics_hierarchy).
for _name in ("app", "app.services", "app.services.powell"):
    sys.modules.setdefault(_name, ModuleType(_name))

_MODULE_PATH = (
    Path(__file__).resolve().parents[3]
    / "app" / "services" / "powell" / "tms_reward_weights.py"
)
_spec = importlib.util.spec_from_file_location(
    "app.services.powell.tms_reward_weights", _MODULE_PATH,
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["app.services.powell.tms_reward_weights"] = _module
_spec.loader.exec_module(_module)

TMS_TRM_REWARD_WEIGHTS = _module.TMS_TRM_REWARD_WEIGHTS
compute_native_tms_reward = _module.compute_native_tms_reward
dock_scheduling_reward = _module.dock_scheduling_reward
equipment_reposition_reward = _module.equipment_reposition_reward
has_native_reward = _module.has_native_reward
intermodal_transfer_reward = _module.intermodal_transfer_reward
load_build_reward = _module.load_build_reward


def _decision(action: int, urgency: float = 0.5, quantity: float = 0.0) -> TMSHeuristicDecision:
    return TMSHeuristicDecision(
        trm_type="test",
        action=action,
        quantity=quantity,
        reasoning="test",
        confidence=1.0,
        urgency=urgency,
        params_used={},
    )


# ─────────────────────────────────────────────────────────────────────
# Weight-table invariants
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("trm_name", sorted(TMS_TRM_REWARD_WEIGHTS))
def test_weights_sum_to_one(trm_name: str) -> None:
    total = sum(TMS_TRM_REWARD_WEIGHTS[trm_name].values())
    assert math.isclose(total, 1.0, abs_tol=1e-6), f"{trm_name} weights sum to {total}"


def test_native_reward_keys_match_dispatch_table() -> None:
    for trm in TMS_TRM_REWARD_WEIGHTS:
        assert has_native_reward(trm)


def test_has_native_reward_false_for_unknown_trm() -> None:
    assert not has_native_reward("capacity_promise")
    assert not has_native_reward("nonexistent")


def test_compute_native_returns_none_for_non_native_trm() -> None:
    assert compute_native_tms_reward(
        "capacity_promise",
        state=None,
        decision=_decision(0),
    ) is None


# ─────────────────────────────────────────────────────────────────────
# Dock scheduling — directional tests
# ─────────────────────────────────────────────────────────────────────


def test_dock_scheduling_accept_low_util_low_risk_is_high_reward() -> None:
    state = DockSchedulingState(
        total_dock_doors=10, available_dock_doors=8,  # 20% util
        carrier_avg_dwell_minutes=60, free_time_minutes=120,  # no detention risk
        shipment_priority=3, is_live_load=False,
    )
    r = dock_scheduling_reward(state, _decision(action=0))  # ACCEPT
    assert r > 0.85


def test_dock_scheduling_accept_high_detention_risk_penalised() -> None:
    state = DockSchedulingState(
        total_dock_doors=10, available_dock_doors=8,
        carrier_avg_dwell_minutes=300, free_time_minutes=120,  # high risk
        shipment_priority=3,
    )
    r_accept = dock_scheduling_reward(state, _decision(action=0))  # ACCEPT
    r_modify = dock_scheduling_reward(state, _decision(action=4))  # MODIFY
    assert r_modify > r_accept


def test_dock_scheduling_defer_for_low_priority_at_high_util() -> None:
    state = DockSchedulingState(
        total_dock_doors=10, available_dock_doors=1,  # 90% util
        carrier_avg_dwell_minutes=60, free_time_minutes=120,
        shipment_priority=5,
    )
    r_defer = dock_scheduling_reward(state, _decision(action=2))  # DEFER
    r_accept = dock_scheduling_reward(state, _decision(action=0))
    assert r_defer > r_accept


def test_dock_scheduling_p1_defer_is_penalised() -> None:
    state = DockSchedulingState(
        total_dock_doors=10, available_dock_doors=2,
        carrier_avg_dwell_minutes=60, free_time_minutes=120,
        shipment_priority=1,  # CRITICAL
    )
    r_defer = dock_scheduling_reward(state, _decision(action=2))
    r_accept = dock_scheduling_reward(state, _decision(action=0))
    assert r_accept > r_defer


# ─────────────────────────────────────────────────────────────────────
# Intermodal transfer — directional tests
# ─────────────────────────────────────────────────────────────────────


def test_intermodal_accept_with_strong_savings_and_slack() -> None:
    state = IntermodalTransferState(
        truck_rate=1000.0, intermodal_rate=750.0,  # 25% savings
        truck_transit_days=3.0, intermodal_transit_days=5.0,
        delivery_window_days=4.0,  # plenty of slack
        intermodal_reliability_pct=0.92,
    )
    r = intermodal_transfer_reward(state, _decision(action=0))  # ACCEPT
    assert r > 0.85


def test_intermodal_reject_when_no_time() -> None:
    state = IntermodalTransferState(
        truck_rate=1000.0, intermodal_rate=750.0,
        truck_transit_days=3.0, intermodal_transit_days=6.0,
        delivery_window_days=1.0,  # blown
        intermodal_reliability_pct=0.85,
    )
    r_accept = intermodal_transfer_reward(state, _decision(action=0))
    r_reject = intermodal_transfer_reward(state, _decision(action=1))
    assert r_reject > r_accept


def test_intermodal_reject_missing_savings_is_penalised() -> None:
    state = IntermodalTransferState(
        truck_rate=1000.0, intermodal_rate=780.0,  # 22% savings
        truck_transit_days=3.0, intermodal_transit_days=4.0,
        delivery_window_days=3.0,
        intermodal_reliability_pct=0.85,
    )
    r_accept = intermodal_transfer_reward(state, _decision(action=0))
    r_reject = intermodal_transfer_reward(state, _decision(action=1))
    assert r_accept > r_reject


# ─────────────────────────────────────────────────────────────────────
# Equipment reposition — directional tests
# ─────────────────────────────────────────────────────────────────────


def test_equipment_reposition_high_roi_high_util_deficit() -> None:
    state = EquipmentRepositionState(
        source_equipment_count=20, source_demand_next_7d=5,
        target_equipment_count=2, target_demand_next_7d=10,
        reposition_miles=150.0, reposition_cost=300.0,
        cost_of_not_repositioning=900.0,  # ROI = 3.0
        network_deficit_locations=2, network_surplus_locations=3,
        fleet_utilization_pct=0.90,
    )
    r_repos = equipment_reposition_reward(state, _decision(action=9))  # REPOSITION
    r_hold = equipment_reposition_reward(state, _decision(action=10))  # HOLD
    assert r_repos > r_hold
    assert r_repos > 0.85


def test_equipment_hold_when_no_deficit_low_util() -> None:
    state = EquipmentRepositionState(
        source_equipment_count=20, source_demand_next_7d=5,
        target_equipment_count=15, target_demand_next_7d=8,
        reposition_miles=500.0, reposition_cost=900.0,
        cost_of_not_repositioning=200.0,  # ROI < 1
        network_deficit_locations=0,
        fleet_utilization_pct=0.50,
    )
    r_hold = equipment_reposition_reward(state, _decision(action=10))
    r_repos = equipment_reposition_reward(state, _decision(action=9))
    assert r_hold > r_repos


def test_equipment_long_repos_penalised_by_transit_cost() -> None:
    short = EquipmentRepositionState(
        source_equipment_count=20, source_demand_next_7d=5,
        target_equipment_count=2, target_demand_next_7d=10,
        reposition_miles=100.0, reposition_cost=300.0,
        cost_of_not_repositioning=900.0,
        network_deficit_locations=1,
        fleet_utilization_pct=0.90,
    )
    long = EquipmentRepositionState(
        source_equipment_count=20, source_demand_next_7d=5,
        target_equipment_count=2, target_demand_next_7d=10,
        reposition_miles=950.0, reposition_cost=300.0,
        cost_of_not_repositioning=900.0,
        network_deficit_locations=1,
        fleet_utilization_pct=0.90,
    )
    r_short = equipment_reposition_reward(short, _decision(action=9))
    r_long = equipment_reposition_reward(long, _decision(action=9))
    assert r_short > r_long


# ─────────────────────────────────────────────────────────────────────
# Load build — directional tests
# ─────────────────────────────────────────────────────────────────────


def test_load_build_consolidate_with_savings_in_band() -> None:
    state = LoadBuildState(
        max_weight=44000.0, max_volume=2700.0,
        total_weight=30000.0, total_volume=2000.0,  # ~70% fill
        ftl_rate=1000.0, ltl_rate_sum=1400.0,
        consolidation_savings=400.0,  # 40% of ftl_rate
        shipment_count=3, stop_count=2, max_stops=3,
    )
    r_cons = load_build_reward(state, _decision(action=7))  # CONSOLIDATE
    r_accept = load_build_reward(state, _decision(action=0))
    assert r_cons > r_accept


def test_load_build_split_when_over_capacity() -> None:
    state = LoadBuildState(
        max_weight=44000.0, max_volume=2700.0,
        total_weight=43500.0, total_volume=2680.0,  # ~98% fill
        ftl_rate=1000.0, ltl_rate_sum=1000.0, consolidation_savings=0.0,
        shipment_count=4, stop_count=1, max_stops=3,
    )
    r_split = load_build_reward(state, _decision(action=8))  # SPLIT
    r_accept = load_build_reward(state, _decision(action=0))
    assert r_split > r_accept


def test_load_build_reject_required_on_hazmat_conflict() -> None:
    state = LoadBuildState(
        max_weight=44000.0, max_volume=2700.0,
        total_weight=20000.0, total_volume=1500.0,
        ftl_rate=1000.0, ltl_rate_sum=1300.0, consolidation_savings=300.0,
        shipment_count=2, stop_count=1, max_stops=3,
        has_hazmat_conflict=True,
    )
    r_reject = load_build_reward(state, _decision(action=1))  # REJECT
    r_cons = load_build_reward(state, _decision(action=7))
    assert r_reject > r_cons


def test_load_build_defer_when_underfilled_single_shipment() -> None:
    state = LoadBuildState(
        max_weight=44000.0, max_volume=2700.0,
        total_weight=10000.0, total_volume=600.0,  # ~23% fill
        ftl_rate=1000.0, ltl_rate_sum=900.0, consolidation_savings=0.0,
        shipment_count=1, stop_count=1, max_stops=3,
    )
    r_defer = load_build_reward(state, _decision(action=2))  # DEFER
    r_accept = load_build_reward(state, _decision(action=0))
    assert r_defer > r_accept


# ─────────────────────────────────────────────────────────────────────
# Output range invariants
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "trm_name,state",
    [
        ("dock_scheduling", DockSchedulingState()),
        ("intermodal_transfer", IntermodalTransferState()),
        ("equipment_reposition", EquipmentRepositionState()),
        ("load_build", LoadBuildState()),
    ],
)
def test_reward_lies_in_expected_range(trm_name: str, state) -> None:
    for action in (0, 1, 2, 3, 4, 7, 8, 9, 10):
        r = compute_native_tms_reward(trm_name, state, _decision(action=action))
        assert r is not None
        assert 0.0 <= r <= 1.5, f"{trm_name} action={action} reward={r}"
