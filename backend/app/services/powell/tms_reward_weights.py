"""Native TMS TRM reward weights — transport-KPI signals.

Closes Open Item #1 from
[TMS_TRM_TRAINING_DATA_SPECIFICATION.md §8](../../../../docs/TMS_TRM_TRAINING_DATA_SPECIFICATION.md):
replaces the SCP-proxy reward weights (ATP_EXECUTOR / REBALANCING /
PO_CREATION / TO_EXECUTION) previously used by four TMS TRMs with
native transport KPIs — dwell, detention, throughput, intermodal
savings, fleet utilisation, fill rate, consolidation economics.

The other seven TMS TRMs continue to use the generic action-based
reward in ``backend/scripts/pretraining/generate_tms_corpus.compute_reward``;
spec §6 considers those SCP-proxy mappings appropriate.

Consumers:

- ``backend/scripts/pretraining/generate_tms_corpus.py`` — BC corpus
  reward column. Used downstream for weighted resampling and backtest
  scoring; not the BC training signal itself (that is the action
  label).
- Future RL fine-tune loop — these weights become the live training
  signal once PPO over the lane-flow twin lands. The signal shape is
  kept compatible with the action-based fallback (range roughly
  ``[0, 1.5]``).
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from autonomy_tms_heuristics.library import (
    DockSchedulingState,
    EquipmentRepositionState,
    IntermodalTransferState,
    LoadBuildState,
    TMSHeuristicDecision,
)


# ─────────────────────────────────────────────────────────────────────
# Action constants — mirror ``autonomy_tms_heuristics.library.Actions``
# inline to avoid the heuristic-library import cycle when this module
# is loaded at corpus-generation time.
# ─────────────────────────────────────────────────────────────────────

_ACCEPT = 0
_REJECT = 1
_DEFER = 2
_ESCALATE = 3
_MODIFY = 4
_CONSOLIDATE = 7
_SPLIT = 8
_REPOSITION = 9
_HOLD = 10


# ─────────────────────────────────────────────────────────────────────
# Reward-weight tables. Sum of components is 1.0 per TRM; reward output
# lies roughly in ``[0, 1.5]`` to align with the generic action-based
# reward range in ``compute_reward``.
# ─────────────────────────────────────────────────────────────────────

TMS_TRM_REWARD_WEIGHTS: Mapping[str, Mapping[str, float]] = {
    "dock_scheduling": {
        # Throughput alignment — does the action fit the current
        # dock-door utilisation envelope (50-85 % is the sweet spot).
        "throughput_alignment": 0.35,
        # Detention avoidance — does the action keep carrier dwell
        # under the free-time window (FMCSA detention study, 2018).
        "detention_avoidance": 0.35,
        # Dwell efficiency — live-load vs drop-trailer call matches
        # facility congestion.
        "dwell_efficiency": 0.20,
        # Priority alignment — P1 / P2 shipments served first.
        "priority_alignment": 0.10,
    },
    "intermodal_transfer": {
        # Mode-shift savings — captured economic gap (% off the
        # truck-only rate). AAR intermodal economic studies.
        "mode_shift_savings": 0.40,
        # On-time intermodal — does the proposed mode fit the
        # delivery window (BNSF / CSX service-level floors).
        "on_time_intermodal": 0.25,
        # Delivery-window slack — remaining margin after intermodal
        # transit; bigger is safer.
        "delivery_window_slack": 0.20,
        # Reliability bonus — scales by intermodal_reliability_pct.
        "reliability_bonus": 0.15,
    },
    "equipment_reposition": {
        # ROI — avoided spot premium / reposition cost (capped at 3×).
        "reposition_roi": 0.40,
        # Fleet utilisation gain — high when repositioning a
        # high-utilisation network with a clear deficit.
        "fleet_utilization_gain": 0.30,
        # Network balance — credit grows with deficit-location count.
        "network_balance_improvement": 0.20,
        # Empty-mile transit cost — penalty grows with reposition miles.
        "transit_cost_penalty": 0.10,
    },
    "load_build": {
        # Fill rate — binding constraint (max of weight & volume
        # utilisation). Operations research consolidation literature.
        "fill_rate": 0.40,
        # Consolidation savings — LTL → FTL economic capture.
        "consolidation_savings": 0.30,
        # Stops efficiency — multi-stop coordination penalty.
        "stops_efficiency": 0.15,
        # Priority alignment — refuse consolidation on hazmat /
        # temperature conflict; honour priority shipments otherwise.
        "priority_alignment": 0.15,
    },
}


# ─────────────────────────────────────────────────────────────────────
# Per-TRM native reward functions.
# ─────────────────────────────────────────────────────────────────────


def dock_scheduling_reward(
    state: DockSchedulingState,
    decision: TMSHeuristicDecision,
) -> float:
    """Native dock-scheduling reward.

    Signals: throughput (utilisation at decision time), detention
    avoidance (carrier dwell vs free time), live-load vs drop-trailer
    fit, priority alignment.
    """
    w = TMS_TRM_REWARD_WEIGHTS["dock_scheduling"]
    util = state.utilization_pct()
    risk = state.detention_risk_score()
    action = decision.action

    # Throughput alignment.
    if action == _ACCEPT:
        throughput = 1.0 if util < 0.85 else max(0.0, 1.0 - (util - 0.85) * 2)
    elif action == _DEFER:
        throughput = 0.8 if util > 0.85 else 0.3
    elif action == _MODIFY:
        throughput = 0.7
    else:
        throughput = 0.5

    # Detention avoidance.
    if action == _ACCEPT and risk < 0.3:
        detention = 1.0
    elif action == _MODIFY and risk > 0.5:
        detention = 1.0
    elif action == _ACCEPT and risk > 0.7:
        detention = 0.0
    else:
        detention = max(0.0, 1.0 - risk)

    # Live-load vs drop-trailer fit.
    if action == _MODIFY and state.is_live_load and util > 0.85:
        dwell = 1.0
    elif state.is_live_load and util > 0.85 and action == _ACCEPT:
        dwell = 0.4
    else:
        dwell = 0.7

    # Priority alignment.
    if state.shipment_priority <= 2 and action == _ACCEPT:
        priority = 1.0
    elif state.shipment_priority <= 2 and action == _DEFER:
        priority = 0.0
    else:
        priority = 0.8

    return (
        w["throughput_alignment"] * throughput
        + w["detention_avoidance"] * detention
        + w["dwell_efficiency"] * dwell
        + w["priority_alignment"] * priority
    )


def intermodal_transfer_reward(
    state: IntermodalTransferState,
    decision: TMSHeuristicDecision,
) -> float:
    """Native intermodal-transfer reward.

    Signals: cost-savings vs truck baseline, on-time feasibility,
    delivery-window slack, intermodal reliability.
    """
    w = TMS_TRM_REWARD_WEIGHTS["intermodal_transfer"]
    savings_pct = state.cost_savings_pct()
    has_time = state.has_time_for_intermodal()
    action = decision.action

    # Mode-shift savings — full credit at 20 %+ savings; penalty for
    # rejecting a clearly profitable shift.
    if action == _ACCEPT:
        mode_shift = max(0.0, min(1.0, savings_pct / 0.20))
    else:  # REJECT
        if savings_pct > 0.15 and has_time:
            mode_shift = 0.0
        else:
            mode_shift = 0.8

    # On-time feasibility.
    if action == _ACCEPT and has_time:
        ontime = 1.0
    elif action == _ACCEPT and not has_time:
        ontime = 0.0
    elif action == _REJECT and not has_time:
        ontime = 1.0
    else:
        ontime = 0.7

    # Delivery-window slack.
    slack_days = state.delivery_window_days - state.transit_time_penalty_days()
    if action == _ACCEPT and slack_days > 1:
        window = 1.0
    elif action == _ACCEPT and slack_days < 0:
        window = 0.0
    else:
        window = max(0.0, min(1.0, slack_days / 3.0))

    # Reliability.
    if action == _ACCEPT:
        reliability = state.intermodal_reliability_pct
    else:
        reliability = 0.7

    return (
        w["mode_shift_savings"] * mode_shift
        + w["on_time_intermodal"] * ontime
        + w["delivery_window_slack"] * window
        + w["reliability_bonus"] * reliability
    )


def equipment_reposition_reward(
    state: EquipmentRepositionState,
    decision: TMSHeuristicDecision,
) -> float:
    """Native equipment-reposition reward.

    Signals: avoided-spot-premium ROI, fleet utilisation lift, network
    deficit-balance improvement, empty-mile transit cost.
    """
    w = TMS_TRM_REWARD_WEIGHTS["equipment_reposition"]
    raw_roi = state.reposition_roi()
    if raw_roi == float("inf"):
        roi_capped = 1.0
    else:
        roi_capped = min(raw_roi, 3.0) / 3.0
    action = decision.action

    # ROI signal.
    if action == _REPOSITION:
        roi_signal = roi_capped if raw_roi >= 1.0 else 0.0
    else:  # HOLD
        roi_signal = 1.0 if raw_roi < 1.0 else max(0.0, 1.0 - roi_capped)

    # Fleet utilisation gain.
    util = state.fleet_utilization_pct
    if action == _REPOSITION and util > 0.85 and state.target_deficit() > 0:
        util_gain = 1.0
    elif action == _HOLD and util < 0.60:
        util_gain = 1.0
    else:
        util_gain = 0.5

    # Network balance.
    if action == _REPOSITION:
        balance = 1.0 if state.network_deficit_locations > 0 else 0.3
    else:
        balance = 0.8 if state.network_deficit_locations == 0 else 0.3

    # Transit cost penalty (full credit at 0 miles, zero at 1000+ miles).
    if action == _REPOSITION:
        cost_pen = max(0.0, 1.0 - state.reposition_miles / 1000.0)
    else:
        cost_pen = 1.0

    return (
        w["reposition_roi"] * roi_signal
        + w["fleet_utilization_gain"] * util_gain
        + w["network_balance_improvement"] * balance
        + w["transit_cost_penalty"] * cost_pen
    )


def load_build_reward(
    state: LoadBuildState,
    decision: TMSHeuristicDecision,
) -> float:
    """Native load-build reward.

    Signals: fill rate (binding of weight / volume), consolidation
    savings, multi-stop efficiency, priority alignment.

    Hazmat / temperature conflicts are a **hard gate**: REJECT earns
    full credit, all other actions earn near-zero. Once consolidated
    you can't unconsolidate, and shipping incompatible loads has
    regulatory (FMCSA hazmat, FDA temp) cost that swamps any
    economic gain from the other signals.
    """
    w = TMS_TRM_REWARD_WEIGHTS["load_build"]
    fill = max(state.weight_utilization(), state.volume_utilization())
    action = decision.action
    has_conflict = state.has_hazmat_conflict or state.has_temp_conflict

    # Hard gate on hazmat / temperature conflict.
    if has_conflict:
        return 1.0 if action == _REJECT else 0.1

    # Fill-rate signal — ACCEPT above 95% is a capacity violation.
    if action == _CONSOLIDATE:
        fill_sig = 1.0 if 0.5 <= fill <= 0.95 else 0.5
    elif action == _ACCEPT:
        fill_sig = 0.2 if fill > 0.95 else fill
    elif action == _SPLIT:
        fill_sig = 1.0 if fill > 0.95 else 0.3
    elif action == _DEFER:
        fill_sig = 0.8 if fill < 0.50 else 0.3
    else:  # REJECT without conflict — usually the wrong call.
        fill_sig = 0.3

    # Consolidation savings.
    if state.ftl_rate > 0:
        savings_ratio = state.consolidation_savings / state.ftl_rate
    else:
        savings_ratio = 0.0
    if action == _CONSOLIDATE:
        cons = min(1.0, max(0.0, savings_ratio * 2.0))
    else:
        cons = 0.5

    # Stops efficiency.
    if (
        action in (_ACCEPT, _CONSOLIDATE, _SPLIT)
        and state.stop_count <= state.max_stops
    ):
        stops_sig = 1.0
    elif action == _CONSOLIDATE and state.stop_count > state.max_stops:
        stops_sig = 0.0
    else:
        stops_sig = 0.7

    # Priority alignment (no conflict path).
    priority = 0.8

    return (
        w["fill_rate"] * fill_sig
        + w["consolidation_savings"] * cons
        + w["stops_efficiency"] * stops_sig
        + w["priority_alignment"] * priority
    )


# ─────────────────────────────────────────────────────────────────────
# Dispatch — corpus generator and future RL trainer entry point.
# ─────────────────────────────────────────────────────────────────────

_REWARD_FNS: Mapping[str, Callable[[Any, TMSHeuristicDecision], float]] = {
    "dock_scheduling": dock_scheduling_reward,
    "intermodal_transfer": intermodal_transfer_reward,
    "equipment_reposition": equipment_reposition_reward,
    "load_build": load_build_reward,
}


def has_native_reward(trm_name: str) -> bool:
    """Whether ``trm_name`` has a native TMS reward function."""
    return trm_name in _REWARD_FNS


def compute_native_tms_reward(
    trm_name: str,
    state: Any,
    decision: TMSHeuristicDecision,
) -> Optional[float]:
    """Compute the native TMS reward for ``trm_name`` if defined.

    Returns ``None`` when ``trm_name`` is not one of the four TMS-native
    TRMs; callers fall back to the generic action-based reward.
    """
    fn = _REWARD_FNS.get(trm_name)
    if fn is None:
        return None
    return fn(state, decision)
