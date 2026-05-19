"""
TMS Heuristic Dispatch — Transportation Decision Rules

Deterministic fallback logic for all 11 TMS TRMs when neural network
models are unavailable. Encodes industry best practices for:
- Carrier waterfall tendering
- Load consolidation
- Dock door optimization
- Exception triage
- Equipment rebalancing

Unlike the SC library which dispatches by ERP source (SAP/D365/Odoo),
TMS heuristics are universal — transportation operations follow standard
industry practices regardless of source system.
"""

import logging
import math
from typing import Any, Dict, List

from .base import (
    TMSHeuristicDecision,
    CapacityPromiseState,
    ShipmentTrackingState,
    LoadVolumeSensingState,
    CapacityBufferState,
    ExceptionManagementState,
    FreightProcurementState,
    BrokerRoutingState,
    DockSchedulingState,
    LoadBuildState,
    IntermodalTransferState,
    EquipmentRepositionState,
    LaneVolumeForecastState,
)

logger = logging.getLogger(__name__)


# ── Action Constants ────────────────────────────────────────────────────
# Discrete action indices used across TRMs

class Actions:
    ACCEPT = 0
    REJECT = 1
    DEFER = 2
    ESCALATE = 3
    MODIFY = 4
    RETENDER = 5
    REROUTE = 6
    CONSOLIDATE = 7
    SPLIT = 8
    REPOSITION = 9
    HOLD = 10


# ============================================================================
# Main Dispatch
# ============================================================================

def compute_tms_decision(trm_type: str, state: Any) -> TMSHeuristicDecision:
    """
    Compute a deterministic decision for any TMS TRM.

    Args:
        trm_type: TRM canonical name (e.g., "capacity_promise")
        state: Corresponding state dataclass

    Returns:
        TMSHeuristicDecision with action, reasoning, and parameters
    """
    # §3.79 Substep 3 (2026-05-19): load_volume_sensing +
    # lane_volume_forecast moved to DP-Ship. The dispatch entries are
    # retained as stubs that raise so callers stuck on an old import
    # path get a clear error pointing at the new home. Stage E.2
    # follow-up removes them entirely after one release.
    dispatch_map = {
        "capacity_promise": _compute_capacity_promise,
        "shipment_tracking": _compute_shipment_tracking,
        "capacity_buffer": _compute_capacity_buffer,
        "exception_management": _compute_exception_management,
        "freight_procurement": _compute_freight_procurement,
        "broker_routing": _compute_broker_routing,
        "dock_scheduling": _compute_dock_scheduling,
        "load_build": _compute_load_build,
        "intermodal_transfer": _compute_intermodal_transfer,
        "equipment_reposition": _compute_equipment_reposition,
    }
    if trm_type in ("load_volume_sensing", "lane_volume_forecast"):
        raise RuntimeError(
            f"compute_tms_decision: trm_type={trm_type!r} moved to DP-Ship "
            "per §3.79 Substep 3. Use `compute_shipment_decision` from "
            "`apps/dp-ship/backend/app/heuristics/shipment_volume` instead."
        )

    func = dispatch_map.get(trm_type)
    if not func:
        logger.warning(f"No heuristic for TRM type '{trm_type}'")
        return TMSHeuristicDecision(
            trm_type=trm_type, action=Actions.HOLD,
            reasoning=f"No heuristic available for {trm_type}",
        )

    return func(state)


# ============================================================================
# 1. Capacity Promise
# ============================================================================

def _compute_capacity_promise(state: CapacityPromiseState) -> TMSHeuristicDecision:
    """
    Evaluate whether to promise capacity for a shipment request.

    Industry algorithm: lane-level composite scoring (Oracle OTM / SAP TM
    pattern). The promise decision considers capacity, carrier quality,
    market tightness, and allocation compliance — not just availability.

    Composite promise confidence score (0–1):
        0.35 × capacity_factor       (available/requested, capped at 1)
      + 0.25 × service_factor        (primary carrier OTP)
      + 0.20 × acceptance_factor     (lane trailing acceptance rate)
      + 0.10 × compliance_factor     (allocation compliance %)
      + 0.10 × market_factor         (1 - market_tightness)

    P1–P2 priority overrides: always promise (consume buffer).
    Score > 0.6: ACCEPT. Score 0.35–0.6: DEFER. Score < 0.35: REJECT.
    """
    available = state.available_capacity()

    # Priority override — critical freight always gets capacity
    if state.priority <= 2:
        if available >= state.requested_loads or state.buffer_capacity > 0:
            return TMSHeuristicDecision(
                trm_type="capacity_promise", action=Actions.ACCEPT,
                quantity=state.requested_loads,
                reasoning=f"High priority (P{state.priority}): committed from {'buffer' if available < state.requested_loads else 'available capacity'}",
                urgency=0.8,
                params_used={"override": "priority", "priority": state.priority},
            )

    # Composite promise confidence score
    cap_factor = min(1.0, available / max(1, state.requested_loads))
    svc_factor = state.primary_carrier_otp
    acc_factor = state.lane_acceptance_rate
    comp_factor = min(1.0, state.allocation_compliance_pct)
    mkt_factor = 1.0 - state.market_tightness

    score = (0.35 * cap_factor
             + 0.25 * svc_factor
             + 0.20 * acc_factor
             + 0.10 * comp_factor
             + 0.10 * mkt_factor)

    scoring_detail = {
        "composite_score": round(score, 3),
        "capacity_factor": round(cap_factor, 3),
        "service_factor": round(svc_factor, 3),
        "acceptance_factor": round(acc_factor, 3),
        "compliance_factor": round(comp_factor, 3),
        "market_factor": round(mkt_factor, 3),
    }

    if score >= 0.60:
        # Sufficient confidence to promise
        # Check spot premium if primary capacity is insufficient
        if available < state.requested_loads and state.spot_rate_premium_pct >= 0.20:
            return TMSHeuristicDecision(
                trm_type="capacity_promise", action=Actions.DEFER,
                quantity=state.requested_loads,
                reasoning=f"Score {score:.2f} but spot premium {state.spot_rate_premium_pct*100:.0f}% exceeds threshold; deferring",
                urgency=0.5,
                params_used=scoring_detail,
            )
        return TMSHeuristicDecision(
            trm_type="capacity_promise", action=Actions.ACCEPT,
            quantity=state.requested_loads,
            reasoning=f"Promise confidence {score:.2f} (cap={cap_factor:.2f} svc={svc_factor:.2f} acc={acc_factor:.2f} mkt={mkt_factor:.2f})",
            urgency=0.3,
            params_used=scoring_detail,
        )

    if score >= 0.35:
        # Marginal — defer to procurement for carrier sourcing
        return TMSHeuristicDecision(
            trm_type="capacity_promise", action=Actions.DEFER,
            quantity=state.requested_loads,
            reasoning=f"Marginal confidence {score:.2f}; deferring to procurement (acc={acc_factor:.2f} mkt={mkt_factor:.2f})",
            urgency=0.6,
            params_used=scoring_detail,
        )

    # Low confidence — reject
    return TMSHeuristicDecision(
        trm_type="capacity_promise", action=Actions.REJECT,
        quantity=0,
        reasoning=f"Low confidence {score:.2f}: capacity={cap_factor:.2f} acceptance={acc_factor:.2f} market={mkt_factor:.2f}",
        urgency=0.9,
        params_used=scoring_detail,
    )


# ============================================================================
# 2. Shipment Tracking
# ============================================================================

def _compute_shipment_tracking(state: ShipmentTrackingState) -> TMSHeuristicDecision:
    """
    Shipment tracking with mode-aware thresholds + conformal intervals +
    carrier-reliability-weighted ETA assessment.

    Industry algorithm (project44 / FourKites pattern):
    - Mode-aware silence thresholds (truck 4h, LTL 8h, ocean 24h, air 2h)
    - Conformal prediction intervals when available (P10/P90 bounds)
    - Carrier reliability weighting on ETA confidence
    - Temperature excursion always escalates (food safety)
    """
    # Mode-aware tracking-lost thresholds
    silence_thresholds = {
        "FTL": 4.0, "LTL": 8.0, "PARCEL": 12.0,
        "FCL": 24.0, "LCL": 24.0, "BULK_OCEAN": 48.0,
        "AIR_STD": 2.0, "AIR_EXPRESS": 1.0,
        "RAIL_INTERMODAL": 12.0, "RAIL_CARLOAD": 24.0,
    }
    silence_limit = silence_thresholds.get(state.transport_mode, 4.0)

    tracking_detail = {
        "mode": state.transport_mode,
        "silence_threshold_hrs": silence_limit,
        "carrier_reliability": state.carrier_reliability_score,
        "pct_complete": state.pct_complete,
    }

    # Tracking lost — mode-aware
    if (state.last_update_hours_ago > silence_limit
            and state.shipment_status == "IN_TRANSIT"):
        return TMSHeuristicDecision(
            trm_type="shipment_tracking", action=Actions.ESCALATE,
            reasoning=f"Tracking lost: no update for {state.last_update_hours_ago:.1f}h (threshold {silence_limit:.0f}h for {state.transport_mode})",
            urgency=0.7,
            params_used=tracking_detail,
        )

    # Temperature excursion — immediate, mode-independent
    if (state.is_temperature_sensitive
            and state.current_temp is not None
            and state.temp_min is not None
            and state.temp_max is not None):
        if state.current_temp < state.temp_min or state.current_temp > state.temp_max:
            return TMSHeuristicDecision(
                trm_type="shipment_tracking", action=Actions.ESCALATE,
                quantity=state.current_temp,
                reasoning=f"Temperature {state.current_temp}°F outside [{state.temp_min}, {state.temp_max}]",
                urgency=0.95,
                params_used=tracking_detail,
            )

    # Late delivery — with conformal interval awareness
    if state.is_late():
        hours_late = 0.0
        if state.planned_delivery and state.current_eta:
            hours_late = (state.current_eta - state.planned_delivery).total_seconds() / 3600
        # If conformal P90 is still within window, lower urgency
        p90_late = False
        if state.eta_p90 and state.planned_delivery:
            p90_late = state.eta_p90 > state.planned_delivery
        confidence_note = ""
        if state.eta_p90 and not p90_late:
            confidence_note = " (P90 still within window — may self-correct)"
            urgency = min(0.7, 0.4 + hours_late / 24)
        else:
            urgency = min(1.0, 0.5 + hours_late / 24)
        # Discount urgency by carrier reliability (reliable carrier → more likely self-correction)
        urgency *= (1.1 - state.carrier_reliability_score * 0.2)
        return TMSHeuristicDecision(
            trm_type="shipment_tracking", action=Actions.ESCALATE,
            quantity=hours_late,
            reasoning=f"ETA {hours_late:.1f}h late{confidence_note}",
            urgency=min(1.0, urgency),
            params_used={**tracking_detail, "hours_late": round(hours_late, 2),
                         "p90_also_late": p90_late},
        )

    # At-risk — behind expected progress curve
    hours_remaining = state.hours_to_delivery()
    if hours_remaining and hours_remaining > 0 and state.pct_complete < 0.5:
        expected_pct = 1.0 - (hours_remaining / max(1, hours_remaining / max(0.01, 1.0 - state.pct_complete)))
        if state.pct_complete < expected_pct - 0.10:
            return TMSHeuristicDecision(
                trm_type="shipment_tracking", action=Actions.MODIFY,
                reasoning=f"At-risk: {state.pct_complete*100:.0f}% complete, expected {expected_pct*100:.0f}%, {hours_remaining:.0f}h remaining",
                urgency=0.5,
                params_used=tracking_detail,
            )

    return TMSHeuristicDecision(
        trm_type="shipment_tracking", action=Actions.ACCEPT,
        reasoning=f"Nominal ({state.pct_complete*100:.0f}% complete, last update {state.last_update_hours_ago:.1f}h ago)",
        urgency=0.1,
        params_used=tracking_detail,
    )


# ============================================================================
# 3. Load Volume Sensing (TMS-native; distinct from DP's product DemandSensing)
# ============================================================================

def _compute_load_volume_sensing(state: LoadVolumeSensingState) -> TMSHeuristicDecision:
    """
    Load-volume sensing with Trigg's tracking signal + order pipeline velocity +
    asymmetric loss (under-forecasting costs more than over-forecasting).

    Industry algorithm (E2open / Terra Technology pattern):
    1. Tracking signal = cumulative_error / MAD. If |TS| > 4 → structural bias
    2. Order pipeline velocity (24h bookings vs. prior period) — strongest signal
    3. Asymmetric correction: under-forecast adjusted 60%, over-forecast 40%
    4. Signal-type-specific magnitude applied when available
    5. Peak season + high MAPE → precautionary buffer
    """
    bias = state.forecast_bias()

    # Trigg's tracking signal — cumulative bias detection
    tracking_signal = 0.0
    if state.cumulative_mad > 0:
        tracking_signal = state.cumulative_forecast_error / state.cumulative_mad

    sensing_detail = {
        "bias": round(bias, 3),
        "tracking_signal": round(tracking_signal, 2),
        "mape": state.forecast_mape,
        "pipeline_velocity": state.order_pipeline_loads_24h,
        "is_peak": state.is_peak_season,
    }

    # Order pipeline velocity — strongest signal (E2open research)
    if state.order_pipeline_loads_24h > 0 and state.order_pipeline_loads_prior_24h > 0:
        pipeline_change = ((state.order_pipeline_loads_24h - state.order_pipeline_loads_prior_24h)
                           / state.order_pipeline_loads_prior_24h)
        if abs(pipeline_change) > 0.15:
            # Asymmetric: under-forecast (positive pipeline surge) gets larger correction
            correction_factor = 0.60 if pipeline_change > 0 else 0.40
            adjustment = pipeline_change * state.forecast_loads * correction_factor
            return TMSHeuristicDecision(
                trm_type="load_volume_sensing", action=Actions.MODIFY,
                quantity=adjustment,
                reasoning=f"Order pipeline {pipeline_change*100:+.0f}% vs prior (asymmetric correction {correction_factor:.0%})",
                urgency=0.6 + abs(pipeline_change) * 0.3,
                params_used={**sensing_detail, "pipeline_change": round(pipeline_change, 3),
                             "correction_factor": correction_factor},
            )

    # Structural bias via tracking signal (Trigg's method)
    if abs(tracking_signal) > 4.0:
        direction = "down" if tracking_signal > 0 else "up"
        # Asymmetric: larger correction for under-forecasting (TS < -4)
        correction = 0.60 if tracking_signal < 0 else 0.40
        adjustment = -bias * state.forecast_loads * correction
        return TMSHeuristicDecision(
            trm_type="load_volume_sensing", action=Actions.MODIFY,
            quantity=adjustment,
            reasoning=f"Tracking signal {tracking_signal:.1f} (threshold ±4): structural bias, adjusting {direction} {correction:.0%}",
            urgency=0.7,
            params_used=sensing_detail,
        )

    # Signal-type-specific adjustment
    if state.signal_type and state.signal_magnitude > 0 and state.signal_confidence > 0.5:
        adjustment = state.signal_magnitude * state.forecast_loads * state.signal_confidence * 0.5
        return TMSHeuristicDecision(
            trm_type="load_volume_sensing", action=Actions.MODIFY,
            quantity=adjustment,
            reasoning=f"Signal {state.signal_type}: magnitude {state.signal_magnitude:.2f} × confidence {state.signal_confidence:.2f}",
            urgency=0.4 + state.signal_confidence * 0.2,
            params_used=sensing_detail,
        )

    # Simple bias correction with asymmetric loss
    if abs(bias) > 0.15:
        direction = "down" if bias > 0 else "up"
        correction = 0.60 if bias < 0 else 0.40  # Larger correction for under-forecast
        adjustment = -bias * state.forecast_loads * correction
        return TMSHeuristicDecision(
            trm_type="load_volume_sensing", action=Actions.MODIFY,
            quantity=adjustment,
            reasoning=f"Bias {bias*100:.0f}%: adjusting {direction} (asymmetric {correction:.0%})",
            urgency=0.5 + abs(bias),
            params_used=sensing_detail,
        )

    # WoW volume shift
    if abs(state.week_over_week_change_pct) > 0.20:
        return TMSHeuristicDecision(
            trm_type="load_volume_sensing", action=Actions.MODIFY,
            quantity=state.week_over_week_change_pct * state.forecast_loads * 0.3,
            reasoning=f"WoW change {state.week_over_week_change_pct*100:+.0f}%",
            urgency=0.4,
            params_used=sensing_detail,
        )

    # Peak season precautionary buffer
    if state.is_peak_season and state.forecast_mape > 0.20:
        return TMSHeuristicDecision(
            trm_type="load_volume_sensing", action=Actions.MODIFY,
            quantity=state.forecast_loads * 0.10,
            reasoning=f"Peak season with {state.forecast_mape*100:.0f}% MAPE; +10% buffer",
            urgency=0.4,
            params_used=sensing_detail,
        )

    return TMSHeuristicDecision(
        trm_type="load_volume_sensing", action=Actions.ACCEPT,
        reasoning=f"Forecast OK (bias {bias*100:.0f}%, TS {tracking_signal:.1f}, MAPE {state.forecast_mape*100:.0f}%)",
        urgency=0.1,
        params_used=sensing_detail,
    )


# ============================================================================
# 4. Capacity Buffer
# ============================================================================

def _compute_capacity_buffer(state: CapacityBufferState) -> TMSHeuristicDecision:
    """
    Capacity buffer sizing with conformal P90-P50 integration.

    Industry algorithm (newsvendor-inspired):
    1. If conformal forecast intervals available (P10/P90), use P90-P50
       as the volatility signal — this is distribution-free and handles
       heavy-tailed freight demand better than Gaussian CV.
    2. Tender reject rate as primary capacity-side signal.
    3. Market tightness via recent capacity misses.
    4. Peak season calendar adjustment.
    5. Consistent oversupply → reduce buffer.
    """
    multiplier = 1.0
    reasons = []

    # Conformal interval: use P90-P50 spread when available (best signal)
    conformal_spread = 0.0
    if state.forecast_p90 > 0 and state.forecast_loads > 0:
        conformal_spread = (state.forecast_p90 - state.forecast_loads) / max(1, state.forecast_loads)
        if conformal_spread > 0.10:
            # Conformal interval says demand could be 10%+ above P50
            multiplier *= (1.0 + conformal_spread)
            reasons.append(f"conformal P90 spread {conformal_spread*100:.0f}%")

    # Tender reject rate (primary capacity signal)
    if state.recent_tender_reject_rate > 0.15:
        # Minimum buffer to absorb rejections: forecast × r / (1-r)
        reject_multiplier = 1.0 + state.recent_tender_reject_rate / max(0.01, 1.0 - state.recent_tender_reject_rate)
        multiplier *= min(1.5, reject_multiplier)  # Cap at 1.5×
        reasons.append(f"tender reject rate {state.recent_tender_reject_rate*100:.0f}%")

    # Demand CV fallback (when conformal not available)
    if conformal_spread <= 0.10 and state.demand_cv > 0.3:
        multiplier *= (1.0 + state.demand_cv * 0.5)
        reasons.append(f"demand volatility CV={state.demand_cv:.2f}")

    if state.is_peak_season:
        multiplier *= 1.2
        reasons.append("peak season")

    if state.demand_trend > 0.1:
        multiplier *= (1.0 + state.demand_trend * 0.2)
        reasons.append(f"demand trend {state.demand_trend:+.2f}")

    if state.recent_capacity_miss_count >= 3:
        multiplier *= 1.25
        reasons.append(f"{state.recent_capacity_miss_count} capacity misses")

    # Reduce if consistently oversupplied
    if (state.recent_tender_reject_rate < 0.05
            and state.demand_cv < 0.15
            and not state.is_peak_season
            and state.recent_capacity_miss_count == 0
            and conformal_spread < 0.05):
        multiplier = 0.85
        reasons = ["consistently oversupplied"]

    adjusted_buffer = max(1, int(state.baseline_buffer_loads * multiplier))
    action = Actions.MODIFY if abs(multiplier - 1.0) > 0.05 else Actions.ACCEPT

    return TMSHeuristicDecision(
        trm_type="capacity_buffer", action=action,
        quantity=adjusted_buffer,
        reasoning=f"Buffer {'adjusted' if action == Actions.MODIFY else 'maintained'}: {'; '.join(reasons) or 'nominal'}",
        urgency=0.3 + abs(multiplier - 1.0) * 0.5,
        params_used={
            "multiplier": round(multiplier, 3),
            "baseline": state.baseline_buffer_loads,
            "conformal_spread": round(conformal_spread, 3),
            "reject_rate": state.recent_tender_reject_rate,
        },
    )


# ============================================================================
# 5. Exception Management
# ============================================================================

def _exception_priority_score(state: ExceptionManagementState) -> float:
    """
    Composite priority score per industry MCDA standard.
    Higher = more urgent. Range roughly 0–1.

        0.25 × severity_factor
      + 0.20 × financial_factor
      + 0.30 × time_criticality   (dominant — determines if resolution is feasible)
      + 0.15 × customer_factor
      + 0.10 × cascade_factor
    """
    import math
    severity_map = {"LOW": 0.15, "MEDIUM": 0.40, "HIGH": 0.70, "CRITICAL": 1.0}
    sev_f = severity_map.get(state.severity, 0.40)

    # Financial: normalized against penalty + shipment value
    total_exposure = state.penalty_exposure + state.estimated_cost_impact
    fin_f = min(1.0, total_exposure / max(1.0, state.shipment_value + state.penalty_exposure))

    # Time criticality: sigmoid ramp as delivery window shrinks
    if state.delivery_window_remaining_hrs <= 0:
        time_f = 1.0
    else:
        # Exponential urgency: approaches 1.0 as window → 0
        time_f = 1.0 - math.exp(-2.0 / max(0.1, state.delivery_window_remaining_hrs))

    # Customer tier: strategic=1.0, transactional=0.3
    cust_f = max(0.2, 1.0 - (state.customer_tier - 1) * 0.2)

    # Cascade: more downstream impact = more urgent
    cascade_f = min(1.0, state.downstream_shipments_affected / 5.0)

    return (0.25 * sev_f + 0.20 * fin_f + 0.30 * time_f
            + 0.15 * cust_f + 0.10 * cascade_f)


def _compute_exception_management(state: ExceptionManagementState) -> TMSHeuristicDecision:
    """
    Exception triage and resolution strategy selection.

    Industry algorithm (Oracle OTM / SAP TM pattern):
    1. Compute composite priority score (severity, financial, time, customer, cascade)
    2. Auto-resolve within tolerance window (delay < appointment buffer)
    3. Select resolution strategy from cost-ordered waterfall:
       accept → carrier intervention → re-tender → reroute → expedite → escalate
    4. Financial gating: don't expedite if expedite_cost > penalty_exposure

    Temperature excursion always escalates immediately (food safety).
    """
    # Temperature excursion — immediate, no scoring needed (food safety / regulatory)
    if state.exception_type == "TEMPERATURE_EXCURSION":
        return TMSHeuristicDecision(
            trm_type="exception_management", action=Actions.ESCALATE,
            reasoning="Temperature excursion — immediate escalation (food safety)",
            urgency=1.0,
            params_used={"override": "temperature_safety"},
        )

    priority = _exception_priority_score(state)
    scoring = {
        "priority_score": round(priority, 3),
        "severity": state.severity,
        "financial_exposure": state.estimated_cost_impact + state.penalty_exposure,
        "delivery_window_hrs": state.delivery_window_remaining_hrs,
        "customer_tier": state.customer_tier,
        "cascade_count": state.downstream_shipments_affected,
    }

    # Auto-resolve: delay within appointment buffer tolerance (SAP TM "tolerance-based auto-close")
    if (state.estimated_delay_hrs <= state.appointment_buffer_hrs
            and state.severity in ("LOW", "MEDIUM")
            and state.downstream_shipments_affected == 0):
        return TMSHeuristicDecision(
            trm_type="exception_management", action=Actions.ACCEPT,
            reasoning=f"Delay {state.estimated_delay_hrs:.1f}h within {state.appointment_buffer_hrs:.0f}h buffer — auto-absorb (score {priority:.2f})",
            urgency=priority,
            params_used=scoring,
        )

    # Resolution waterfall (cost-ordered), gated by priority score
    if priority >= 0.75:
        # High urgency: re-tender immediately if possible
        if state.can_retender and state.alternate_carriers_available > 0:
            return TMSHeuristicDecision(
                trm_type="exception_management", action=Actions.RETENDER,
                reasoning=f"High priority {priority:.2f}: re-tendering to {state.alternate_carriers_available} alternates ({state.exception_type})",
                urgency=priority,
                params_used=scoring,
            )
        # No re-tender → escalate for manual intervention
        return TMSHeuristicDecision(
            trm_type="exception_management", action=Actions.ESCALATE,
            reasoning=f"High priority {priority:.2f} but no re-tender options — escalating ({state.exception_type})",
            urgency=priority,
            params_used=scoring,
        )

    if priority >= 0.50:
        # Medium urgency: try reroute if available, else re-tender
        if state.can_reroute and state.delivery_window_remaining_hrs > 4:
            return TMSHeuristicDecision(
                trm_type="exception_management", action=Actions.REROUTE,
                reasoning=f"Medium priority {priority:.2f}: rerouting with {state.delivery_window_remaining_hrs:.0f}h window ({state.exception_type})",
                urgency=priority,
                params_used=scoring,
            )
        if state.can_retender and state.alternate_carriers_available > 0:
            # Financial gate: only re-tender if penalty justifies it
            retender_cost = state.estimated_cost_impact * 0.3  # Estimated re-tender premium
            if state.penalty_exposure > retender_cost or state.customer_tier <= 2:
                return TMSHeuristicDecision(
                    trm_type="exception_management", action=Actions.RETENDER,
                    reasoning=f"Medium priority {priority:.2f}: re-tender justified (penalty ${state.penalty_exposure:.0f} > re-tender cost ${retender_cost:.0f})",
                    urgency=priority,
                    params_used=scoring,
                )
        # Escalate for review
        return TMSHeuristicDecision(
            trm_type="exception_management", action=Actions.ESCALATE,
            reasoning=f"Medium priority {priority:.2f}: escalating for review ({state.exception_type}, delay {state.estimated_delay_hrs:.1f}h)",
            urgency=priority,
            params_used=scoring,
        )

    # Low urgency: accept and monitor
    return TMSHeuristicDecision(
        trm_type="exception_management", action=Actions.ACCEPT,
        reasoning=f"Low priority {priority:.2f}: monitoring ({state.exception_type}, {state.estimated_delay_hrs:.1f}h delay, {state.severity})",
        urgency=priority,
        params_used=scoring,
    )


# ============================================================================
# 6. Freight Procurement
# ============================================================================

def _score_carrier(carrier: Dict[str, Any], benchmark_rate: float) -> float:
    """
    Composite carrier score per industry standard (Oracle OTM / SAP TM).
    Higher = better candidate.

        0.35 × cost_factor      (benchmark / rate — lower rate = higher score)
      + 0.25 × otp_factor       (on-time performance)
      + 0.20 × acceptance_factor (historical acceptance rate)
      + 0.10 × compliance_factor (allocation compliance)
      + 0.10 × capacity_factor   (available equipment indicator)
    """
    rate = carrier.get("rate", 0)
    cost_f = min(1.0, benchmark_rate / rate) if rate > 0 and benchmark_rate > 0 else 0.5
    otp_f = carrier.get("otp_pct", 0.90)
    acc_f = carrier.get("acceptance_pct", 0.80)
    comp_f = min(1.0, carrier.get("allocation_compliance", 1.0))
    cap_f = 1.0 if carrier.get("has_capacity", True) else 0.3
    return 0.35 * cost_f + 0.25 * otp_f + 0.20 * acc_f + 0.10 * comp_f + 0.10 * cap_f


def _compute_freight_procurement(state: FreightProcurementState) -> TMSHeuristicDecision:
    """
    Carrier waterfall tendering with composite scoring + market adjustment.

    Industry algorithm (Oracle OTM Carrier Selection Workbench pattern):
    1. Lead-time fast-path: if <4h to pickup, skip waterfall → spot
    2. Acceptance-rate gating: skip carriers with <50% trailing acceptance
    3. Composite scoring: rank by cost+OTP+acceptance+compliance, not just priority
    4. Market adjustment: tight market (OTRI > 0.15) → shorten waterfall depth
    5. DAT benchmark gating: spot rate vs. benchmark, not just vs. contract

    EDI flow: 204 (tender) → 990 (accept/decline).
    """
    benchmark = state.dat_benchmark_rate or state.contract_rate or state.primary_carrier_rate

    # Lead-time fast-path: very short lead time → skip to spot
    if state.lead_time_hours < 4:
        if state.spot_rate > 0:
            premium = ((state.spot_rate - benchmark) / benchmark) if benchmark > 0 else 0
            return TMSHeuristicDecision(
                trm_type="freight_procurement", action=Actions.ACCEPT,
                quantity=state.spot_rate,
                reasoning=f"Short lead time ({state.lead_time_hours:.0f}h): direct to spot at ${state.spot_rate:.2f} ({premium*100:+.0f}% vs benchmark)",
                urgency=0.7,
                params_used={"rate_type": "spot_fast_path", "lead_time_hours": state.lead_time_hours},
            )
        return TMSHeuristicDecision(
            trm_type="freight_procurement", action=Actions.ESCALATE,
            reasoning=f"Short lead time ({state.lead_time_hours:.0f}h) with no spot rate; escalating to broker",
            urgency=0.9,
        )

    # Market-adjusted waterfall depth: tight market → fewer contract attempts
    effective_max_attempts = state.max_tender_attempts
    if state.market_tightness > 0.6:
        effective_max_attempts = max(2, state.max_tender_attempts - 1)

    # Attempt 1: primary carrier (with acceptance-rate gate)
    if state.tender_attempt == 1 and state.primary_carrier_id:
        if state.primary_carrier_acceptance_pct < 0.50:
            # Known decliner — skip to backups
            pass
        else:
            primary_score = _score_carrier({
                "rate": state.primary_carrier_rate,
                "acceptance_pct": state.primary_carrier_acceptance_pct,
                "otp_pct": 0.93,
                "allocation_compliance": 1.0,
            }, benchmark)
            return TMSHeuristicDecision(
                trm_type="freight_procurement", action=Actions.ACCEPT,
                quantity=state.primary_carrier_rate,
                reasoning=f"Primary carrier score {primary_score:.2f} at ${state.primary_carrier_rate:.2f} (accept rate {state.primary_carrier_acceptance_pct*100:.0f}%)",
                urgency=0.3,
                params_used={
                    "carrier_id": state.primary_carrier_id,
                    "rate_type": "contract",
                    "composite_score": round(primary_score, 3),
                    "market_tightness": state.market_tightness,
                },
            )

    # Subsequent attempts: composite-scored backups with acceptance gating
    if state.tender_attempt <= effective_max_attempts and state.backup_carriers:
        eligible = [c for c in state.backup_carriers
                    if c.get("acceptance_pct", 0.80) >= 0.50]
        if eligible:
            scored = [(c, _score_carrier(c, benchmark)) for c in eligible]
            scored.sort(key=lambda x: x[1], reverse=True)
            best_carrier, best_score = scored[0]
            return TMSHeuristicDecision(
                trm_type="freight_procurement", action=Actions.ACCEPT,
                quantity=best_carrier.get("rate", 0),
                reasoning=f"Backup carrier score {best_score:.2f} at ${best_carrier.get('rate', 0):.2f} (attempt {state.tender_attempt}, {len(eligible)} eligible of {len(state.backup_carriers)})",
                urgency=0.5,
                params_used={
                    "carrier_id": best_carrier.get("id"),
                    "rate_type": "contract_backup",
                    "composite_score": round(best_score, 3),
                    "carriers_skipped_low_acceptance": len(state.backup_carriers) - len(eligible),
                },
            )

    # Spot market: gate against DAT benchmark, not just contract rate
    if state.spot_rate > 0 and benchmark > 0:
        premium_vs_benchmark = (state.spot_rate - benchmark) / benchmark
        # Tight market: widen acceptance threshold
        spot_threshold = 0.25 if state.market_tightness < 0.4 else 0.35
        if premium_vs_benchmark < spot_threshold:
            return TMSHeuristicDecision(
                trm_type="freight_procurement", action=Actions.ACCEPT,
                quantity=state.spot_rate,
                reasoning=f"Spot at ${state.spot_rate:.2f} ({premium_vs_benchmark*100:+.0f}% vs DAT benchmark, threshold {spot_threshold*100:.0f}%)",
                urgency=0.6,
                params_used={
                    "rate_type": "spot",
                    "premium_vs_benchmark": round(premium_vs_benchmark, 3),
                    "market_tightness": state.market_tightness,
                    "threshold_used": spot_threshold,
                },
            )

    # Waterfall exhausted → broker
    return TMSHeuristicDecision(
        trm_type="freight_procurement", action=Actions.ESCALATE,
        reasoning=f"Waterfall exhausted after {state.tender_attempt} attempts (market tightness {state.market_tightness:.2f}); escalating to broker",
        urgency=0.8,
        params_used={"market_tightness": state.market_tightness},
    )


# ============================================================================
# 7. Broker Routing
# ============================================================================

def _compute_broker_routing(state: BrokerRoutingState) -> TMSHeuristicDecision:
    """
    Broker selection with time-urgency scaling + market-adjusted premium
    threshold + DAT benchmark anchoring.

    Industry algorithm (CH Robinson / XPO / Uber Freight pattern):
    - P1-P2: most reliable broker regardless of cost
    - Time-urgency: widen premium threshold 10%/hr as pickup approaches
    - Market-adjusted: tight market widens threshold from 25% to 40%
    - DAT benchmark anchoring (not contract rate) for premium calculation
    - Reliability-adjusted cost scoring with fallthrough rate
    """
    if not state.available_brokers:
        return TMSHeuristicDecision(
            trm_type="broker_routing", action=Actions.ESCALATE,
            reasoning="No brokers available; manual intervention required",
            urgency=1.0,
        )

    benchmark = state.dat_benchmark_rate or state.contract_rate
    if benchmark <= 0:
        benchmark = min(b.get("rate", float("inf")) for b in state.available_brokers) * 0.85

    if state.shipment_priority <= 2:
        best = max(state.available_brokers, key=lambda b: b.get("reliability", 0))
        return TMSHeuristicDecision(
            trm_type="broker_routing", action=Actions.ACCEPT,
            quantity=best.get("rate", 0),
            reasoning=f"Critical (P{state.shipment_priority}): broker {best.get('name', 'N/A')} (reliability {best.get('reliability', 0)*100:.0f}%)",
            urgency=0.8,
            params_used={"broker_id": best.get("id"), "selection": "reliability_priority_override"},
        )

    # Score brokers: reliability-adjusted cost with fallthrough penalty
    scored = []
    for broker in state.available_brokers:
        rate = broker.get("rate", float("inf"))
        reliability = broker.get("reliability", 0.5)
        fallthrough = broker.get("fallthrough_rate", 1.0 - reliability)
        rebooking_premium = 0.25
        expected_cost = rate * (1.0 + fallthrough * rebooking_premium)
        scored.append((expected_cost, broker))
    scored.sort(key=lambda x: x[0])
    _, best_broker = scored[0]
    best_rate = best_broker.get("rate", 0)

    # Premium threshold: base + market adjustment + time-urgency scaling
    base_threshold = 0.25
    # Market tightness widens threshold
    market_adj = state.market_tightness * 0.15  # up to +15% in extreme market
    # Time urgency: widen 10%/hr when <6h to pickup
    time_adj = 0.0
    if state.hours_to_pickup < 6:
        time_adj = max(0, (6 - state.hours_to_pickup) * 0.10)
    effective_threshold = base_threshold + market_adj + time_adj

    premium_vs_benchmark = (best_rate - benchmark) / benchmark if benchmark > 0 else 0

    broker_detail = {
        "broker_id": best_broker.get("id"),
        "broker_name": best_broker.get("name"),
        "reliability": best_broker.get("reliability"),
        "premium_vs_benchmark": round(premium_vs_benchmark, 3),
        "effective_threshold": round(effective_threshold, 3),
        "market_adj": round(market_adj, 3),
        "time_adj": round(time_adj, 3),
        "hours_to_pickup": state.hours_to_pickup,
    }

    if premium_vs_benchmark > effective_threshold:
        return TMSHeuristicDecision(
            trm_type="broker_routing", action=Actions.ESCALATE,
            quantity=best_rate,
            reasoning=f"Broker ${best_rate:.0f} is {premium_vs_benchmark*100:+.0f}% vs benchmark (threshold {effective_threshold*100:.0f}%); needs approval",
            urgency=0.7,
            params_used=broker_detail,
        )

    return TMSHeuristicDecision(
        trm_type="broker_routing", action=Actions.ACCEPT,
        quantity=best_rate,
        reasoning=f"Broker {best_broker.get('name', 'N/A')}: ${best_rate:.0f} ({premium_vs_benchmark*100:+.0f}% vs benchmark, threshold {effective_threshold*100:.0f}%)",
        urgency=0.5,
        params_used=broker_detail,
    )


# ============================================================================
# 8. Dock Scheduling
# ============================================================================

def _compute_dock_scheduling(state: DockSchedulingState) -> TMSHeuristicDecision:
    """
    Dock scheduling with equipment-door compatibility + detention cost
    awareness + yard capacity check + congestion-driven mode switching.

    Industry algorithm (SAP EWM / Manhattan WMS pattern):
    1. Priority override: P1-P2 always accommodate
    2. Compatibility: check equipment type vs. door type (reefer/dry/hazmat)
    3. Yard capacity: if yard is full, recommend drop-trailer offsite
    4. Detention cost: compute projected detention $ and prioritize accordingly
    5. Queue congestion: switch live-load → drop-trailer when queue > 3
    6. Utilization management: defer low-priority when utilization > 85%
    """
    util = state.utilization_pct()
    detention_risk = state.detention_risk_score()

    # Projected detention cost
    projected_overage_min = max(0, state.carrier_avg_dwell_minutes - state.free_time_minutes)
    projected_detention_cost = (projected_overage_min / 60.0) * state.detention_rate_per_hour

    dock_detail = {
        "utilization": round(util, 3),
        "detention_risk": round(detention_risk, 3),
        "projected_detention_cost": round(projected_detention_cost, 2),
        "queue_depth": state.current_queue_depth,
        "yard_available": state.yard_spots_available,
        "equipment_type": state.equipment_type,
        "door_type_required": state.required_door_type,
    }

    # Priority override
    if state.shipment_priority <= 2:
        return TMSHeuristicDecision(
            trm_type="dock_scheduling", action=Actions.ACCEPT,
            quantity=state.estimated_load_time_minutes,
            reasoning=f"Priority P{state.shipment_priority}: dock assigned immediately",
            urgency=0.7,
            params_used=dock_detail,
        )

    # Equipment-door compatibility check (reefer needs reefer-capable door)
    if state.equipment_type == "REEFER" and state.available_dock_doors == 0:
        return TMSHeuristicDecision(
            trm_type="dock_scheduling", action=Actions.DEFER,
            reasoning=f"No compatible {state.equipment_type} dock doors available; deferring",
            urgency=0.6,
            params_used=dock_detail,
        )

    # Yard capacity — if no spots, recommend offsite hold
    if state.yard_spots_available <= 0 and not state.is_live_load:
        return TMSHeuristicDecision(
            trm_type="dock_scheduling", action=Actions.DEFER,
            reasoning=f"Yard full ({state.yard_spots_total} spots, 0 available); hold trailer offsite",
            urgency=0.5,
            params_used={**dock_detail, "recommendation": "hold_offsite"},
        )

    # Detention cost trigger: if projected detention > $150, prioritize turnaround
    if projected_detention_cost > 150:
        return TMSHeuristicDecision(
            trm_type="dock_scheduling", action=Actions.MODIFY,
            reasoning=f"Detention risk: ${projected_detention_cost:.0f} projected (avg dwell {state.carrier_avg_dwell_minutes:.0f}min vs {state.free_time_minutes:.0f}min free)",
            urgency=0.8,
            params_used={**dock_detail, "recommendation": "expedite_turnaround"},
        )

    # Queue congestion → switch to drop-trailer
    if state.current_queue_depth > 3 and state.is_live_load:
        return TMSHeuristicDecision(
            trm_type="dock_scheduling", action=Actions.MODIFY,
            reasoning=f"Queue depth {state.current_queue_depth}: convert live-load to drop-trailer",
            urgency=0.6,
            params_used={**dock_detail, "recommendation": "drop_trailer"},
        )

    # Basic detention risk (below $150 but still elevated)
    if detention_risk > 0.5:
        return TMSHeuristicDecision(
            trm_type="dock_scheduling", action=Actions.MODIFY,
            reasoning=f"Elevated detention risk {detention_risk:.0%}: monitoring turnaround",
            urgency=0.5,
            params_used={**dock_detail, "recommendation": "monitor_turnaround"},
        )

    # High utilization — defer low-priority
    if util > 0.85 and state.shipment_priority >= 4:
        return TMSHeuristicDecision(
            trm_type="dock_scheduling", action=Actions.DEFER,
            reasoning=f"Utilization {util*100:.0f}%, deferring P{state.shipment_priority}",
            urgency=0.4,
            params_used=dock_detail,
        )

    return TMSHeuristicDecision(
        trm_type="dock_scheduling", action=Actions.ACCEPT,
        quantity=state.estimated_load_time_minutes,
        reasoning=f"Dock available (util {util*100:.0f}%, queue {state.current_queue_depth}, detention risk {detention_risk:.0%})",
        urgency=0.2,
        params_used=dock_detail,
    )


# ============================================================================
# 9. Load Build
# ============================================================================

def _ftl_ltl_crossover(state: LoadBuildState) -> str:
    """
    Industry FTL vs. LTL crossover decision.

    Standard thresholds (Oracle OTM, Manhattan Associates):
    - < 8,000 lbs and < 10 pallets: LTL is typically cheaper
    - 8,000–12,000 lbs: volume LTL / partial truckload zone
    - > 12,000 lbs or > 12 pallets: FTL is almost always cheaper

    When both LTL rate sum and FTL rate are available, use direct cost
    comparison. Otherwise fall back to weight/pallet thresholds.
    """
    # Direct cost comparison when rates are available
    if state.ftl_rate > 0 and state.ltl_rate_sum > 0:
        if state.ftl_rate < state.ltl_rate_sum:
            return "FTL"
        # Check volume LTL as middle option
        if state.volume_ltl_rate > 0 and state.volume_ltl_rate < state.ftl_rate:
            return "VOLUME_LTL"
        return "LTL"

    # Threshold fallback
    if state.total_weight >= 12000 or state.total_pallets >= 12:
        return "FTL"
    if state.total_weight >= 8000 or state.total_pallets >= 10:
        return "VOLUME_LTL"
    return "LTL"


def _multi_stop_savings(state: LoadBuildState) -> float:
    """
    Clarke-Wright inspired savings estimate for multi-stop loads.

    Savings = sum of individual FTL costs - (multi-stop FTL + stop-off charges).
    Simplified: approximate individual cost as proportional to shipment count.
    """
    if state.shipment_count <= 1 or state.stop_count <= 1:
        return 0.0
    if state.ftl_rate <= 0:
        return 0.0
    # Individual shipment cost ≈ FTL rate each (worst case, all separate trucks)
    individual_total = state.ftl_rate * state.shipment_count
    # Multi-stop cost = one FTL + stop-off charges
    multi_stop_cost = state.ftl_rate + (state.stop_count - 1) * state.stop_off_charge_per_stop
    return max(0.0, individual_total - multi_stop_cost)


def _compute_load_build(state: LoadBuildState) -> TMSHeuristicDecision:
    """
    Load consolidation with FTL/LTL crossover economics + multi-stop savings.

    Industry algorithm (Oracle OTM / Manhattan Associates pattern):
    1. Reject: hazmat or temperature conflict
    2. Over-capacity: split
    3. FTL/LTL crossover: determine optimal mode by weight/cost threshold
    4. Multi-stop evaluation: savings from Clarke-Wright vs. stop-off charges
    5. Underutilized single shipment: hold for consolidation window
    6. Otherwise: accept current load plan

    Key thresholds:
    - FTL breakeven: ~8,000–12,000 lbs or 10–12 pallets
    - Max multi-stop: 3–5 stops (state.max_stops)
    - Consolidation savings target: 15–35% vs. individual LTL
    """
    w_util = state.weight_utilization()
    v_util = state.volume_utilization()

    # Hard constraint: compatibility conflicts
    if state.has_hazmat_conflict or state.has_temp_conflict:
        return TMSHeuristicDecision(
            trm_type="load_build", action=Actions.REJECT,
            reasoning=f"Cannot consolidate: {'hazmat' if state.has_hazmat_conflict else 'temperature'} conflict",
            urgency=0.3,
        )

    # Over-capacity: must split
    if w_util > 0.95 or v_util > 0.95:
        return TMSHeuristicDecision(
            trm_type="load_build", action=Actions.SPLIT,
            reasoning=f"Exceeds capacity: weight {w_util*100:.0f}%, volume {v_util*100:.0f}%",
            urgency=0.5,
        )

    # Delivery window conflict for multi-stop
    if state.stop_count > 1 and not state.delivery_windows_compatible:
        return TMSHeuristicDecision(
            trm_type="load_build", action=Actions.SPLIT,
            reasoning=f"Delivery windows incompatible across {state.stop_count} stops",
            urgency=0.4,
        )

    # FTL/LTL crossover analysis
    optimal_mode = _ftl_ltl_crossover(state)
    ms_savings = _multi_stop_savings(state) if state.shipment_count > 1 else 0.0
    total_savings = state.consolidation_savings + ms_savings

    mode_detail = {
        "optimal_mode": optimal_mode,
        "weight_util": round(w_util, 3),
        "volume_util": round(v_util, 3),
        "consolidation_savings": state.consolidation_savings,
        "multi_stop_savings": round(ms_savings, 2),
        "total_savings": round(total_savings, 2),
        "stop_count": state.stop_count,
    }

    # Consolidation justified: savings positive AND physically feasible
    if total_savings > 0 and state.should_consolidate():
        # Check multi-stop limit
        if state.stop_count > state.max_stops:
            return TMSHeuristicDecision(
                trm_type="load_build", action=Actions.SPLIT,
                reasoning=f"Consolidation saves ${total_savings:.0f} but {state.stop_count} stops exceeds max {state.max_stops}",
                urgency=0.4,
                params_used=mode_detail,
            )
        savings_pct = (total_savings / max(1, state.ltl_rate_sum)) * 100 if state.ltl_rate_sum > 0 else 0
        return TMSHeuristicDecision(
            trm_type="load_build", action=Actions.CONSOLIDATE,
            quantity=state.shipment_count,
            reasoning=f"Consolidate {state.shipment_count} shipments as {optimal_mode}: ${total_savings:.0f} savings ({savings_pct:.0f}%), {w_util*100:.0f}% weight, {v_util*100:.0f}% volume",
            urgency=0.4,
            params_used=mode_detail,
        )

    # LTL crossover: if weight is below FTL breakeven and no savings from consolidation
    if optimal_mode == "LTL" and state.shipment_count == 1:
        return TMSHeuristicDecision(
            trm_type="load_build", action=Actions.ACCEPT,
            quantity=1,
            reasoning=f"Single shipment {state.total_weight:.0f} lbs / {state.total_pallets} pallets — route as LTL (below FTL breakeven)",
            urgency=0.2,
            params_used=mode_detail,
        )

    # Underutilized: hold for consolidation window
    if w_util < 0.50 and state.shipment_count == 1:
        return TMSHeuristicDecision(
            trm_type="load_build", action=Actions.DEFER,
            reasoning=f"Underutilized ({w_util*100:.0f}% weight): hold for {state.consolidation_window_hours:.0f}h consolidation window",
            urgency=0.3,
            params_used=mode_detail,
        )

    # Accept current load plan
    return TMSHeuristicDecision(
        trm_type="load_build", action=Actions.ACCEPT,
        quantity=state.shipment_count,
        reasoning=f"Load accepted as {optimal_mode}: {state.shipment_count} shipments, {w_util*100:.0f}% weight, {v_util*100:.0f}% volume",
        urgency=0.2,
        params_used=mode_detail,
    )


# ============================================================================
# 10. Intermodal Transfer
# ============================================================================

def _compute_intermodal_transfer(state: IntermodalTransferState) -> TMSHeuristicDecision:
    """
    Mode shift with commodity gating + ramp proximity filter + drayage
    decomposition + inventory carrying cost.

    Industry algorithm (Oracle OTM / J.B. Hunt 360 pattern):
    1. Commodity eligibility: hazmat and temperature-controlled → reject
    2. Ramp proximity: >100 miles from origin or dest ramp → reject
    3. Distance filter: <500 miles total → reject
    4. Ramp congestion: >0.7 → reject
    5. Transit feasibility: delivery window must accommodate penalty
    6. All-in cost with drayage decomposition + inventory carrying cost
    7. Savings threshold: 8% minimum (5% on long-haul >800mi)
    """
    savings = state.cost_savings_pct()
    has_time = state.has_time_for_intermodal()

    intermodal_detail = {
        "savings_pct": round(savings, 3),
        "truck_rate": state.truck_rate,
        "intermodal_rate": state.intermodal_rate,
        "drayage_origin": state.drayage_rate_origin,
        "drayage_dest": state.drayage_rate_dest,
        "origin_ramp_miles": state.origin_ramp_distance_miles,
        "dest_ramp_miles": state.dest_ramp_distance_miles,
        "total_truck_miles": state.total_truck_miles,
        "transit_penalty_days": state.transit_time_penalty_days(),
    }

    # Commodity eligibility gate — hazmat and reefer typically ineligible for rail
    if state.is_hazmat:
        return TMSHeuristicDecision(
            trm_type="intermodal_transfer", action=Actions.REJECT,
            reasoning="Hazmat freight ineligible for intermodal",
            urgency=0.1,
            params_used={**intermodal_detail, "gate": "hazmat"},
        )
    if state.is_temperature_controlled:
        return TMSHeuristicDecision(
            trm_type="intermodal_transfer", action=Actions.REJECT,
            reasoning="Temperature-controlled freight — limited intermodal reefer availability",
            urgency=0.1,
            params_used={**intermodal_detail, "gate": "temperature"},
        )

    # Ramp proximity gate — >100 miles kills drayage economics
    if state.origin_ramp_distance_miles > 100 or state.dest_ramp_distance_miles > 100:
        far_end = "origin" if state.origin_ramp_distance_miles > 100 else "destination"
        far_miles = max(state.origin_ramp_distance_miles, state.dest_ramp_distance_miles)
        return TMSHeuristicDecision(
            trm_type="intermodal_transfer", action=Actions.REJECT,
            reasoning=f"{far_end.title()} ramp {far_miles:.0f}mi away (>100mi threshold); drayage kills economics",
            urgency=0.1,
            params_used={**intermodal_detail, "gate": "ramp_proximity"},
        )

    # Distance filter — intermodal rarely viable <500 miles
    if state.total_truck_miles < 500:
        return TMSHeuristicDecision(
            trm_type="intermodal_transfer", action=Actions.REJECT,
            reasoning=f"Lane {state.total_truck_miles:.0f}mi — below 500mi intermodal threshold",
            urgency=0.1,
            params_used={**intermodal_detail, "gate": "distance"},
        )

    # Ramp congestion
    if state.ramp_congestion_level > 0.7:
        return TMSHeuristicDecision(
            trm_type="intermodal_transfer", action=Actions.REJECT,
            reasoning=f"Ramp congestion {state.ramp_congestion_level*100:.0f}%; staying on truck",
            urgency=0.3,
            params_used=intermodal_detail,
        )

    # Transit feasibility
    if not has_time:
        return TMSHeuristicDecision(
            trm_type="intermodal_transfer", action=Actions.REJECT,
            reasoning=f"Transit penalty {state.transit_time_penalty_days():.1f}d exceeds window ({state.delivery_window_days:.1f}d)",
            urgency=0.2,
            params_used=intermodal_detail,
        )

    # Reliability + tight window
    if state.intermodal_reliability_pct < 0.80 and state.delivery_window_days < 2:
        return TMSHeuristicDecision(
            trm_type="intermodal_transfer", action=Actions.REJECT,
            reasoning=f"Reliability {state.intermodal_reliability_pct*100:.0f}% too low for {state.delivery_window_days:.1f}d window",
            urgency=0.3,
            params_used=intermodal_detail,
        )

    # Inventory carrying cost adjustment for high-value freight
    carrying_cost = 0.0
    if state.commodity_value_per_lb > 0 and state.truck_rate > 0:
        # Annual carrying rate ~10%, daily = 10%/365
        daily_carry = state.commodity_value_per_lb * 44000 * (0.10 / 365.0)
        carrying_cost = daily_carry * state.transit_time_penalty_days()

    # All-in savings including carrying cost
    effective_savings = state.truck_rate - state.intermodal_rate - carrying_cost
    effective_savings_pct = effective_savings / state.truck_rate if state.truck_rate > 0 else 0

    # Threshold: 8% normally, 5% on long-haul >800mi
    threshold = 0.05 if state.total_truck_miles > 800 else 0.08

    intermodal_detail["carrying_cost"] = round(carrying_cost, 2)
    intermodal_detail["effective_savings_pct"] = round(effective_savings_pct, 3)
    intermodal_detail["threshold"] = threshold

    if effective_savings_pct >= threshold and has_time:
        return TMSHeuristicDecision(
            trm_type="intermodal_transfer", action=Actions.ACCEPT,
            quantity=state.intermodal_rate,
            reasoning=f"Mode shift {state.current_mode}→{state.candidate_mode}: {effective_savings_pct*100:.0f}% net savings (${effective_savings:.0f}), +{state.transit_time_penalty_days():.1f}d",
            urgency=0.4,
            params_used=intermodal_detail,
        )

    return TMSHeuristicDecision(
        trm_type="intermodal_transfer", action=Actions.REJECT,
        reasoning=f"Net savings {effective_savings_pct*100:.0f}% below {threshold*100:.0f}% threshold (carrying cost ${carrying_cost:.0f})",
        urgency=0.1,
        params_used=intermodal_detail,
    )


# ============================================================================
# 11. Equipment Reposition
# ============================================================================

def _compute_equipment_reposition(state: EquipmentRepositionState) -> TMSHeuristicDecision:
    """
    Equipment repositioning (deadhead management).

    Rules:
    - Source surplus + target deficit: reposition
    - ROI > 1.5: economically justified
    - Fleet utilization >90% and deficit exists: urgent reposition
    - No surplus or no deficit: hold
    """
    surplus = state.source_surplus()
    deficit = state.target_deficit()
    roi = state.reposition_roi()

    if surplus == 0:
        return TMSHeuristicDecision(
            trm_type="equipment_reposition", action=Actions.HOLD,
            reasoning=f"No surplus at source ({state.source_equipment_count} equipment, {state.source_demand_next_7d} needed)",
            urgency=0.1,
        )

    if deficit == 0:
        return TMSHeuristicDecision(
            trm_type="equipment_reposition", action=Actions.HOLD,
            reasoning=f"No deficit at target ({state.target_equipment_count} equipment, {state.target_demand_next_7d} needed)",
            urgency=0.1,
        )

    if state.fleet_utilization_pct > 0.90 and deficit > 0:
        qty = min(surplus, deficit)
        return TMSHeuristicDecision(
            trm_type="equipment_reposition", action=Actions.REPOSITION,
            quantity=qty,
            reasoning=f"Urgent: fleet {state.fleet_utilization_pct*100:.0f}% utilized, repositioning {qty} {state.equipment_type} units ({state.reposition_miles:.0f}mi)",
            urgency=0.8,
        )

    if roi > 1.5:
        qty = min(surplus, deficit, state.breakeven_loads)
        return TMSHeuristicDecision(
            trm_type="equipment_reposition", action=Actions.REPOSITION,
            quantity=qty,
            reasoning=f"ROI {roi:.1f}x: reposition {qty} units, cost ${state.reposition_cost:.0f} vs ${state.cost_of_not_repositioning:.0f} spot premium avoided",
            urgency=0.5,
            params_used={"roi": roi, "miles": state.reposition_miles},
        )

    if roi > 1.0 and state.reposition_miles < 200:
        qty = min(surplus, deficit)
        return TMSHeuristicDecision(
            trm_type="equipment_reposition", action=Actions.REPOSITION,
            quantity=qty,
            reasoning=f"Short reposition ({state.reposition_miles:.0f}mi, ROI {roi:.1f}x)",
            urgency=0.3,
        )

    return TMSHeuristicDecision(
        trm_type="equipment_reposition", action=Actions.HOLD,
        reasoning=f"Reposition ROI {roi:.1f}x below threshold (1.5x) for {state.reposition_miles:.0f}mi move",
        urgency=0.2,
    )


# ============================================================================
# 12. Lane Volume Forecast (NEW — Execution-tier orchestrator)
# ============================================================================

# ── §3.36 — Lane-volume forecast segmentation ──────────────────────────


def _normalize_share(history: Dict[str, float]) -> Dict[str, float]:
    """Normalise a mix-share dict so values sum to 1.0.

    Defensive: if all values are zero or negative, returns the input
    unchanged (caller decides what to do).
    """
    total = sum(v for v in history.values() if v > 0)
    if total <= 0:
        return dict(history)
    return {k: max(0.0, v) / total for k, v in history.items() if v > 0}


def compute_segmented_loads(
    state: LaneVolumeForecastState,
    aggregate_loads_p50: float,
) -> Dict[str, Any]:
    """Compute mode-level + equipment-level mix shares + secondary
    tonnage / cube derivations.

    Industry-norm segmentation (§3.36): forecast aggregate loads at the
    lane level, then split by EWMA-smoothed historical share. Equipment
    segmentation runs only inside the FTL share when ``equipment_history``
    is populated.

    The function returns a dict suitable for merging into a heuristic
    decision's ``params_used`` so consumers can read per-mode /
    per-equipment forecasts without changing the ``TMSHeuristicDecision``
    schema.

    Returns:
        Dict with keys:
            - ``segmentation_method``: one of
              ``"ewma_share_history"`` (multi-mode mix applied),
              ``"single_mode_passthrough"`` (one mode dominates ≥ 95%),
              ``"no_segmentation"`` (history unavailable; loads is the
              only signal).
            - ``forecast_loads_p50``: aggregate loads forecast (echo).
            - ``mode_mix``: dict of mode → share (sums to ~1.0) when
              segmented; empty otherwise.
            - ``mode_loads_p50``: dict of mode → forecast loads. Always
              present (single-key dict in the no-segmentation case).
            - ``equipment_mix``: dict of equipment → share within FTL,
              when ``equipment_history`` populated; empty otherwise.
            - ``equipment_loads_p50``: dict of equipment → forecast
              loads within FTL.
            - ``forecast_weight_kg_p50``: derived from
              ``proposed_weight_kg_p50`` if non-zero, else
              ``mean_weight_kg_per_load × forecast_loads_p50``.
            - ``forecast_volume_m3_p50``: derived analogously.
    """
    out: Dict[str, Any] = {
        "forecast_loads_p50": float(aggregate_loads_p50),
        "mode_mix": {},
        "mode_loads_p50": {},
        "equipment_mix": {},
        "equipment_loads_p50": {},
    }

    # ── Mode segmentation ────────────────────────────────────────────
    mode_hist = state.mode_history or {}
    if not mode_hist:
        out["segmentation_method"] = "no_segmentation"
        out["mode_loads_p50"] = {"unsegmented": float(aggregate_loads_p50)}
    else:
        mode_mix = _normalize_share(mode_hist)
        # If one mode is ≥ 0.95 share, treat as single-mode passthrough.
        dominant = max(mode_mix.items(), key=lambda kv: kv[1], default=("", 0.0))
        if dominant[1] >= 0.95:
            out["segmentation_method"] = "single_mode_passthrough"
            out["mode_mix"] = {dominant[0]: 1.0}
            out["mode_loads_p50"] = {dominant[0]: float(aggregate_loads_p50)}
        else:
            out["segmentation_method"] = "ewma_share_history"
            out["mode_mix"] = {k: round(v, 4) for k, v in mode_mix.items()}
            out["mode_loads_p50"] = {
                k: round(v * float(aggregate_loads_p50), 2)
                for k, v in mode_mix.items()
            }

    # ── Equipment segmentation (within FTL only) ─────────────────────
    equip_hist = state.equipment_history or {}
    ftl_loads = float(out["mode_loads_p50"].get("FTL", 0.0))
    if equip_hist and ftl_loads > 0:
        equip_mix = _normalize_share(equip_hist)
        out["equipment_mix"] = {k: round(v, 4) for k, v in equip_mix.items()}
        out["equipment_loads_p50"] = {
            k: round(v * ftl_loads, 2) for k, v in equip_mix.items()
        }

    # ── Secondary tonnage / cube (P50 only per industry norm) ────────
    if state.proposed_weight_kg_p50 > 0:
        out["forecast_weight_kg_p50"] = float(state.proposed_weight_kg_p50)
    elif state.mean_weight_kg_per_load > 0:
        out["forecast_weight_kg_p50"] = round(
            state.mean_weight_kg_per_load * float(aggregate_loads_p50), 2,
        )
    else:
        out["forecast_weight_kg_p50"] = 0.0

    if state.proposed_volume_m3_p50 > 0:
        out["forecast_volume_m3_p50"] = float(state.proposed_volume_m3_p50)
    elif state.mean_volume_m3_per_load > 0:
        out["forecast_volume_m3_p50"] = round(
            state.mean_volume_m3_per_load * float(aggregate_loads_p50), 2,
        )
    else:
        out["forecast_volume_m3_p50"] = 0.0

    return out


def _compute_lane_volume_forecast(state: LaneVolumeForecastState) -> TMSHeuristicDecision:
    """
    Lane-volume forecast orchestrator. Decides which model family to route to
    (Holt-Winters / LightGBM / Croston / TSB / AutoETS cold-start) via
    Syntetos-Boylan classification, then commits a publish action.

    Action semantics (TMS Execution-tier, sub-10ms):
      ACCEPT    — proposed forecast is reliable, ship it
      MODIFY    — apply external signal overlay (promo / event / market shift)
                  or peak-season precautionary buffer; reasoning records
                  the overlay magnitude
      ESCALATE  — low confidence (cold start, declining trend, large
                  trailing MAPE, or wide conformal interval); flag for
                  human review before publishing
      DEFER     — insufficient history (< 4 weeks) — cannot forecast yet,
                  fall back to upstream supply / transfer plan or
                  customer-supplied volume

    Industry pattern: mirrors SCP's Forecast Baseline TRM but at lane grain.
    Model selection is heuristic-internal (Syntetos-Boylan + covariate
    eligibility); the TRM's surfaced action is whether to commit the
    resulting forecast.

    Gating reminder: this TRM is provisioned only on shipper facilities
    and only invoked at runtime for lanes with `has_external_endpoint=True`.
    Internal-transfer and inbound lanes inherit volume from the upstream
    supply / transfer plan and never reach this heuristic.
    """
    cls = state.syntetos_boylan_class()
    method = state.recommended_model()

    # §3.36 — segmentation rides through every action path via params_used
    segmentation = compute_segmented_loads(state, state.proposed_forecast_p50)

    detail = {
        "demand_class": cls,
        "recommended_method": method,
        "weeks_history": state.weeks_of_history,
        "adi": round(state.adi(), 2),
        "cv2": round(state.cv_squared(), 2),
        "trailing_mape": round(state.trailing_mape, 3),
        "interval_width_pct": round(state.forecast_interval_width_pct, 3),
        **segmentation,
    }

    # ── DEFER: insufficient data to forecast ─────────────────────────
    if state.weeks_of_history < 4:
        return TMSHeuristicDecision(
            trm_type="lane_volume_forecast", action=Actions.DEFER,
            quantity=0.0,
            reasoning=(
                f"Insufficient history ({state.weeks_of_history}w < 4w required); "
                f"defer — fall back to upstream plan or customer-supplied volume"
            ),
            urgency=0.3, confidence=0.5,
            params_used=detail,
        )

    # ── ESCALATE: cold start (NEW class) — needs human eyes ─────────
    if cls == "NEW":
        return TMSHeuristicDecision(
            trm_type="lane_volume_forecast", action=Actions.ESCALATE,
            quantity=state.proposed_forecast_p50,
            reasoning=(
                f"Cold-start lane ({state.weeks_of_history}w history < 8w threshold); "
                f"AutoETS cold-start fit, confidence low — flag for review"
            ),
            urgency=0.6, confidence=0.4,
            params_used=detail,
        )

    # ── ESCALATE: declining lane (potential EOL / churn signal) ─────
    if cls == "DECLINING":
        return TMSHeuristicDecision(
            trm_type="lane_volume_forecast", action=Actions.ESCALATE,
            quantity=state.proposed_forecast_p50,
            reasoning=(
                f"Declining lane (slope {state.trend_slope:+.3f}, last {state.last_period_actual:.1f} "
                f"vs mean {state.mean_demand:.1f}); flag EOL / customer-churn risk"
            ),
            urgency=0.6, confidence=0.5,
            params_used=detail,
        )

    # ── ESCALATE: trailing MAPE blown out ────────────────────────────
    if state.trailing_mape > 0.40:
        return TMSHeuristicDecision(
            trm_type="lane_volume_forecast", action=Actions.ESCALATE,
            quantity=state.proposed_forecast_p50,
            reasoning=(
                f"Trailing MAPE {state.trailing_mape*100:.0f}% > 40% — model performance "
                f"degraded; class {cls}, currently {state.forecast_method_in_use or 'untrained'}"
            ),
            urgency=0.7, confidence=0.4,
            params_used=detail,
        )

    # ── ESCALATE: very wide conformal interval (uncertainty) ────────
    if state.forecast_interval_width_pct > 1.5:
        return TMSHeuristicDecision(
            trm_type="lane_volume_forecast", action=Actions.ESCALATE,
            quantity=state.proposed_forecast_p50,
            reasoning=(
                f"P10–P90 interval width {state.forecast_interval_width_pct*100:.0f}% of P50 "
                f"(>150% threshold); class {cls}, method {method}"
            ),
            urgency=0.5, confidence=0.4,
            params_used=detail,
        )

    # ── ESCALATE: poor conformal coverage (calibration drift) ───────
    if state.conformal_coverage_p80 < 0.60:
        return TMSHeuristicDecision(
            trm_type="lane_volume_forecast", action=Actions.ESCALATE,
            quantity=state.proposed_forecast_p50,
            reasoning=(
                f"Conformal coverage {state.conformal_coverage_p80*100:.0f}% < 60% target; "
                f"calibration drift on {method}"
            ),
            urgency=0.5, confidence=0.5,
            params_used=detail,
        )

    # ── MODIFY: signal overlay (promo / NPI / EOL / event / market) ─
    if state.signal_type and state.signal_magnitude > 0 and state.signal_confidence > 0.5:
        adjustment = state.signal_magnitude * state.proposed_forecast_p50 * state.signal_confidence
        return TMSHeuristicDecision(
            trm_type="lane_volume_forecast", action=Actions.MODIFY,
            quantity=adjustment,
            reasoning=(
                f"Signal {state.signal_type}: magnitude {state.signal_magnitude:+.2f} × "
                f"confidence {state.signal_confidence:.2f}; class {cls}, method {method}"
            ),
            urgency=0.5, confidence=state.signal_confidence,
            params_used={**detail, "signal_type": state.signal_type,
                         "signal_magnitude": state.signal_magnitude},
        )

    # ── MODIFY: peak season + degraded MAPE → precautionary buffer ──
    if state.is_peak_season and state.trailing_mape > 0.20:
        adjustment = state.proposed_forecast_p50 * 0.10
        return TMSHeuristicDecision(
            trm_type="lane_volume_forecast", action=Actions.MODIFY,
            quantity=adjustment,
            reasoning=(
                f"Peak season + MAPE {state.trailing_mape*100:.0f}% > 20%: +10% precautionary "
                f"buffer on top of {method} P50"
            ),
            urgency=0.4, confidence=0.6,
            params_used=detail,
        )

    # ── ACCEPT: ship the proposed forecast ──────────────────────────
    return TMSHeuristicDecision(
        trm_type="lane_volume_forecast", action=Actions.ACCEPT,
        quantity=state.proposed_forecast_p50,
        reasoning=(
            f"Class {cls}, method {method}, MAPE {state.trailing_mape*100:.0f}%, "
            f"coverage {state.conformal_coverage_p80*100:.0f}% — publish forecast"
        ),
        urgency=0.2, confidence=0.85,
        params_used=detail,
    )
