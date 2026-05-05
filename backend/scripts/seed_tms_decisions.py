#!/usr/bin/env python3
"""
Seed TMS AgentDecision records for all 11 TMS TRM agents.

Reads demo data created by seed_tms_demo.py and generates realistic
decisions by running the TMS heuristic library over DB state.

Usage:
    docker compose exec backend python scripts/seed_tms_decisions.py --tenant-id 1 --config-id 1
"""

import argparse
import os
import random
import sys
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy import select

from app.db.session import sync_session_factory
from app.models.decision_tracking import (
    AgentDecision,
    DecisionType,
    DecisionStatus,
    DecisionUrgency,
)
from app.models.tms_entities import (
    Carrier,
    CarrierLane,
    CarrierScorecard,
    Load,
    Shipment,
    ShipmentException,
    Appointment,
    FreightRate,
    Commodity,
    LoadStatus,
    ShipmentStatus,
    ExceptionSeverity,
)
from app.models.transportation_config import LaneProfile, FacilityConfig
from app.models.tms_planning import ShippingForecast
from app.models.supply_chain_config import Site, TransportationLane
from autonomy_tms_heuristics.library.base import (
    CapacityPromiseState,
    ShipmentTrackingState,
    DemandSensingState,
    CapacityBufferState,
    ExceptionManagementState,
    FreightProcurementState,
    BrokerRoutingState,
    DockSchedulingState,
    LoadBuildState,
    IntermodalTransferState,
    EquipmentRepositionState,
    TMSHeuristicDecision,
)
from autonomy_tms_heuristics.library.dispatch import (
    compute_tms_decision,
    Actions,
)


# ============================================================================
# Helpers
# ============================================================================

AGENT_TYPE = "tms_trm"
AGENT_VERSION = "1.0.0"

# Reverse action lookup for human-readable descriptions
ACTION_LABELS = {
    Actions.ACCEPT: "ACCEPT",
    Actions.REJECT: "REJECT",
    Actions.DEFER: "DEFER",
    Actions.ESCALATE: "ESCALATE",
    Actions.MODIFY: "MODIFY",
    Actions.RETENDER: "RETENDER",
    Actions.REROUTE: "REROUTE",
    Actions.CONSOLIDATE: "CONSOLIDATE",
    Actions.SPLIT: "SPLIT",
    Actions.REPOSITION: "REPOSITION",
    Actions.HOLD: "HOLD",
}


def _pick_status() -> DecisionStatus:
    """60% INFORMED, 30% ACTIONED, 10% OVERRIDDEN weighted random."""
    roll = random.random()
    if roll < 0.60:
        return DecisionStatus.INFORMED
    if roll < 0.90:
        return DecisionStatus.ACTIONED
    return DecisionStatus.OVERRIDDEN


def _urgency_from_score(urgency: float) -> DecisionUrgency:
    if urgency > 0.7:
        return DecisionUrgency.URGENT
    if urgency >= 0.3:
        return DecisionUrgency.STANDARD
    return DecisionUrgency.LOW


def _rand_created_at() -> datetime:
    """Random timestamp within last 7 days."""
    days = random.uniform(0, 7)
    return datetime.utcnow() - timedelta(days=days)


def _decision_exists(session, tenant_id: int, decision_type: DecisionType, item_code: str) -> bool:
    stmt = select(AgentDecision).where(
        AgentDecision.tenant_id == tenant_id,
        AgentDecision.decision_type == decision_type,
        AgentDecision.item_code == item_code,
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def _serialize_state(state: Any) -> Dict[str, Any]:
    """Convert a state dataclass to a JSON-safe dict for context_data."""
    result: Dict[str, Any] = {}
    for key, value in state.__dict__.items():
        if isinstance(value, (datetime, date)):
            result[key] = value.isoformat()
        elif isinstance(value, (list, dict, str, int, float, bool)) or value is None:
            result[key] = value
        else:
            result[key] = str(value)
    return result


def _build_agent_decision(
    tenant_id: int,
    decision_type: DecisionType,
    item_code: str,
    item_name: str,
    category: str,
    issue_summary: str,
    recommendation: str,
    decision: TMSHeuristicDecision,
    state: Any,
    impact_value: Optional[float] = None,
    impact_description: Optional[str] = None,
) -> AgentDecision:
    """Translate a TMSHeuristicDecision into an AgentDecision row."""
    status = _pick_status()
    created_at = _rand_created_at()

    user_action: Optional[str] = None
    override_reason: Optional[str] = None
    action_timestamp: Optional[datetime] = None
    if status == DecisionStatus.ACTIONED:
        user_action = "accept"
        action_timestamp = created_at + timedelta(minutes=random.randint(1, 120))
    elif status == DecisionStatus.OVERRIDDEN:
        user_action = "reject"
        override_reason = (
            "Planner override: local context requires manual coordination "
            "with customer / carrier before executing agent recommendation."
        )
        action_timestamp = created_at + timedelta(minutes=random.randint(5, 240))

    return AgentDecision(
        tenant_id=tenant_id,
        decision_type=decision_type,
        item_code=item_code,
        item_name=item_name,
        category=category,
        issue_summary=issue_summary,
        impact_value=impact_value,
        impact_description=impact_description,
        agent_recommendation=recommendation,
        agent_reasoning=decision.reasoning,
        agent_confidence=round(random.uniform(0.65, 0.95), 3),
        recommended_value=float(decision.quantity) if decision.quantity else None,
        status=status,
        urgency=_urgency_from_score(decision.urgency),
        user_action=user_action,
        override_reason=override_reason,
        action_timestamp=action_timestamp,
        agent_type=AGENT_TYPE,
        agent_version=AGENT_VERSION,
        created_at=created_at,
        context_data=_serialize_state(state),
    )


# ============================================================================
# Data fetchers
# ============================================================================

def _fetch_sites(session, config_id: int) -> List[Site]:
    return session.execute(
        select(Site).where(Site.config_id == config_id)
    ).scalars().all()


def _fetch_lanes(session, tenant_id: int) -> List[LaneProfile]:
    return session.execute(
        select(LaneProfile).where(LaneProfile.tenant_id == tenant_id)
    ).scalars().all()


def _fetch_loads(session, tenant_id: int, statuses: Optional[List[LoadStatus]] = None) -> List[Load]:
    stmt = select(Load).where(Load.tenant_id == tenant_id)
    if statuses:
        stmt = stmt.where(Load.status.in_(statuses))
    return session.execute(stmt).scalars().all()


def _fetch_shipments(session, tenant_id: int, statuses: Optional[List[ShipmentStatus]] = None) -> List[Shipment]:
    stmt = select(Shipment).where(Shipment.tenant_id == tenant_id)
    if statuses:
        stmt = stmt.where(Shipment.status.in_(statuses))
    return session.execute(stmt).scalars().all()


def _fetch_carriers(session, tenant_id: int) -> List[Carrier]:
    return session.execute(
        select(Carrier).where(Carrier.tenant_id == tenant_id)
    ).scalars().all()


def _fetch_scorecards_by_carrier(session, tenant_id: int) -> Dict[int, CarrierScorecard]:
    scorecards = session.execute(
        select(CarrierScorecard).where(CarrierScorecard.tenant_id == tenant_id)
    ).scalars().all()
    return {sc.carrier_id: sc for sc in scorecards}


def _fetch_exceptions(session, tenant_id: int) -> List[ShipmentException]:
    return session.execute(
        select(ShipmentException).where(ShipmentException.tenant_id == tenant_id)
    ).scalars().all()


def _fetch_appointments(session, tenant_id: int) -> List[Appointment]:
    return session.execute(
        select(Appointment).where(Appointment.tenant_id == tenant_id)
    ).scalars().all()


def _fetch_rates_by_lane(session, tenant_id: int) -> Dict[int, List[FreightRate]]:
    rates = session.execute(
        select(FreightRate).where(FreightRate.tenant_id == tenant_id)
    ).scalars().all()
    by_lane: Dict[int, List[FreightRate]] = {}
    for r in rates:
        by_lane.setdefault(r.lane_id, []).append(r)
    return by_lane


def _fetch_facility_configs(session, tenant_id: int) -> List[FacilityConfig]:
    return session.execute(
        select(FacilityConfig).where(FacilityConfig.tenant_id == tenant_id)
    ).scalars().all()


def _lane_label(session, lane_profile: LaneProfile) -> str:
    lane = session.execute(
        select(TransportationLane).where(TransportationLane.id == lane_profile.lane_id)
    ).scalar_one_or_none()
    if not lane:
        return f"Lane #{lane_profile.lane_id}"
    origin = session.execute(select(Site).where(Site.id == lane.from_site_id)).scalar_one_or_none()
    dest = session.execute(select(Site).where(Site.id == lane.to_site_id)).scalar_one_or_none()
    origin_name = origin.name if origin else f"Site {lane.from_site_id}"
    dest_name = dest.name if dest else f"Site {lane.to_site_id}"
    return f"{origin_name} -> {dest_name}"


def _site_name(session, site_id: Optional[int]) -> str:
    if not site_id:
        return "Unknown Site"
    site = session.execute(select(Site).where(Site.id == site_id)).scalar_one_or_none()
    return site.name if site else f"Site {site_id}"


# ============================================================================
# 1. Capacity Promise
# ============================================================================

def seed_capacity_promise_decisions(session, tenant_id: int, config_id: int) -> int:
    lanes = _fetch_lanes(session, tenant_id)
    loads = _fetch_loads(session, tenant_id, [LoadStatus.PLANNING, LoadStatus.TENDERED])
    if not lanes:
        print("  capacity_promise: no LaneProfiles — skipping")
        return 0

    created = 0
    target = max(5, min(10, len(lanes)))
    for i, lane in enumerate(lanes[:target]):
        item_code = f"CAP-PROMISE-LANE-{lane.lane_id}"
        if _decision_exists(session, tenant_id, DecisionType.CAPACITY_PROMISE, item_code):
            continue

        weekly = lane.avg_weekly_volume or 12
        forecast_loads = int(weekly + (i * 2) % 5)
        committed = int(weekly * 0.8)
        total_capacity = int(weekly * 1.2)
        booked = int(weekly * 0.75) + (i % 3)
        requested = 1 + (i % 4)
        priority = 1 + (i % 5)

        state = CapacityPromiseState(
            shipment_id=loads[i % len(loads)].id if loads else 0,
            lane_id=lane.lane_id,
            requested_date=datetime.utcnow() + timedelta(days=1 + (i % 5)),
            requested_loads=requested,
            mode=lane.primary_mode or "FTL",
            priority=priority,
            committed_capacity=committed,
            total_capacity=total_capacity,
            buffer_capacity=max(1, int(weekly * 0.1)),
            forecast_loads=forecast_loads,
            booked_loads=booked,
            primary_carrier_available=(i % 4 != 0),
            backup_carriers_count=1 + (i % 3),
            spot_rate_premium_pct=round(0.05 + (i % 5) * 0.05, 2),
        )

        decision = compute_tms_decision("capacity_promise", state)
        lane_name = _lane_label(session, lane)

        recommendation_map = {
            Actions.ACCEPT: f"Commit {requested} load(s) on lane {lane_name}",
            Actions.REJECT: f"Decline capacity promise on lane {lane_name}",
            Actions.DEFER: f"Defer capacity promise on lane {lane_name} to procurement",
        }
        recommendation = recommendation_map.get(
            decision.action,
            f"{ACTION_LABELS.get(decision.action, 'EVALUATE')} capacity promise on lane {lane_name}",
        )

        session.add(_build_agent_decision(
            tenant_id=tenant_id,
            decision_type=DecisionType.CAPACITY_PROMISE,
            item_code=item_code,
            item_name=lane_name,
            category=lane.primary_mode or "FTL",
            issue_summary=(
                f"Capacity promise request: {requested} load(s) on {lane_name} "
                f"at P{priority} priority, {state.utilization_pct()*100:.0f}% current utilization"
            ),
            recommendation=recommendation,
            decision=decision,
            state=state,
            impact_value=float(requested),
            impact_description=f"{requested} loads requested",
        ))
        created += 1
    print(f"  capacity_promise: {created} decisions created")
    return created


# ============================================================================
# 2. Shipment Tracking
# ============================================================================

def seed_shipment_tracking_decisions(session, tenant_id: int, config_id: int) -> int:
    shipments = _fetch_shipments(session, tenant_id, [ShipmentStatus.IN_TRANSIT, ShipmentStatus.DISPATCHED])
    carriers = {c.id: c for c in _fetch_carriers(session, tenant_id)}
    scorecards = _fetch_scorecards_by_carrier(session, tenant_id)

    if not shipments:
        print("  shipment_tracking: no in-transit shipments — skipping")
        return 0

    created = 0
    target = min(10, len(shipments))
    for i, shipment in enumerate(shipments[:target]):
        item_code = f"TRACK-{shipment.shipment_number}"
        if _decision_exists(session, tenant_id, DecisionType.SHIPMENT_TRACKING, item_code):
            continue

        # Derive tracking state
        now = datetime.utcnow()
        planned_delivery = shipment.requested_delivery_date or (now + timedelta(hours=24))
        # Mix of on-time, late, and stale shipments for variety
        is_late = (i % 3 == 0)
        is_stale = (i % 5 == 0)
        current_eta = planned_delivery + timedelta(hours=6 if is_late else -1)
        last_update_hours = 8.0 if is_stale else round(0.5 + (i % 3) * 1.2, 1)

        pct_complete = 0.3 + (i % 7) * 0.1
        total_miles = float(shipment.current_lat and 1200 or 900)
        carrier = carriers.get(shipment.carrier_id) if shipment.carrier_id else None
        sc = scorecards.get(shipment.carrier_id) if shipment.carrier_id else None
        carrier_otp = (sc.on_time_delivery_pct / 100.0) if sc and sc.on_time_delivery_pct else 0.9

        state = ShipmentTrackingState(
            shipment_id=shipment.id,
            shipment_status=str(shipment.status.value) if shipment.status else "IN_TRANSIT",
            planned_pickup=shipment.requested_pickup_date,
            actual_pickup=shipment.actual_pickup_date,
            planned_delivery=planned_delivery,
            current_eta=current_eta,
            current_lat=shipment.current_lat or 0.0,
            current_lon=shipment.current_lon or 0.0,
            last_update_hours_ago=last_update_hours,
            total_miles=total_miles,
            miles_remaining=total_miles * (1 - pct_complete),
            pct_complete=pct_complete,
            carrier_otp_pct=carrier_otp,
            carrier_reliability_score=(sc.composite_score / 100.0) if sc and sc.composite_score else 0.8,
            active_exceptions_count=len(shipment.exceptions) if hasattr(shipment, 'exceptions') and shipment.exceptions else 0,
            is_temperature_sensitive=bool(shipment.is_temperature_sensitive),
        )

        decision = compute_tms_decision("shipment_tracking", state)

        recommendation_map = {
            Actions.ACCEPT: f"Tracking nominal for {shipment.shipment_number}",
            Actions.ESCALATE: f"Escalate {shipment.shipment_number}: tracking anomaly detected",
            Actions.MODIFY: f"Update ETA / notify consignee for {shipment.shipment_number}",
        }
        recommendation = recommendation_map.get(
            decision.action,
            f"{ACTION_LABELS.get(decision.action, 'REVIEW')} tracking for {shipment.shipment_number}",
        )

        session.add(_build_agent_decision(
            tenant_id=tenant_id,
            decision_type=DecisionType.SHIPMENT_TRACKING,
            item_code=item_code,
            item_name=shipment.shipment_number,
            category="in_transit",
            issue_summary=(
                f"Shipment {shipment.shipment_number} in transit "
                f"({pct_complete*100:.0f}% complete, last update {last_update_hours:.1f}h ago"
                f"{', LATE' if is_late else ''})"
            ),
            recommendation=recommendation,
            decision=decision,
            state=state,
            impact_value=state.miles_remaining,
            impact_description=f"{state.miles_remaining:.0f} miles remaining",
        ))
        created += 1
    print(f"  shipment_tracking: {created} decisions created")
    return created


# ============================================================================
# 3. Demand Sensing
# ============================================================================

def seed_demand_sensing_decisions(session, tenant_id: int, config_id: int) -> int:
    lanes = _fetch_lanes(session, tenant_id)
    if not lanes:
        print("  demand_sensing: no LaneProfiles — skipping")
        return 0

    # Try to fetch forecasts; if sparse, synthesize from LaneProfile
    forecasts = session.execute(
        select(ShippingForecast).where(ShippingForecast.tenant_id == tenant_id)
    ).scalars().all()
    by_lane = {f.lane_id: f for f in forecasts}

    created = 0
    target = max(5, min(10, len(lanes)))
    today = date.today()
    for i, lane in enumerate(lanes[:target]):
        item_code = f"DEMAND-LANE-{lane.lane_id}-WK{today.isocalendar()[1]}"
        if _decision_exists(session, tenant_id, DecisionType.DEMAND_SENSING, item_code):
            continue

        forecast = by_lane.get(lane.lane_id)
        forecast_loads = float(forecast.forecast_loads) if forecast and forecast.forecast_loads else float(lane.avg_weekly_volume or 12)
        # Introduce realistic bias and variability
        actual_current = forecast_loads * (1.0 + (-0.25 + (i % 6) * 0.08))
        actual_prior = forecast_loads * (1.0 + (-0.15 + (i % 5) * 0.05))
        wow_change = (actual_current - actual_prior) / max(1.0, actual_prior)

        signal_candidates = ["VOLUME_SURGE", "SEASONAL_SHIFT", "PROMOTION", "WEATHER_IMPACT", ""]
        signal_type = signal_candidates[i % len(signal_candidates)]

        state = DemandSensingState(
            lane_id=lane.lane_id,
            period_start=today,
            period_days=7,
            forecast_loads=forecast_loads,
            forecast_method=(str(forecast.forecast_method.value) if forecast and forecast.forecast_method else "CONFORMAL"),
            forecast_mape=float(forecast.mape) if forecast and forecast.mape else round(0.10 + (i % 4) * 0.05, 2),
            actual_loads_current=actual_current,
            actual_loads_prior=actual_prior,
            week_over_week_change_pct=wow_change,
            rolling_4wk_avg=forecast_loads,
            signal_type=signal_type,
            signal_magnitude=round(abs(wow_change), 2),
            signal_confidence=round(0.5 + (i % 5) * 0.1, 2),
            seasonal_index=round(0.9 + (i % 4) * 0.08, 2),
            is_peak_season=(i % 3 == 0),
        )

        decision = compute_tms_decision("demand_sensing", state)
        lane_name = _lane_label(session, lane)

        recommendation_map = {
            Actions.ACCEPT: f"Hold forecast on {lane_name}",
            Actions.MODIFY: f"Adjust forecast on {lane_name} by {decision.quantity:+.0f} loads",
        }
        recommendation = recommendation_map.get(
            decision.action,
            f"{ACTION_LABELS.get(decision.action, 'REVIEW')} forecast on {lane_name}",
        )

        session.add(_build_agent_decision(
            tenant_id=tenant_id,
            decision_type=DecisionType.DEMAND_SENSING,
            item_code=item_code,
            item_name=lane_name,
            category="forecast",
            issue_summary=(
                f"Forecast vs actuals on {lane_name}: "
                f"forecast {forecast_loads:.0f}, actual {actual_current:.0f}, "
                f"WoW {wow_change*100:+.0f}%"
            ),
            recommendation=recommendation,
            decision=decision,
            state=state,
            impact_value=abs(actual_current - forecast_loads),
            impact_description=f"Forecast delta {actual_current - forecast_loads:+.0f} loads",
        ))
        created += 1
    print(f"  demand_sensing: {created} decisions created")
    return created


# ============================================================================
# 4. Capacity Buffer
# ============================================================================

def seed_capacity_buffer_decisions(session, tenant_id: int, config_id: int) -> int:
    lanes = _fetch_lanes(session, tenant_id)
    if not lanes:
        print("  capacity_buffer: no LaneProfiles — skipping")
        return 0

    created = 0
    target = max(5, min(10, len(lanes)))
    for i, lane in enumerate(lanes[:target]):
        item_code = f"CAP-BUFFER-LANE-{lane.lane_id}"
        if _decision_exists(session, tenant_id, DecisionType.CAPACITY_BUFFER, item_code):
            continue

        weekly = lane.avg_weekly_volume or 12
        baseline_buffer = max(1, int(weekly * 0.15))
        forecast = int(weekly + (i % 4))

        state = CapacityBufferState(
            lane_id=lane.lane_id,
            mode=lane.primary_mode or "FTL",
            baseline_buffer_loads=baseline_buffer,
            buffer_policy="PCT_FORECAST",
            forecast_loads=forecast,
            forecast_p10=int(forecast * 0.85),
            forecast_p90=int(forecast * 1.20),
            committed_loads=int(forecast * 0.9),
            contract_capacity=int(weekly * 1.1),
            spot_availability=max(1, int(weekly * 0.2)),
            recent_tender_reject_rate=round(0.05 + (i % 5) * 0.05, 2),
            recent_capacity_miss_count=(i % 4),
            avg_spot_premium_pct=round(0.10 + (i % 4) * 0.05, 2),
            demand_cv=round(0.15 + (i % 5) * 0.06, 2),
            demand_trend=round(-0.1 + (i % 5) * 0.05, 2),
            is_peak_season=(i % 3 == 0),
        )

        decision = compute_tms_decision("capacity_buffer", state)
        lane_name = _lane_label(session, lane)

        if decision.action == Actions.MODIFY:
            recommendation = (
                f"Adjust capacity buffer on {lane_name} from {baseline_buffer} "
                f"to {int(decision.quantity)} loads"
            )
        else:
            recommendation = f"Maintain capacity buffer of {baseline_buffer} on {lane_name}"

        session.add(_build_agent_decision(
            tenant_id=tenant_id,
            decision_type=DecisionType.CAPACITY_BUFFER,
            item_code=item_code,
            item_name=lane_name,
            category=lane.primary_mode or "FTL",
            issue_summary=(
                f"Capacity buffer review on {lane_name}: "
                f"reject rate {state.recent_tender_reject_rate*100:.0f}%, "
                f"demand CV {state.demand_cv:.2f}"
            ),
            recommendation=recommendation,
            decision=decision,
            state=state,
            impact_value=float(decision.quantity - baseline_buffer) if decision.quantity else 0.0,
            impact_description="Buffer load delta",
        ))
        created += 1
    print(f"  capacity_buffer: {created} decisions created")
    return created


# ============================================================================
# 5. Exception Management
# ============================================================================

def seed_exception_management_decisions(session, tenant_id: int, config_id: int) -> int:
    exceptions = _fetch_exceptions(session, tenant_id)
    shipments = {s.id: s for s in _fetch_shipments(session, tenant_id)}

    if not exceptions:
        print("  exception_management: no shipment exceptions — skipping")
        return 0

    created = 0
    target = min(10, len(exceptions))
    now = datetime.utcnow()
    for i, exc in enumerate(exceptions[:target]):
        item_code = f"EXC-{exc.id}"
        if _decision_exists(session, tenant_id, DecisionType.EXCEPTION_MANAGEMENT, item_code):
            continue

        shipment = shipments.get(exc.shipment_id)
        hours_since = (now - exc.detected_at).total_seconds() / 3600 if exc.detected_at else 1.0
        delivery_window_remaining = 12.0 - hours_since if hours_since < 12 else max(0.0, 24 - hours_since)

        state = ExceptionManagementState(
            exception_id=exc.id,
            shipment_id=exc.shipment_id,
            exception_type=str(exc.exception_type.value) if exc.exception_type else "UNKNOWN",
            severity=str(exc.severity.value) if exc.severity else "MEDIUM",
            hours_since_detected=round(hours_since, 1),
            estimated_delay_hrs=float(exc.estimated_delay_hrs or 0.0),
            estimated_cost_impact=float(exc.estimated_cost_impact or 0.0),
            revenue_at_risk=float(exc.revenue_at_risk or 0.0),
            shipment_priority=(shipment.priority if shipment and shipment.priority else 3),
            is_temperature_sensitive=bool(shipment.is_temperature_sensitive) if shipment else False,
            is_hazmat=bool(shipment.is_hazmat) if shipment else False,
            delivery_window_remaining_hrs=round(delivery_window_remaining, 1),
            carrier_id=(shipment.carrier_id if shipment and shipment.carrier_id else 0),
            carrier_reliability_score=0.8,
            can_retender=True,
            alternate_carriers_available=2 + (i % 3),
            can_reroute=(i % 2 == 0),
            can_partial_deliver=(i % 4 == 0),
        )

        decision = compute_tms_decision("exception_management", state)
        ship_number = shipment.shipment_number if shipment else f"Shipment {exc.shipment_id}"

        recommendation_map = {
            Actions.RETENDER: f"Re-tender {ship_number} to alternate carrier",
            Actions.REROUTE: f"Reroute {ship_number} to preserve delivery window",
            Actions.ESCALATE: f"Escalate exception on {ship_number} for planner review",
            Actions.ACCEPT: f"Accept minor delay on {ship_number} and monitor",
        }
        recommendation = recommendation_map.get(
            decision.action,
            f"{ACTION_LABELS.get(decision.action, 'EVALUATE')} exception on {ship_number}",
        )

        session.add(_build_agent_decision(
            tenant_id=tenant_id,
            decision_type=DecisionType.EXCEPTION_MANAGEMENT,
            item_code=item_code,
            item_name=ship_number,
            category=state.exception_type,
            issue_summary=(
                f"{state.severity} {state.exception_type} on {ship_number}: "
                f"{state.estimated_delay_hrs:.1f}h delay, ${state.estimated_cost_impact:.0f} cost impact"
            ),
            recommendation=recommendation,
            decision=decision,
            state=state,
            impact_value=state.estimated_cost_impact,
            impact_description=f"${state.estimated_cost_impact:.0f} estimated impact",
        ))
        created += 1
    print(f"  exception_management: {created} decisions created")
    return created


# ============================================================================
# 6. Freight Procurement
# ============================================================================

def seed_freight_procurement_decisions(session, tenant_id: int, config_id: int) -> int:
    loads = _fetch_loads(session, tenant_id, [LoadStatus.PLANNING, LoadStatus.TENDERED])
    rates_by_lane = _fetch_rates_by_lane(session, tenant_id)
    lane_profiles = {lp.lane_id: lp for lp in _fetch_lanes(session, tenant_id)}
    carriers = {c.id: c for c in _fetch_carriers(session, tenant_id)}

    if not loads:
        print("  freight_procurement: no procurable loads — skipping")
        return 0

    created = 0
    target = min(10, len(loads))
    now = datetime.utcnow()
    for i, load in enumerate(loads[:target]):
        item_code = f"PROCURE-{load.load_number}"
        if _decision_exists(session, tenant_id, DecisionType.FREIGHT_PROCUREMENT, item_code):
            continue

        # Infer lane via origin/dest sites — fall back to first available lane
        lane_id = None
        for lp in lane_profiles.values():
            tl = session.execute(
                select(TransportationLane).where(TransportationLane.id == lp.lane_id)
            ).scalar_one_or_none()
            if tl and tl.from_site_id == load.origin_site_id and tl.to_site_id == load.destination_site_id:
                lane_id = lp.lane_id
                break
        if lane_id is None and lane_profiles:
            lane_id = next(iter(lane_profiles.keys()))

        lane_rates = rates_by_lane.get(lane_id or 0, [])
        primary_rate = next((r for r in lane_rates if r.rate_type and r.rate_type.value == "CONTRACT"), None)
        spot_rate_row = next((r for r in lane_rates if r.rate_type and r.rate_type.value == "SPOT"), None)
        benchmark = lane_profiles[lane_id].benchmark_rate if lane_id in lane_profiles else (load.total_cost or 1500.0)

        primary_carrier_id = load.carrier_id or (primary_rate.carrier_id if primary_rate else None)
        primary_rate_val = float(primary_rate.rate_flat) if primary_rate and primary_rate.rate_flat else float(load.total_cost or 1800.0)

        backups = []
        for r in lane_rates[:3]:
            if r.carrier_id == primary_carrier_id:
                continue
            backups.append({
                "id": r.carrier_id,
                "rate": float(r.rate_flat or 0.0),
                "acceptance_pct": 0.80,
                "priority": len(backups) + 2,
            })

        state = FreightProcurementState(
            load_id=load.id,
            lane_id=lane_id or 0,
            mode=str(load.mode.value) if load.mode else "FTL",
            required_equipment=str(load.equipment_type.value) if load.equipment_type else "DRY_VAN",
            weight=float(load.total_weight or 0.0),
            pallet_count=int(load.total_pallets or 0),
            pickup_date=load.planned_departure,
            delivery_date=load.planned_arrival,
            lead_time_hours=(
                ((load.planned_departure - now).total_seconds() / 3600)
                if load.planned_departure else 48.0
            ),
            primary_carrier_id=primary_carrier_id,
            primary_carrier_rate=primary_rate_val,
            primary_carrier_acceptance_pct=0.85,
            backup_carriers=backups,
            spot_rate=float(spot_rate_row.rate_flat) if spot_rate_row and spot_rate_row.rate_flat else primary_rate_val * 1.15,
            contract_rate=primary_rate_val,
            market_tightness=round(0.3 + (i % 5) * 0.12, 2),
            dat_benchmark_rate=float(benchmark or primary_rate_val),
            tender_attempt=1 + (i % 3),
            max_tender_attempts=3,
            hours_to_tender_deadline=max(4.0, 24.0 - (i * 2)),
        )

        decision = compute_tms_decision("freight_procurement", state)
        carrier_name = carriers[primary_carrier_id].name if primary_carrier_id in carriers else "primary carrier"

        recommendation_map = {
            Actions.ACCEPT: f"Tender {load.load_number} to {carrier_name} at ${decision.quantity:.0f}",
            Actions.ESCALATE: f"Escalate {load.load_number} to broker routing (waterfall exhausted)",
        }
        recommendation = recommendation_map.get(
            decision.action,
            f"{ACTION_LABELS.get(decision.action, 'EVALUATE')} tender for {load.load_number}",
        )

        session.add(_build_agent_decision(
            tenant_id=tenant_id,
            decision_type=DecisionType.FREIGHT_PROCUREMENT,
            item_code=item_code,
            item_name=load.load_number,
            category=state.mode,
            issue_summary=(
                f"Tender {load.load_number}: attempt {state.tender_attempt}/{state.max_tender_attempts}, "
                f"{state.hours_to_tender_deadline:.0f}h to deadline, "
                f"contract ${state.contract_rate:.0f}"
            ),
            recommendation=recommendation,
            decision=decision,
            state=state,
            impact_value=decision.quantity,
            impact_description="Tender rate",
        ))
        created += 1
    print(f"  freight_procurement: {created} decisions created")
    return created


# ============================================================================
# 7. Broker Routing
# ============================================================================

def seed_broker_routing_decisions(session, tenant_id: int, config_id: int) -> int:
    loads = _fetch_loads(session, tenant_id, [LoadStatus.PLANNING, LoadStatus.TENDERED])
    carriers = _fetch_carriers(session, tenant_id)
    broker_carriers = [c for c in carriers if c.carrier_type and c.carrier_type.value in ("BROKER", "3PL")]

    if not loads:
        print("  broker_routing: no tendered loads — skipping")
        return 0

    # Build broker pool from available carriers
    broker_pool: List[Dict[str, Any]] = []
    for b in broker_carriers:
        broker_pool.append({
            "id": b.id,
            "name": b.name,
            "rate": 2400.0 + (b.id * 53) % 600,
            "reliability": 0.75 + (b.id % 4) * 0.05,
            "coverage_score": 0.8,
        })
    if not broker_pool:
        broker_pool = [
            {"id": 901, "name": "Generic Broker A", "rate": 2450.0, "reliability": 0.82, "coverage_score": 0.85},
            {"id": 902, "name": "Generic Broker B", "rate": 2580.0, "reliability": 0.88, "coverage_score": 0.80},
        ]

    created = 0
    # Use last third of tendered loads as "waterfall exhausted" candidates
    candidates = loads[-5:] if len(loads) >= 5 else loads
    target = min(8, len(candidates))
    for i, load in enumerate(candidates[:target]):
        item_code = f"BROKER-{load.load_number}"
        if _decision_exists(session, tenant_id, DecisionType.BROKER_ROUTING, item_code):
            continue

        state = BrokerRoutingState(
            load_id=load.id,
            lane_id=0,
            mode=str(load.mode.value) if load.mode else "FTL",
            tender_attempts_exhausted=3,
            all_contract_carriers_declined=True,
            hours_to_pickup=max(4.0, 36.0 - i * 4),
            available_brokers=broker_pool,
            contract_rate=float(load.total_cost or 1800.0),
            spot_rate=float(load.total_cost or 1800.0) * 1.18,
            broker_rate_premium_pct=round(0.15 + (i % 4) * 0.05, 2),
            budget_remaining=15000.0,
            shipment_priority=1 + (i % 5),
            is_customer_committed=(i % 2 == 0),
        )

        decision = compute_tms_decision("broker_routing", state)

        if decision.action == Actions.ACCEPT:
            recommendation = f"Route {load.load_number} via broker at ${decision.quantity:.0f}"
        else:
            recommendation = f"Escalate {load.load_number}: broker options exceed premium threshold"

        session.add(_build_agent_decision(
            tenant_id=tenant_id,
            decision_type=DecisionType.BROKER_ROUTING,
            item_code=item_code,
            item_name=load.load_number,
            category="broker",
            issue_summary=(
                f"Carrier waterfall exhausted on {load.load_number}: "
                f"{state.hours_to_pickup:.0f}h to pickup, priority P{state.shipment_priority}"
            ),
            recommendation=recommendation,
            decision=decision,
            state=state,
            impact_value=decision.quantity,
            impact_description="Broker rate",
        ))
        created += 1
    print(f"  broker_routing: {created} decisions created")
    return created


# ============================================================================
# 8. Dock Scheduling
# ============================================================================

def seed_dock_scheduling_decisions(session, tenant_id: int, config_id: int) -> int:
    appointments = _fetch_appointments(session, tenant_id)
    facility_configs = {fc.site_id: fc for fc in _fetch_facility_configs(session, tenant_id)}

    if not appointments:
        print("  dock_scheduling: no appointments — skipping")
        return 0

    created = 0
    target = min(10, len(appointments))
    for i, appt in enumerate(appointments[:target]):
        item_code = f"DOCK-{appt.id}"
        if _decision_exists(session, tenant_id, DecisionType.DOCK_SCHEDULING, item_code):
            continue

        fc = facility_configs.get(appt.site_id)
        total_doors = fc.total_dock_doors if fc and fc.total_dock_doors else 12
        # Synthesize current occupancy based on time-of-day variability
        occupied = int(total_doors * (0.4 + (i % 6) * 0.1))
        available = max(0, total_doors - occupied)
        queue = (i % 5)
        avg_dwell = 55.0 + (i % 4) * 15.0

        state = DockSchedulingState(
            facility_id=appt.site_id,
            appointment_id=appt.id,
            appointment_type=str(appt.appointment_type.value) if appt.appointment_type else "DELIVERY",
            total_dock_doors=total_doors,
            available_dock_doors=available,
            yard_spots_total=(fc.total_yard_spots if fc and fc.total_yard_spots else 40),
            yard_spots_available=(fc.total_yard_spots // 2) if fc and fc.total_yard_spots else 20,
            requested_time=appt.scheduled_start,
            earliest_available_slot=appt.scheduled_start,
            latest_acceptable_slot=appt.scheduled_end,
            appointments_in_window=1 + (i % 4),
            avg_dwell_time_minutes=avg_dwell,
            current_queue_depth=queue,
            shipment_priority=1 + (i % 5),
            is_live_load=(i % 2 == 0),
            estimated_load_time_minutes=float(fc.avg_load_time_minutes) if fc and fc.avg_load_time_minutes else 60.0,
            free_time_minutes=120.0,
            detention_rate_per_hour=75.0,
            carrier_avg_dwell_minutes=avg_dwell * 1.4,
        )

        decision = compute_tms_decision("dock_scheduling", state)
        site_name = _site_name(session, appt.site_id)

        recommendation_map = {
            Actions.ACCEPT: f"Confirm dock appointment at {site_name}",
            Actions.DEFER: f"Defer appointment at {site_name} to less congested window",
            Actions.MODIFY: f"Switch appointment at {site_name} to drop-trailer mode",
        }
        recommendation = recommendation_map.get(
            decision.action,
            f"{ACTION_LABELS.get(decision.action, 'REVIEW')} dock appointment at {site_name}",
        )

        session.add(_build_agent_decision(
            tenant_id=tenant_id,
            decision_type=DecisionType.DOCK_SCHEDULING,
            item_code=item_code,
            item_name=f"{site_name} appt {appt.id}",
            category=state.appointment_type,
            issue_summary=(
                f"Dock scheduling at {site_name}: "
                f"{state.utilization_pct()*100:.0f}% utilization, queue {queue}, "
                f"detention risk {state.detention_risk_score():.0%}"
            ),
            recommendation=recommendation,
            decision=decision,
            state=state,
            impact_value=float(queue),
            impact_description="Queue depth",
        ))
        created += 1
    print(f"  dock_scheduling: {created} decisions created")
    return created


# ============================================================================
# 9. Load Build
# ============================================================================

def seed_load_build_decisions(session, tenant_id: int, config_id: int) -> int:
    loads = _fetch_loads(session, tenant_id, [LoadStatus.PLANNING, LoadStatus.TENDERED, LoadStatus.IN_TRANSIT])
    if not loads:
        print("  load_build: no loads — skipping")
        return 0

    created = 0
    target = min(10, len(loads))
    for i, load in enumerate(loads[:target]):
        item_code = f"LOADBUILD-{load.load_number}"
        if _decision_exists(session, tenant_id, DecisionType.LOAD_BUILD, item_code):
            continue

        weight = float(load.total_weight or 22000.0)
        volume = float(load.total_volume or 1800.0)
        pallets = int(load.total_pallets or 10)

        state = LoadBuildState(
            shipment_ids=[load.id],
            lane_id=0,
            mode=str(load.mode.value) if load.mode else "FTL",
            equipment_type=str(load.equipment_type.value) if load.equipment_type else "DRY_VAN",
            max_weight=44000.0,
            max_volume=2700.0,
            max_pallets=26,
            total_weight=weight,
            total_volume=volume,
            total_pallets=pallets,
            shipment_count=max(1, (i % 4) + 1),
            has_hazmat_conflict=(i == 7),
            has_temp_conflict=(i == 3),
            has_destination_conflict=False,
            max_stops=3,
            earliest_pickup=load.planned_departure,
            latest_pickup=load.planned_departure + timedelta(hours=12) if load.planned_departure else None,
            consolidation_window_hours=24.0,
            ftl_rate=float(load.total_cost or 1800.0),
            ltl_rate_sum=float(load.total_cost or 1800.0) * 1.25,
            consolidation_savings=float(load.total_cost or 1800.0) * 0.22,
        )

        decision = compute_tms_decision("load_build", state)

        recommendation_map = {
            Actions.CONSOLIDATE: f"Consolidate {state.shipment_count} shipments onto {load.load_number}",
            Actions.SPLIT: f"Split {load.load_number}: exceeds equipment capacity",
            Actions.DEFER: f"Hold {load.load_number} within consolidation window",
            Actions.ACCEPT: f"Accept load plan for {load.load_number}",
            Actions.REJECT: f"Reject consolidation for {load.load_number}: compatibility conflict",
        }
        recommendation = recommendation_map.get(
            decision.action,
            f"{ACTION_LABELS.get(decision.action, 'EVALUATE')} load plan for {load.load_number}",
        )

        session.add(_build_agent_decision(
            tenant_id=tenant_id,
            decision_type=DecisionType.LOAD_BUILD,
            item_code=item_code,
            item_name=load.load_number,
            category="consolidation",
            issue_summary=(
                f"Load build review for {load.load_number}: "
                f"weight {state.weight_utilization()*100:.0f}%, volume {state.volume_utilization()*100:.0f}%"
            ),
            recommendation=recommendation,
            decision=decision,
            state=state,
            impact_value=state.consolidation_savings,
            impact_description=f"${state.consolidation_savings:.0f} savings opportunity",
        ))
        created += 1
    print(f"  load_build: {created} decisions created")
    return created


# ============================================================================
# 10. Intermodal Transfer
# ============================================================================

def seed_intermodal_transfer_decisions(session, tenant_id: int, config_id: int) -> int:
    shipments = _fetch_shipments(session, tenant_id)
    # Prefer shipments not yet delivered for intermodal evaluation
    candidates = [s for s in shipments if s.status and s.status.value not in ("DELIVERED", "POD_RECEIVED", "CLOSED")]
    if not candidates:
        candidates = shipments[:]
    if not candidates:
        print("  intermodal_transfer: no shipments — skipping")
        return 0

    created = 0
    target = min(10, len(candidates))
    for i, shipment in enumerate(candidates[:target]):
        item_code = f"INTERMODAL-{shipment.shipment_number}"
        if _decision_exists(session, tenant_id, DecisionType.INTERMODAL_TRANSFER, item_code):
            continue

        truck_miles = 900 + (i * 137) % 1200
        truck_rate = 2.25 * truck_miles
        intermodal_rate = truck_rate * (0.75 + (i % 5) * 0.03)

        state = IntermodalTransferState(
            shipment_id=shipment.id,
            current_mode=str(shipment.mode.value) if shipment.mode else "FTL",
            candidate_mode="RAIL_INTERMODAL",
            origin_to_ramp_miles=50.0 + (i % 4) * 20,
            ramp_to_ramp_miles=truck_miles * 0.85,
            ramp_to_dest_miles=60.0 + (i % 4) * 25,
            total_truck_miles=float(truck_miles),
            truck_rate=truck_rate,
            intermodal_rate=intermodal_rate,
            drayage_rate_origin=250.0,
            drayage_rate_dest=280.0,
            truck_transit_days=round(truck_miles / 500.0, 1),
            intermodal_transit_days=round(truck_miles / 500.0, 1) + 1.5,
            delivery_window_days=float(2 + (i % 4)),
            rail_capacity_available=True,
            ramp_congestion_level=round(0.1 + (i % 6) * 0.15, 2),
            intermodal_reliability_pct=round(0.80 + (i % 4) * 0.04, 2),
            weather_risk_score=round((i % 5) * 0.15, 2),
        )

        decision = compute_tms_decision("intermodal_transfer", state)

        if decision.action == Actions.ACCEPT:
            recommendation = (
                f"Shift {shipment.shipment_number} from {state.current_mode} to {state.candidate_mode}: "
                f"{state.cost_savings_pct()*100:.0f}% savings"
            )
        else:
            recommendation = f"Keep {shipment.shipment_number} on {state.current_mode}"

        session.add(_build_agent_decision(
            tenant_id=tenant_id,
            decision_type=DecisionType.INTERMODAL_TRANSFER,
            item_code=item_code,
            item_name=shipment.shipment_number,
            category="mode_shift",
            issue_summary=(
                f"Intermodal evaluation for {shipment.shipment_number}: "
                f"{truck_miles:.0f}mi, savings {state.cost_savings_pct()*100:.0f}%, "
                f"+{state.transit_time_penalty_days():.1f}d transit"
            ),
            recommendation=recommendation,
            decision=decision,
            state=state,
            impact_value=truck_rate - intermodal_rate,
            impact_description=f"${truck_rate - intermodal_rate:.0f} potential savings",
        ))
        created += 1
    print(f"  intermodal_transfer: {created} decisions created")
    return created


# ============================================================================
# 11. Equipment Reposition
# ============================================================================

def seed_equipment_reposition_decisions(session, tenant_id: int, config_id: int) -> int:
    sites = _fetch_sites(session, config_id)
    if len(sites) < 2:
        print("  equipment_reposition: need >=2 sites — skipping")
        return 0

    created = 0
    equipment_types = ["DRY_VAN", "REEFER", "CONTAINER_40HC", "FLATBED"]

    # Pair sites to create reposition candidates
    pairs = []
    for i in range(len(sites)):
        for j in range(len(sites)):
            if i != j:
                pairs.append((sites[i], sites[j]))

    target = min(10, len(pairs))
    for i, (source, target_site) in enumerate(pairs[:target]):
        item_code = f"REPO-{source.id}-{target_site.id}"
        if _decision_exists(session, tenant_id, DecisionType.EQUIPMENT_REPOSITION, item_code):
            continue

        eq_type = equipment_types[i % len(equipment_types)]
        # Compute rough great-circle miles from lat/lon
        try:
            from math import radians, sin, cos, asin, sqrt
            lat1, lon1 = radians(source.latitude or 0), radians(source.longitude or 0)
            lat2, lon2 = radians(target_site.latitude or 0), radians(target_site.longitude or 0)
            dlat, dlon = lat2 - lat1, lon2 - lon1
            a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
            miles = 3959.0 * 2 * asin(sqrt(a))
        except Exception:
            miles = 300.0 + (i * 47) % 700

        source_count = 8 + (i % 6)
        source_demand = 3 + (i % 4)
        target_count = 1 + (i % 3)
        target_demand = 6 + (i % 5)
        reposition_cost = miles * 1.8

        state = EquipmentRepositionState(
            equipment_type=eq_type,
            source_facility_id=source.id,
            source_equipment_count=source_count,
            source_demand_next_7d=source_demand,
            target_facility_id=target_site.id,
            target_equipment_count=target_count,
            target_demand_next_7d=target_demand,
            reposition_miles=float(miles),
            reposition_cost=reposition_cost,
            reposition_transit_hours=miles / 50.0,
            network_surplus_locations=3,
            network_deficit_locations=2,
            total_fleet_size=120,
            fleet_utilization_pct=round(0.75 + (i % 4) * 0.05, 2),
            cost_of_not_repositioning=reposition_cost * (1.5 + (i % 4) * 0.3),
            breakeven_loads=max(1, 3 - (i % 3)),
        )

        decision = compute_tms_decision("equipment_reposition", state)

        if decision.action == Actions.REPOSITION:
            recommendation = (
                f"Reposition {int(decision.quantity)} {eq_type} unit(s) "
                f"from {source.name} to {target_site.name} ({miles:.0f}mi)"
            )
        else:
            recommendation = (
                f"Hold {eq_type} equipment at {source.name}: reposition ROI "
                f"{state.reposition_roi():.1f}x below threshold"
            )

        session.add(_build_agent_decision(
            tenant_id=tenant_id,
            decision_type=DecisionType.EQUIPMENT_REPOSITION,
            item_code=item_code,
            item_name=f"{eq_type} {source.name} -> {target_site.name}",
            category=eq_type,
            issue_summary=(
                f"Equipment reposition: surplus {state.source_surplus()} at {source.name}, "
                f"deficit {state.target_deficit()} at {target_site.name}, ROI {state.reposition_roi():.1f}x"
            ),
            recommendation=recommendation,
            decision=decision,
            state=state,
            impact_value=state.cost_of_not_repositioning - state.reposition_cost,
            impact_description="Net benefit",
        ))
        created += 1
    print(f"  equipment_reposition: {created} decisions created")
    return created


# ============================================================================
# Main
# ============================================================================

def seed_all(tenant_id: int, config_id: int) -> None:
    random.seed(42)
    session = sync_session_factory()
    total = 0
    try:
        print(f"Seeding TMS decisions for tenant_id={tenant_id}, config_id={config_id}")
        total += seed_capacity_promise_decisions(session, tenant_id, config_id)
        total += seed_shipment_tracking_decisions(session, tenant_id, config_id)
        total += seed_demand_sensing_decisions(session, tenant_id, config_id)
        total += seed_capacity_buffer_decisions(session, tenant_id, config_id)
        total += seed_exception_management_decisions(session, tenant_id, config_id)
        total += seed_freight_procurement_decisions(session, tenant_id, config_id)
        total += seed_broker_routing_decisions(session, tenant_id, config_id)
        total += seed_dock_scheduling_decisions(session, tenant_id, config_id)
        total += seed_load_build_decisions(session, tenant_id, config_id)
        total += seed_intermodal_transfer_decisions(session, tenant_id, config_id)
        total += seed_equipment_reposition_decisions(session, tenant_id, config_id)
        session.commit()
        print(f"\nTMS decisions seeded successfully: {total} total for tenant {tenant_id}, config {config_id}")
    except Exception as e:
        session.rollback()
        print(f"\nError seeding TMS decisions: {e}")
        raise
    finally:
        session.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Seed TMS AgentDecision records for a tenant")
    parser.add_argument('--tenant-id', type=int, default=1, help="Tenant ID")
    parser.add_argument('--config-id', type=int, default=1, help="Supply chain config ID")
    args = parser.parse_args()
    seed_all(args.tenant_id, args.config_id)
