"""
SiteAgent API Endpoints

REST API for SiteAgent operations:
- ATP checking with TRM adjustments
- Replenishment planning
- CDC status and triggers
- Model management
"""

from datetime import date, datetime
from typing import Optional, List, Dict, Any, Literal
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.db.session import get_db
from app.api.deps import get_current_user
from app.core.clock import tenant_today
from app.models.user import User
from app.services.decision_governance_service import DecisionGovernanceService
from app.models.decision_governance import DecisionGovernancePolicy, GuardrailDirective
from app.services.powell import (
    SiteAgent,
    SiteAgentConfig,
    CDCMonitor,
    CDCConfig,
    SiteMetrics,
    TriggerReason,
    ReplanAction,
    Priority,
)
from app.services.powell.engines import (
    MRPConfig,
    AATPConfig,
    SafetyStockConfig,
    Order as EngineOrder,
    GrossRequirement,
)

router = APIRouter(prefix="/site-agent", tags=["site-agent"])


# ----- Request/Response Models -----

class ATPCheckRequest(BaseModel):
    """Request to check ATP availability"""
    order_id: str
    product_id: str
    location_id: str
    requested_qty: float
    requested_date: date
    priority: int = Field(default=3, ge=1, le=5)
    customer_id: str = "DEFAULT"
    order_type: str = "standard"


class ATPCheckResponse(BaseModel):
    """Response from ATP check with optional conformal prediction intervals"""
    order_id: str
    can_fulfill: bool
    promised_qty: float
    shortage_qty: float
    promise_date: date
    source: str  # "deterministic", "trm_adjusted"
    confidence: float
    explanation: str
    consumption_detail: Optional[List[Dict]] = None
    # Conformal Decision Theory (CDT) risk bound
    risk_bound: Optional[float] = Field(
        None, description="P(loss > threshold) from CDT wrapper. Lower is safer."
    )
    risk_assessment: Optional[Dict[str, Any]] = Field(
        None, description="Full CDT diagnostic: threshold, calibration_size, interval_width"
    )
    # Demand conformal interval for the order's product-location
    demand_interval: Optional[Dict[str, Any]] = Field(
        None, description="Conformal demand interval: {lower, upper, point, coverage, method}"
    )


class ReplenishmentRequest(BaseModel):
    """Request for replenishment planning"""
    site_key: str
    planning_horizon_days: int = 90
    include_trm_adjustments: bool = True


class ReplenishmentResponse(BaseModel):
    """Response from replenishment planning"""
    site_key: str
    recommendations: List[Dict]
    total_orders: int
    expedited_orders: int
    generated_at: datetime


class CDCStatusResponse(BaseModel):
    """CDC monitor status"""
    site_key: str
    enabled: bool
    last_check: Optional[datetime]
    last_trigger: Optional[datetime]
    backlog_growth_days: int
    thresholds: Dict[str, float]
    recent_triggers: List[Dict]


class CDCTriggerRequest(BaseModel):
    """Manual CDC trigger request"""
    site_key: str
    reason: str = "manual"


class CDCTriggerResponse(BaseModel):
    """CDC trigger result"""
    triggered: bool
    reasons: List[str]
    recommended_action: str
    severity: str
    message: str


class SiteMetricsInput(BaseModel):
    """Current site metrics for CDC check"""
    demand_cumulative: float
    forecast_cumulative: float
    inventory_on_hand: float
    inventory_target: float
    service_level: float
    target_service_level: float = 0.95
    avg_lead_time_actual: float
    avg_lead_time_expected: float
    supplier_on_time_rate: float = 0.95
    backlog_units: float = 0
    backlog_yesterday: float = 0


class LoadAllocationsRequest(BaseModel):
    """Request to load allocations from tGNN"""
    site_key: str
    allocations: List[Dict]  # [{product_id, location_id, priority, allocated_qty}]


class SiteAgentStatusResponse(BaseModel):
    """Overall site agent status"""
    site_key: str
    agent_mode: str
    use_trm: bool
    model_loaded: bool
    model_checkpoint: Optional[str]
    param_counts: Optional[Dict]
    cdc_enabled: bool
    allocation_summary: Dict


# ----- Site Agent Cache -----

_site_agents: Dict[str, SiteAgent] = {}


def get_site_agent(
    site_key: str,
    db: Session,
    use_trm: bool = True
) -> SiteAgent:
    """Get or create SiteAgent for site"""
    cache_key = f"{site_key}:{use_trm}"

    if cache_key not in _site_agents:
        config = SiteAgentConfig(
            site_key=site_key,
            use_trm_adjustments=use_trm,
            agent_mode="copilot",
        )
        _site_agents[cache_key] = SiteAgent(config, db)

    return _site_agents[cache_key]


# ----- ATP Endpoints -----

@router.post("/atp/check", response_model=ATPCheckResponse)
async def check_atp(
    request: ATPCheckRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Check Available-to-Promise for an order.

    Uses deterministic AATP engine with optional TRM adjustments
    for exception handling (partial fill, substitute, etc.).
    """
    site_agent = get_site_agent(request.location_id, db)

    order = EngineOrder(
        order_id=request.order_id,
        product_id=request.product_id,
        location_id=request.location_id,
        requested_qty=request.requested_qty,
        requested_date=request.requested_date,
        priority=Priority.from_value(request.priority),
        customer_id=request.customer_id,
        order_type=request.order_type,
    )

    result = await site_agent.execute_atp(order)

    # Get demand conformal interval (optional enrichment)
    demand_interval = None
    try:
        from app.services.conformal_prediction.suite import get_conformal_suite
        suite = get_conformal_suite()
        location_int = int(request.location_id) if request.location_id else None
        if location_int and suite.has_demand_predictor(request.product_id, location_int):
            interval = suite.predict_demand(
                request.product_id, location_int, request.requested_qty
            )
            demand_interval = {
                "lower": interval.lower,
                "upper": interval.upper,
                "point": interval.point_estimate,
                "coverage": interval.coverage_target,
                "method": interval.method,
            }
    except Exception:
        pass  # Conformal enrichment is optional

    return ATPCheckResponse(
        order_id=result.order_id,
        can_fulfill=result.promised_qty >= request.requested_qty,
        promised_qty=result.promised_qty,
        shortage_qty=max(0, request.requested_qty - result.promised_qty),
        promise_date=result.promise_date,
        source=result.source,
        confidence=result.confidence,
        explanation=result.explanation,
        risk_bound=getattr(result, 'risk_bound', None),
        risk_assessment=getattr(result, 'risk_assessment', None),
        demand_interval=demand_interval,
    )


@router.post("/atp/batch", response_model=List[ATPCheckResponse])
async def check_atp_batch(
    requests: List[ATPCheckRequest],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Check ATP for multiple orders"""
    results = []

    for req in requests:
        site_agent = get_site_agent(req.location_id, db)

        order = EngineOrder(
            order_id=req.order_id,
            product_id=req.product_id,
            location_id=req.location_id,
            requested_qty=req.requested_qty,
            requested_date=req.requested_date,
            priority=Priority.from_value(req.priority),
            customer_id=req.customer_id,
            order_type=req.order_type,
        )

        result = await site_agent.execute_atp(order)

        # Get demand conformal interval (optional enrichment)
        demand_interval = None
        try:
            from app.services.conformal_prediction.suite import get_conformal_suite
            suite = get_conformal_suite()
            location_int = int(req.location_id) if req.location_id else None
            if location_int and suite.has_demand_predictor(req.product_id, location_int):
                interval = suite.predict_demand(
                    req.product_id, location_int, req.requested_qty
                )
                demand_interval = {
                    "lower": interval.lower,
                    "upper": interval.upper,
                    "point": interval.point_estimate,
                    "coverage": interval.coverage_target,
                    "method": interval.method,
                }
        except Exception:
            pass

        results.append(ATPCheckResponse(
            order_id=result.order_id,
            can_fulfill=result.promised_qty >= req.requested_qty,
            promised_qty=result.promised_qty,
            shortage_qty=max(0, req.requested_qty - result.promised_qty),
            promise_date=result.promise_date,
            source=result.source,
            confidence=result.confidence,
            explanation=result.explanation,
            risk_bound=getattr(result, 'risk_bound', None),
            risk_assessment=getattr(result, 'risk_assessment', None),
            demand_interval=demand_interval,
        ))

    return results


@router.post("/allocations/load")
async def load_allocations(
    request: LoadAllocationsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Load allocations from tGNN into AATP engine"""
    from app.services.powell.engines import ATPAllocation, Priority

    site_agent = get_site_agent(request.site_key, db)

    today = await tenant_today(current_user.tenant_id, db)
    allocations = [
        ATPAllocation(
            product_id=a['product_id'],
            location_id=a.get('location_id', request.site_key),
            priority=Priority.from_value(a.get('priority', 3)),
            allocated_qty=a['allocated_qty'],
            period_start=today,
            period_end=today,
        )
        for a in request.allocations
    ]

    site_agent.aatp_engine.load_allocations(allocations)

    return {
        "status": "success",
        "site_key": request.site_key,
        "allocations_loaded": len(allocations),
        "summary": site_agent.aatp_engine.get_allocation_summary()
    }


# ----- Replenishment Endpoints -----

@router.post("/replenishment/plan", response_model=ReplenishmentResponse)
async def plan_replenishment(
    request: ReplenishmentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate replenishment plan using MRP engine with TRM adjustments.
    """
    site_agent = get_site_agent(request.site_key, db, request.include_trm_adjustments)

    # Load supply plan recommendations from database
    from app.models.sc_entities import SupplyPlan
    from app.models.supply_chain_config import Site
    plans = db.query(SupplyPlan).join(
        Site, SupplyPlan.site_id == Site.id
    ).filter(Site.name == request.site_key).order_by(
        SupplyPlan.planned_order_date
    ).limit(50).all()
    recs = []
    for p in plans:
        recs.append({
            "plan_type": p.plan_type,
            "product_id": p.product_id,
            "quantity": p.planned_order_quantity,
            "date": p.planned_order_date.isoformat() if p.planned_order_date else None,
            "supplier_id": p.supplier_id,
            "urgency": "normal",
        })
    return ReplenishmentResponse(
        site_key=request.site_key,
        recommendations=recs,
        total_orders=len(recs),
        expedited_orders=0,
        generated_at=datetime.utcnow(),
    )


# ----- CDC Endpoints -----

@router.get("/cdc/status/{site_key}", response_model=CDCStatusResponse)
async def get_cdc_status(
    site_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get CDC monitor status for a site"""
    from app.models.powell_decision import CDCTriggerLog
    from sqlalchemy import desc

    site_agent = get_site_agent(site_key, db)
    status = site_agent.cdc_monitor.get_status()

    # Load recent triggers from database
    trigger_rows = (
        db.query(CDCTriggerLog)
        .filter(CDCTriggerLog.site_key == site_key)
        .order_by(desc(CDCTriggerLog.timestamp))
        .limit(20)
        .all()
    )
    recent_triggers = [
        {
            "id": t.id,
            "timestamp": t.timestamp.isoformat() if t.timestamp else None,
            "triggered": t.triggered,
            "reasons": t.reasons or [],
            "severity": t.severity,
            "recommended_action": t.recommended_action,
            "action_taken": t.action_taken,
            "replan_completed": t.replan_completed,
            "threshold_breaches": t.threshold_breaches or {},
        }
        for t in trigger_rows
    ]

    # Determine last_check from most recent trigger entry
    last_check = trigger_rows[0].timestamp if trigger_rows else None
    # Determine last_trigger from most recent actual trigger
    last_trigger_row = next((t for t in trigger_rows if t.triggered), None)
    last_trigger_time = last_trigger_row.timestamp if last_trigger_row else None

    return CDCStatusResponse(
        site_key=site_key,
        enabled=status['enabled'],
        last_check=last_check,
        last_trigger=last_trigger_time,
        backlog_growth_days=status['backlog_growth_days'],
        thresholds=status['thresholds'],
        recent_triggers=recent_triggers,
    )


@router.post("/cdc/check", response_model=CDCTriggerResponse)
async def check_cdc_trigger(
    site_key: str,
    metrics: SiteMetricsInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Check if CDC thresholds are exceeded.

    Provide current site metrics to check against configured thresholds.
    """
    site_agent = get_site_agent(site_key, db)

    site_metrics = SiteMetrics(
        site_key=site_key,
        timestamp=datetime.utcnow(),
        demand_cumulative=metrics.demand_cumulative,
        forecast_cumulative=metrics.forecast_cumulative,
        inventory_on_hand=metrics.inventory_on_hand,
        inventory_target=metrics.inventory_target,
        service_level=metrics.service_level,
        target_service_level=metrics.target_service_level,
        avg_lead_time_actual=metrics.avg_lead_time_actual,
        avg_lead_time_expected=metrics.avg_lead_time_expected,
        supplier_on_time_rate=metrics.supplier_on_time_rate,
        backlog_units=metrics.backlog_units,
        backlog_yesterday=metrics.backlog_yesterday,
    )

    trigger = await site_agent.check_cdc_trigger(site_metrics)

    return CDCTriggerResponse(
        triggered=trigger.triggered,
        reasons=[r.value for r in trigger.reasons],
        recommended_action=trigger.recommended_action.value,
        severity=trigger.severity,
        message=trigger.message,
    )


@router.post("/cdc/trigger-manual", response_model=CDCTriggerResponse)
async def trigger_cdc_manual(
    request: CDCTriggerRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually trigger CDC replanning.

    Use this to force an out-of-cadence planning run.
    """
    # Trigger replanning via CDCRetrainingService
    from app.services.powell.cdc_retraining_service import CDCRetrainingService
    from app.models.powell_decision import CDCTriggerLog
    site_agent = get_site_agent(request.site_key, db)
    # Log the trigger
    trigger = CDCTriggerLog(
        site_key=request.site_key,
        trigger_type="manual",
        reason=f"Manual trigger by {current_user.email}",
        severity=request.severity or "medium",
        recommended_action="full_cfa",
    )
    db.add(trigger)
    db.commit()
    return CDCTriggerResponse(
        triggered=True,
        reasons=["manual", f"initiated by {current_user.email}"],
        recommended_action="full_cfa",
        severity=request.severity or "medium",
        message=f"CDC replanning triggered for {request.site_key}. Trigger logged.",
    )


@router.put("/cdc/thresholds/{site_key}")
async def update_cdc_thresholds(
    site_key: str,
    thresholds: Dict[str, float],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update CDC thresholds for a site"""
    site_agent = get_site_agent(site_key, db)

    # Update thresholds
    for key, value in thresholds.items():
        if key in site_agent.cdc_monitor.config.thresholds:
            site_agent.cdc_monitor.config.thresholds[key] = value

    # Persist to database
    from app.models.powell_decision import CDCThreshold
    for key, value in thresholds.items():
        existing = db.query(CDCThreshold).filter(
            CDCThreshold.site_key == site_key, CDCThreshold.metric_name == key
        ).first()
        if existing:
            existing.threshold_value = value
        else:
            db.add(CDCThreshold(site_key=site_key, metric_name=key, threshold_value=value))
    db.commit()

    return {
        "status": "success",
        "site_key": site_key,
        "thresholds": site_agent.cdc_monitor.config.thresholds,
    }


# ----- Inventory Adjustment Endpoints -----

@router.get("/inventory/adjustments/{site_key}")
async def get_inventory_adjustments(
    site_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get TRM-suggested inventory parameter adjustments"""
    site_agent = get_site_agent(site_key, db)
    adjustments = await site_agent.get_inventory_adjustments()

    return {
        "site_key": site_key,
        "adjustments": adjustments,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ----- Status Endpoints -----

@router.get("/status/{site_key}", response_model=SiteAgentStatusResponse)
async def get_site_agent_status(
    site_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get overall SiteAgent status"""
    site_agent = get_site_agent(site_key, db)
    status = site_agent.get_status()

    param_counts = None
    if site_agent.model:
        param_counts = site_agent.model.get_parameter_count()

    return SiteAgentStatusResponse(
        site_key=site_key,
        agent_mode=status['agent_mode'],
        use_trm=status['use_trm'],
        model_loaded=status['model_loaded'],
        model_checkpoint=site_agent.config.model_checkpoint_path,
        param_counts=param_counts,
        cdc_enabled=status['cdc_status']['enabled'],
        allocation_summary=status['allocations_summary'],
    )


@router.get("/status")
async def list_site_agents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all active site agents"""
    return {
        "active_agents": list(_site_agents.keys()),
        "count": len(_site_agents),
    }


@router.delete("/cache/{site_key}")
async def clear_site_agent_cache(
    site_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Clear cached site agent (forces reload)"""
    removed = []
    for key in list(_site_agents.keys()):
        if key.startswith(site_key):
            del _site_agents[key]
            removed.append(key)

    return {
        "status": "success",
        "removed": removed,
    }


# ----- CDC Trigger History & Retraining Endpoints -----

@router.get("/cdc/triggers/{site_key}")
async def get_cdc_triggers(
    site_key: str,
    limit: int = Query(default=50, ge=1, le=200),
    triggered_only: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get CDC trigger history for a site.

    Returns recent trigger events from powell_cdc_trigger_log.
    """
    from app.models.powell_decision import CDCTriggerLog
    from sqlalchemy import desc

    query = db.query(CDCTriggerLog).filter(CDCTriggerLog.site_key == site_key)
    if triggered_only:
        query = query.filter(CDCTriggerLog.triggered == True)

    rows = query.order_by(desc(CDCTriggerLog.timestamp)).limit(limit).all()

    return {
        "site_key": site_key,
        "total": len(rows),
        "triggers": [
            {
                "id": t.id,
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
                "triggered": t.triggered,
                "reasons": t.reasons or [],
                "severity": t.severity,
                "recommended_action": t.recommended_action,
                "action_taken": t.action_taken,
                "replan_completed": t.replan_completed,
                "threshold_breaches": t.threshold_breaches or {},
                "metrics_snapshot": t.metrics_snapshot or {},
            }
            for t in rows
        ],
    }


@router.get("/retraining/status/{site_key}")
async def get_retraining_status(
    site_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get retraining status for a site.

    Returns latest checkpoint info, pending experience count,
    and readiness indicator.
    """
    from app.models.powell_decision import SiteAgentCheckpoint, SiteAgentDecision, CDCTriggerLog
    from sqlalchemy import desc, func
    from datetime import timedelta

    now = datetime.utcnow()

    # Latest active checkpoint
    checkpoint = (
        db.query(SiteAgentCheckpoint)
        .filter(
            SiteAgentCheckpoint.site_key == site_key,
            SiteAgentCheckpoint.is_active == True,
        )
        .order_by(desc(SiteAgentCheckpoint.created_at))
        .first()
    )

    # Count decisions with outcomes (potential training data)
    total_with_outcomes = (
        db.query(func.count(SiteAgentDecision.decision_id))
        .filter(
            SiteAgentDecision.site_key == site_key,
            SiteAgentDecision.actual_outcome.isnot(None),
        )
        .scalar() or 0
    )

    # Count decisions with outcomes since last checkpoint
    last_checkpoint_time = checkpoint.created_at if checkpoint else datetime.min
    pending_experiences = (
        db.query(func.count(SiteAgentDecision.decision_id))
        .filter(
            SiteAgentDecision.site_key == site_key,
            SiteAgentDecision.actual_outcome.isnot(None),
            SiteAgentDecision.timestamp > last_checkpoint_time,
        )
        .scalar() or 0
    )

    # Count decisions awaiting outcomes
    awaiting_outcomes = (
        db.query(func.count(SiteAgentDecision.decision_id))
        .filter(
            SiteAgentDecision.site_key == site_key,
            SiteAgentDecision.actual_outcome.is_(None),
            SiteAgentDecision.timestamp > now - timedelta(days=30),
        )
        .scalar() or 0
    )

    # Recent CDC triggers
    recent_trigger_count = (
        db.query(func.count(CDCTriggerLog.id))
        .filter(
            CDCTriggerLog.site_key == site_key,
            CDCTriggerLog.triggered == True,
            CDCTriggerLog.timestamp > now - timedelta(hours=24),
        )
        .scalar() or 0
    )

    # Determine readiness: green (ready), orange (almost), red (not ready)
    min_experiences = 100
    cooldown_ok = (
        not checkpoint
        or (now - checkpoint.created_at).total_seconds() > 6 * 3600
    )
    has_enough_data = pending_experiences >= min_experiences
    has_trigger = recent_trigger_count > 0

    if has_enough_data and cooldown_ok and has_trigger:
        readiness = "ready"
    elif has_enough_data and cooldown_ok:
        readiness = "waiting_for_trigger"
    elif pending_experiences >= min_experiences * 0.5:
        readiness = "collecting"
    else:
        readiness = "not_ready"

    return {
        "site_key": site_key,
        "checkpoint": {
            "id": checkpoint.checkpoint_id if checkpoint else None,
            "model_version": checkpoint.model_version if checkpoint else None,
            "training_loss": checkpoint.training_loss if checkpoint else None,
            "training_samples": checkpoint.training_samples if checkpoint else None,
            "training_phase": checkpoint.training_phase if checkpoint else None,
            "created_at": checkpoint.created_at.isoformat() if checkpoint else None,
            "is_validated": checkpoint.is_validated if checkpoint else None,
        } if checkpoint else None,
        "experiences": {
            "total_with_outcomes": total_with_outcomes,
            "pending_since_checkpoint": pending_experiences,
            "awaiting_outcomes": awaiting_outcomes,
            "min_required": min_experiences,
        },
        "recent_cdc_triggers": recent_trigger_count,
        "cooldown_ok": cooldown_ok,
        "readiness": readiness,
    }


@router.post("/retraining/trigger/{site_key}")
async def trigger_retraining(
    site_key: str,
    reason: Optional[str] = Query(None, description="Manual trigger reason"),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually trigger TRM retraining for a site.

    Creates a TriggerEvent with manual reason, logs it to
    powell_cdc_trigger_log, and runs retraining in background.
    Returns a pipeline_id for status polling.
    """
    import uuid
    from app.services.powell.cdc_retraining_service import CDCRetrainingService
    from app.services.powell.cdc_monitor import TriggerEvent

    pipeline_id = str(uuid.uuid4())

    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="User has no tenant assigned")

    svc = CDCRetrainingService(db=db, site_key=site_key, tenant_id=tenant_id)

    # Quick pre-check
    if not svc.evaluate_retraining_need(skip_trigger_check=True):
        return {
            "status": "skipped",
            "pipeline_id": pipeline_id,
            "site_key": site_key,
            "message": "Not enough training data or cooldown not elapsed",
        }

    # Create a manual TriggerEvent
    manual_trigger = TriggerEvent(
        triggered=True,
        reasons=[TriggerReason.DEMAND_DEVIATION],
        metrics_snapshot=SiteMetrics(
            site_key=site_key,
            timestamp=datetime.utcnow(),
            demand_cumulative=0.0,
            forecast_cumulative=0.0,
            inventory_on_hand=0.0,
            inventory_target=0.0,
            service_level=0.0,
            target_service_level=0.95,
            avg_lead_time_actual=0.0,
            avg_lead_time_expected=0.0,
            supplier_on_time_rate=1.0,
            backlog_units=0.0,
        ),
        recommended_action=ReplanAction.FULL_CFA,
        severity="medium",
        message=f"Manual trigger by {current_user.email}: {reason or 'no reason given'}",
    )

    # Log trigger event to DB
    try:
        from app.models.powell_decision import CDCTriggerLog

        trigger_log = CDCTriggerLog(
            site_key=site_key,
            triggered=True,
            reasons=["manual_trigger"],
            severity="medium",
            recommended_action=ReplanAction.FULL_CFA.value,
            metrics_snapshot={
                "site_key": site_key,
                "manual": True,
                "reason": reason or "manual_trigger",
                "triggered_by": current_user.email,
                "pipeline_id": pipeline_id,
            },
        )
        db.add(trigger_log)
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to log manual trigger: {e}")

    def _run_retraining():
        from app.db.session import SessionLocal
        bg_db = SessionLocal()
        try:
            bg_svc = CDCRetrainingService(db=bg_db, site_key=site_key, tenant_id=tenant_id)
            result = bg_svc.execute_retraining(trigger_event=manual_trigger)
            if result and result.final_loss < float("inf"):
                logger.info(
                    f"Manual retraining completed for {site_key} "
                    f"(pipeline={pipeline_id}): loss={result.final_loss:.4f}"
                )
            else:
                logger.warning(
                    f"Manual retraining for {site_key} "
                    f"(pipeline={pipeline_id}) produced no improvement"
                )
        except Exception as e:
            logger.error(f"Manual retraining failed for {site_key} (pipeline={pipeline_id}): {e}")
        finally:
            bg_db.close()

    background_tasks.add_task(_run_retraining)

    return {
        "status": "started",
        "pipeline_id": pipeline_id,
        "site_key": site_key,
        "message": f"Retraining triggered by {current_user.email}, running in background",
    }


# ============================================================================
# Hive Signal Bus & Decision Cycle Endpoints
# ============================================================================

@router.get("/hive/status/{site_key}")
async def get_hive_status(
    site_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get hive signal bus status, urgency vector, and signal divergence."""
    site_agent = get_site_agent(site_key, db)
    result: Dict[str, Any] = {
        "site_key": site_key,
        "hive_enabled": site_agent.signal_bus is not None,
    }

    if site_agent.signal_bus is None:
        result["urgency_vector"] = None
        result["signal_bus"] = None
        result["signal_divergence"] = 0.0
        result["hive_health"] = None
        return result

    bus = site_agent.signal_bus

    # Urgency vector as labelled dict
    uv = bus.urgency
    uv_values = uv.values_array()
    uv_snapshot = uv.snapshot()
    idx_to_name = {v: k for k, v in uv.TRM_INDICES.items()}
    result["urgency_vector"] = {
        "values": {idx_to_name[i]: round(v, 4) for i, v in enumerate(uv_values)},
        "directions": {
            idx_to_name[i]: d for i, d in enumerate(uv_snapshot["directions"])
        },
        "last_updated": {
            idx_to_name[i]: t for i, t in enumerate(uv_snapshot["last_updated"])
        },
    }

    # Signal bus stats
    result["signal_bus"] = bus.stats()
    result["signal_summary"] = bus.signal_summary()

    # Active signals (limited to 50 most recent)
    active = bus.active_signals()
    result["active_signals"] = [s.to_dict() for s in active[-50:]]

    # Signal divergence
    result["signal_divergence"] = getattr(
        site_agent.cdc_monitor, "_signal_divergence_score", 0.0
    )

    # Hive health metrics
    result["hive_health"] = site_agent.get_hive_health()

    # tGNN directive status
    directive = site_agent.get_current_directive()
    if directive is not None:
        result["directive"] = {
            "site_key": getattr(directive, "site_key", ""),
            "criticality_score": getattr(directive, "criticality_score", 0.0),
            "bottleneck_risk": getattr(directive, "bottleneck_risk", 0.0),
            "safety_stock_multiplier": getattr(directive, "safety_stock_multiplier", 1.0),
            "resilience_score": getattr(directive, "resilience_score", 0.0),
            "confidence": getattr(directive, "confidence", 0.0),
            "inter_hive_signal_count": len(getattr(directive, "inter_hive_signals", [])),
        }
    else:
        result["directive"] = None

    # Registered TRMs
    result["registered_trms"] = list(site_agent._registered_trms.keys())

    return result


@router.get("/hive/decision-cycle/{site_key}")
async def get_decision_cycle_info(
    site_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get decision cycle phase mapping and TRM assignments."""
    from app.services.powell.decision_cycle import PHASE_TRM_MAP, DecisionCyclePhase

    phases = []
    for phase in DecisionCyclePhase:
        trms = PHASE_TRM_MAP.get(phase, [])
        phases.append({
            "phase": phase.value,
            "name": phase.name,
            "trms": trms,
        })

    site_agent = get_site_agent(site_key, db)
    registered = list(site_agent._registered_trms.keys())

    return {
        "site_key": site_key,
        "phases": phases,
        "registered_trms": registered,
        "total_phases": len(phases),
    }


@router.post("/hive/decision-cycle/{site_key}/run")
async def run_decision_cycle(
    site_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Execute a decision cycle and return phase-by-phase results."""
    site_agent = get_site_agent(site_key, db)

    # Build executors from registered TRMs (if they're callable)
    executors = {}
    for name, trm in site_agent._registered_trms.items():
        if callable(trm):
            executors[name] = trm

    result = site_agent.execute_decision_cycle(trm_executors=executors)

    return {
        "site_key": site_key,
        "completed_at": result.completed_at.isoformat() if result.completed_at else None,
        "total_duration_ms": round(result.total_duration_ms, 2),
        "total_signals_emitted": result.total_signals_emitted,
        "conflicts_detected": result.conflicts_detected,
        "phases": [
            {
                "phase": p.phase.value,
                "name": p.phase.name,
                "trms_executed": p.trms_executed,
                "signals_emitted": p.signals_emitted,
                "duration_ms": round(p.duration_ms, 2),
                "errors": p.errors,
            }
            for p in result.phases
        ],
    }


# ============================================================================
# Directive Broadcast Endpoints (Inter-Hive Orchestration)
# ============================================================================

# Singleton broadcast service
_broadcast_service = None


def _get_broadcast_service():
    """Lazy-init the broadcast service with all registered SiteAgents."""
    global _broadcast_service
    if _broadcast_service is None:
        from app.services.powell.directive_broadcast_service import DirectiveBroadcastService
        _broadcast_service = DirectiveBroadcastService()
        # Wire in any cached site agents
        for key, agent in _site_agents.items():
            _broadcast_service.register_site(key, agent)
    return _broadcast_service


@router.get("/directive-broadcast/status")
async def get_broadcast_status(
    current_user: User = Depends(get_current_user),
):
    """Get directive broadcast service status."""
    svc = _get_broadcast_service()
    return svc.get_status()


@router.post("/directive-broadcast/run")
async def run_broadcast_cycle(
    gnn_outputs: Dict[str, Dict[str, Any]],
    network_topology: Optional[Dict[str, List[str]]] = None,
    current_user: User = Depends(get_current_user),
):
    """Run a full directive broadcast cycle.

    Body:
        gnn_outputs: Per-site tGNN output embeddings
            {"plant_a": {"criticality_score": 0.8, "bottleneck_risk": 0.3, ...}}
        network_topology: Optional DAG adjacency
            {"plant_a": ["dc_east", "dc_west"]}
    """
    svc = _get_broadcast_service()
    result = svc.run_cycle(gnn_outputs, network_topology)
    return result


@router.get("/directive-broadcast/feedback")
async def get_broadcast_feedback(
    current_user: User = Depends(get_current_user),
):
    """Collect hive feedback features from all registered sites."""
    svc = _get_broadcast_service()
    return svc.collect_feedback()


# ============================================================================
# GNN Orchestration — Full Inference → Directive Cycle
# ============================================================================

@router.post("/gnn/run-cycle")
async def run_gnn_orchestration_cycle(
    force_recompute: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """GNN orchestration cycle endpoint — **disabled** by GNN-1.

    Returns HTTP 410 Gone. The underlying ``GNNOrchestrationService``
    runs SCP-shape S&OP + Execution tGNN inference whose directives
    no TMS plane code consumes (per PR-5.E audit). The daily
    APScheduler cron driving this cycle was disabled
    [2026-05-05](../app/services/powell/relearning_jobs.py); this
    endpoint is the matching manual surface.

    Re-enable once the rewrite plan
    [TMS_POWELL_GNN_REWRITE.md](../../docs/TMS_POWELL_GNN_REWRITE.md)
    lands TMS-shape replacements (GNN-3 through GNN-6) and the
    broadcast has live consumers.
    """
    from fastapi import status as _http_status
    raise HTTPException(
        status_code=_http_status.HTTP_410_GONE,
        detail=(
            "GNN orchestration cycle disabled (GNN-1, 2026-05-11). "
            "Outputs are SCP-shape and have no TMS consumers; re-enable "
            "once TMS-shape replacements ship per TMS_POWELL_GNN_REWRITE.md."
        ),
    )

    # ── Dead path below — kept for the forensic / development case
    # when the endpoint is manually re-enabled by editing this file.
    from app.services.powell.gnn_orchestration_service import GNNOrchestrationService
    from app.models.supply_chain_config import SupplyChainConfig

    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="User has no tenant assigned")

    config = db.query(SupplyChainConfig).filter(
        SupplyChainConfig.tenant_id == tenant_id
    ).first()
    if not config:
        raise HTTPException(status_code=404, detail="No supply chain config found for tenant")

    orchestrator = GNNOrchestrationService(db, config.id)
    result = await orchestrator.run_full_cycle(force_recompute=force_recompute)

    # Enrich response with conformal prediction intervals from execution GNN output
    if "execution_output" in result and hasattr(result["execution_output"], "demand_interval"):
        exec_out = result["execution_output"]
        result["conformal_prediction"] = {
            "demand_interval": exec_out.demand_interval if exec_out.demand_interval else {},
            "allocation_interval": exec_out.allocation_interval if exec_out.allocation_interval else {},
        }

    return {
        "success": len(result.get("errors", [])) == 0,
        "data": result,
    }


@router.get("/gnn/status")
async def get_gnn_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get status of last GNN inference cycle and S&OP embeddings."""
    from app.services.powell.sop_inference_service import SOPInferenceService
    from app.models.supply_chain_config import SupplyChainConfig

    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="User has no tenant assigned")

    config = db.query(SupplyChainConfig).filter(
        SupplyChainConfig.tenant_id == tenant_id
    ).first()

    try:
        config_id = config.id if config else 0
        sop_svc = SOPInferenceService(db, config_id=config_id)
        embeddings = await sop_svc.get_embeddings_tensor()
        has_sop = embeddings is not None
    except Exception:
        has_sop = False

    broadcast_svc = _get_broadcast_service()
    broadcast_status = broadcast_svc.get_status()

    return {
        "success": True,
        "data": {
            "sop_embeddings_cached": has_sop,
            "broadcast_service": broadcast_status,
        },
    }


# ============================================================================
# GNN Directive Review — Human Override at Network Level
# ============================================================================

class GNNDirectiveReviewRequest(BaseModel):
    """Review (accept/inspect/override) a GNN-generated directive (AIIO model)."""
    action: str = Field(..., description="ACTIONED | INSPECTED | OVERRIDDEN")
    override_values: Optional[Dict[str, Any]] = Field(None, description="Override values (required if OVERRIDDEN)")
    reason_code: Optional[str] = Field(None, description="Override reason code")
    reason_text: Optional[str] = Field(None, description="Override reason text")


class PolicyEnvelopeOverrideRequest(BaseModel):
    """Override a single parameter in a PolicyEnvelope."""
    parameter_path: str = Field(..., description="e.g. 'safety_stock_targets.frozen'")
    override_value: Any = Field(..., description="New value for this parameter")
    reason_code: Optional[str] = None
    reason_text: Optional[str] = None


@router.get("/gnn/directives")
async def list_gnn_directives(
    status: Optional[str] = Query(None, description="Filter by status (INFORMED, ACTIONED, INSPECTED, OVERRIDDEN, etc.)"),
    site_key: Optional[str] = Query(None),
    directive_scope: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List GNN-generated directives with their review status.

    Used by the S&OP Director to see pending directives that need review.
    """
    from app.models.gnn_directive_review import GNNDirectiveReview

    query = db.query(GNNDirectiveReview).order_by(GNNDirectiveReview.created_at.desc())

    if status:
        # Map AIIO frontend names to DB values (backward compat)
        _aiio_to_db = {
            "INFORMED": "PROPOSED",
            "ACTIONED": "ACCEPTED",
            "INSPECTED": "INSPECTED",
            "OVERRIDDEN": "OVERRIDDEN",
        }
        db_status = _aiio_to_db.get(status.upper(), status)
        query = query.filter(GNNDirectiveReview.status == db_status)
    if site_key:
        query = query.filter(GNNDirectiveReview.site_key == site_key)
    if directive_scope:
        query = query.filter(GNNDirectiveReview.directive_scope == directive_scope)

    directives = query.limit(limit).all()

    return {
        "success": True,
        "data": [d.to_dict() for d in directives],
        "count": len(directives),
    }


@router.post("/gnn/directives/{directive_id}/review")
async def review_gnn_directive(
    directive_id: int,
    request: GNNDirectiveReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Review a GNN directive — Accept, Override, or Reject.

    For OVERRIDDEN: provide override_values with the same structure as proposed_values.
    The OutcomeCollector will later compute counterfactual comparison and feed
    the Bayesian posterior system.
    """
    from app.models.gnn_directive_review import GNNDirectiveReview

    directive = db.query(GNNDirectiveReview).filter(
        GNNDirectiveReview.id == directive_id,
    ).first()

    if not directive:
        raise HTTPException(status_code=404, detail=f"Directive {directive_id} not found")

    if directive.status not in ("PROPOSED", "AUTO_APPLIED", "INFORMED"):
        raise HTTPException(
            status_code=400,
            detail=f"Directive already reviewed (status={directive.status})",
        )

    action = request.action.upper()
    # Map AIIO action names to DB status values (backward compat)
    _action_to_db = {
        "ACTIONED": "ACCEPTED",
        "INSPECTED": "INSPECTED",
        "OVERRIDDEN": "OVERRIDDEN",
        # Backward compat: old names still accepted
        "ACCEPTED": "ACCEPTED",
        "REJECTED": "OVERRIDDEN",
    }
    db_action = _action_to_db.get(action)
    if db_action is None:
        raise HTTPException(status_code=400, detail="action must be ACTIONED, INSPECTED, or OVERRIDDEN")

    if db_action == "OVERRIDDEN" and not request.override_values:
        raise HTTPException(status_code=400, detail="override_values required when action is OVERRIDDEN")

    directive.status = db_action
    directive.reviewed_by = current_user.id
    directive.reviewed_at = datetime.utcnow()

    if db_action == "OVERRIDDEN":
        directive.override_values = request.override_values
        directive.override_reason_code = request.reason_code
        directive.override_reason_text = request.reason_text

    if db_action in ("ACCEPTED", "OVERRIDDEN"):
        directive.applied_at = datetime.utcnow()

    db.commit()

    return {
        "success": True,
        "directive_id": directive_id,
        "status": action,
        "message": f"Directive {action.lower()}. "
                   + ("Override effectiveness will be tracked after outcome measurement."
                      if action == "OVERRIDDEN" else ""),
    }


@router.get("/gnn/directives/{directive_id}/ask-why")
async def ask_why_gnn_directive(
    directive_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Explain why the GNN generated this directive for a specific site.

    Returns model inputs, confidence, and key factors that drove the recommendation.
    """
    from app.models.gnn_directive_review import GNNDirectiveReview

    directive = db.query(GNNDirectiveReview).filter(
        GNNDirectiveReview.id == directive_id,
    ).first()

    if not directive:
        raise HTTPException(status_code=404, detail=f"Directive {directive_id} not found")

    # Build explanation from proposed values and model metadata
    explanation = {
        "directive_id": directive.id,
        "site_key": directive.site_key,
        "scope": directive.directive_scope,
        "model_type": directive.model_type,
        "model_confidence": directive.model_confidence,
        "proposed_values": directive.proposed_values,
        "reasoning": [],
    }

    pv = directive.proposed_values or {}

    if directive.directive_scope == "sop_policy":
        explanation["reasoning"] = _explain_sop_policy(pv, directive.site_key)
    elif directive.directive_scope == "execution_directive":
        explanation["reasoning"] = _explain_execution_directive(pv, directive.site_key)
    elif directive.directive_scope == "allocation_refresh":
        explanation["reasoning"] = _explain_allocation_refresh(pv, directive.site_key)

    # Include outcome data if available
    if directive.override_delta is not None:
        explanation["outcome"] = {
            "proposed_outcome_metric": directive.proposed_outcome_metric,
            "actual_outcome_metric": directive.actual_outcome_metric,
            "override_delta": directive.override_delta,
            "override_classification": directive.override_classification,
        }

    return {"success": True, "data": explanation}


@router.get("/gnn/override-effectiveness")
async def get_gnn_override_effectiveness(
    days: int = Query(default=90, ge=7, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get override effectiveness metrics for GNN-level directives.

    Returns breakdown by directive scope (sop_policy, execution_directive, allocation_refresh).
    """
    from app.models.gnn_directive_review import GNNDirectiveReview
    from sqlalchemy import func, case
    from datetime import timedelta

    cutoff = datetime.utcnow() - timedelta(days=days)

    # Overridden directives
    base_q = db.query(GNNDirectiveReview).filter(
        GNNDirectiveReview.status == "OVERRIDDEN",
        GNNDirectiveReview.created_at > cutoff,
    )

    total = base_q.count()
    classified = base_q.filter(GNNDirectiveReview.override_classification.isnot(None))
    beneficial = classified.filter(GNNDirectiveReview.override_classification == "BENEFICIAL").count()
    neutral = classified.filter(GNNDirectiveReview.override_classification == "NEUTRAL").count()
    detrimental = classified.filter(GNNDirectiveReview.override_classification == "DETRIMENTAL").count()
    classified_total = beneficial + neutral + detrimental
    pending = total - classified_total

    effectiveness_rate = (beneficial / classified_total * 100) if classified_total > 0 else 0.0

    net_delta = db.query(func.sum(GNNDirectiveReview.override_delta)).filter(
        GNNDirectiveReview.status == "OVERRIDDEN",
        GNNDirectiveReview.override_delta.isnot(None),
        GNNDirectiveReview.created_at > cutoff,
    ).scalar() or 0.0

    # By scope breakdown
    scope_rows = db.query(
        GNNDirectiveReview.directive_scope,
        func.count(GNNDirectiveReview.id).label("count"),
        func.sum(case(
            (GNNDirectiveReview.override_classification == "BENEFICIAL", 1), else_=0
        )).label("beneficial"),
        func.sum(case(
            (GNNDirectiveReview.override_classification == "NEUTRAL", 1), else_=0
        )).label("neutral"),
        func.sum(case(
            (GNNDirectiveReview.override_classification == "DETRIMENTAL", 1), else_=0
        )).label("detrimental"),
        func.coalesce(func.sum(GNNDirectiveReview.override_delta), 0.0).label("net_delta"),
    ).filter(
        GNNDirectiveReview.status == "OVERRIDDEN",
        GNNDirectiveReview.created_at > cutoff,
    ).group_by(GNNDirectiveReview.directive_scope).all()

    by_scope = {}
    for row in scope_rows:
        ct = int(row.beneficial or 0) + int(row.neutral or 0) + int(row.detrimental or 0)
        by_scope[row.directive_scope] = {
            "total": row.count,
            "beneficial": int(row.beneficial or 0),
            "neutral": int(row.neutral or 0),
            "detrimental": int(row.detrimental or 0),
            "effectiveness_rate": (int(row.beneficial or 0) / ct * 100) if ct > 0 else 0.0,
            "net_delta": float(row.net_delta),
        }

    # All directives summary (not just overridden)
    all_directives = db.query(
        GNNDirectiveReview.status,
        func.count(GNNDirectiveReview.id),
    ).filter(
        GNNDirectiveReview.created_at > cutoff,
    ).group_by(GNNDirectiveReview.status).all()

    status_summary = {row[0]: row[1] for row in all_directives}

    return {
        "success": True,
        "data": {
            "total_overridden": total,
            "effectiveness_rate": effectiveness_rate,
            "net_reward_delta": float(net_delta),
            "beneficial_count": beneficial,
            "neutral_count": neutral,
            "detrimental_count": detrimental,
            "pending_count": pending,
            "by_scope": by_scope,
            "status_summary": status_summary,
        },
    }


@router.post("/gnn/policy-envelope/{envelope_id}/override-parameter")
async def override_policy_envelope_parameter(
    envelope_id: int,
    request: PolicyEnvelopeOverrideRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Override a single parameter in a PolicyEnvelope before it propagates downstream.

    Captures the override for Bayesian posterior tracking.
    """
    from app.models.planning_cascade import PolicyEnvelope
    from app.models.gnn_directive_review import PolicyEnvelopeOverride

    envelope = db.query(PolicyEnvelope).filter(PolicyEnvelope.id == envelope_id).first()
    if not envelope:
        raise HTTPException(status_code=404, detail=f"PolicyEnvelope {envelope_id} not found")

    # Navigate the parameter path to get original value
    parts = request.parameter_path.split(".")
    original = None
    if len(parts) == 2:
        field_name, key = parts
        field_data = getattr(envelope, field_name, None)
        if isinstance(field_data, dict):
            original = field_data.get(key)
    elif len(parts) == 1:
        original = getattr(envelope, parts[0], None)

    override = PolicyEnvelopeOverride(
        policy_envelope_id=envelope_id,
        config_id=envelope.config_id,
        parameter_path=request.parameter_path,
        original_value=original,
        override_value=request.override_value,
        override_by=current_user.id,
        reason_code=request.reason_code,
        reason_text=request.reason_text,
    )
    db.add(override)

    # Apply the override to the envelope
    if len(parts) == 2:
        field_name, key = parts
        field_data = getattr(envelope, field_name, None)
        if isinstance(field_data, dict):
            updated = dict(field_data)
            updated[key] = request.override_value
            setattr(envelope, field_name, updated)
    elif len(parts) == 1:
        setattr(envelope, parts[0], request.override_value)

    db.commit()

    return {
        "success": True,
        "override_id": override.id,
        "parameter_path": request.parameter_path,
        "original_value": original,
        "override_value": request.override_value,
        "message": "Parameter overridden. Effectiveness will be tracked after downstream outcomes are measured.",
    }


def _explain_sop_policy(pv: dict, site_key: str) -> List[Dict[str, str]]:
    """Build explanation reasons for S&OP policy directive."""
    reasons = []
    crit = pv.get("criticality_score", 0.5)
    if crit > 0.7:
        reasons.append({
            "factor": "High Criticality",
            "detail": f"Site {site_key} has criticality score {crit:.2f} (>0.7), indicating high network importance.",
        })
    bottleneck = pv.get("bottleneck_risk", 0.3)
    if bottleneck > 0.5:
        reasons.append({
            "factor": "Bottleneck Risk",
            "detail": f"Bottleneck risk of {bottleneck:.2f} — limited alternatives for this site's throughput.",
        })
    ssm = pv.get("safety_stock_multiplier", 1.0)
    if ssm != 1.0:
        direction = "increased" if ssm > 1.0 else "decreased"
        reasons.append({
            "factor": "Safety Stock Adjustment",
            "detail": f"Safety stock multiplier {direction} to {ssm:.2f}x based on network risk analysis.",
        })
    if not reasons:
        reasons.append({
            "factor": "Standard Parameters",
            "detail": f"Site {site_key} parameters within normal ranges. No elevated risk detected.",
        })
    return reasons


def _explain_execution_directive(pv: dict, site_key: str) -> List[Dict[str, str]]:
    """Build explanation reasons for execution directive."""
    reasons = []
    exc_prob = pv.get("exception_probability", 0.1)
    if exc_prob > 0.3:
        reasons.append({
            "factor": "High Exception Probability",
            "detail": f"Exception probability of {exc_prob:.1%} — model predicts elevated disruption risk.",
        })
    demand = pv.get("demand_forecast", [])
    if demand:
        avg = sum(demand) / len(demand) if demand else 0
        reasons.append({
            "factor": "Demand Forecast",
            "detail": f"Forecasted demand: {len(demand)}-period horizon, average {avg:.0f} units/period.",
        })
    confidence = pv.get("confidence", 0.5)
    reasons.append({
        "factor": "Model Confidence",
        "detail": f"Execution tGNN confidence: {confidence:.1%} for site {site_key}.",
    })
    return reasons


def _explain_allocation_refresh(pv: dict, site_key: str) -> List[Dict[str, str]]:
    """Build explanation reasons for allocation refresh."""
    reasons = []
    allocs = pv.get("priority_allocations", {})
    if allocs:
        reasons.append({
            "factor": "Priority Allocations",
            "detail": f"Updated allocations across {len(allocs)} priority tiers.",
        })
    version = pv.get("allocation_version")
    if version:
        reasons.append({
            "factor": "Allocation Version",
            "detail": f"Refresh version {version} — based on latest tGNN inference cycle.",
        })
    if not reasons:
        reasons.append({
            "factor": "Standard Refresh",
            "detail": "Periodic allocation refresh from tGNN inference cycle.",
        })
    return reasons


# ============================================================================
# Override Tracking & Effectiveness Endpoints
# ============================================================================

class OverrideRequest(BaseModel):
    """Record a human override of an agent decision."""
    override_value: Dict[str, Any] = Field(..., description="What the human chose instead")
    reason: Optional[str] = Field(None, description="Why they overrode")


class OverrideEffectivenessResponse(BaseModel):
    """Override effectiveness metrics for a site."""
    effectiveness_rate: float
    net_reward_delta: float
    total_overrides: int
    beneficial_count: int
    neutral_count: int
    detrimental_count: int
    pending_count: int
    by_trm_type: Dict[str, Dict[str, Any]]
    trend: List[Dict[str, Any]]


@router.post("/decisions/{decision_id}/override")
async def record_override(
    decision_id: str,
    request: OverrideRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Record a human override of a SiteAgent TRM decision.

    Sets is_overridden=True, stores the human's chosen value and reason.
    The OutcomeCollector will later compute counterfactual comparison.
    """
    from app.models.powell_decision import SiteAgentDecision

    decision = db.query(SiteAgentDecision).filter(
        SiteAgentDecision.decision_id == decision_id,
    ).first()

    if not decision:
        raise HTTPException(status_code=404, detail=f"Decision {decision_id} not found")

    decision.is_overridden = True
    decision.override_value = request.override_value
    decision.override_reason_text = request.reason
    decision.override_user_id = current_user.id
    decision.override_timestamp = datetime.utcnow()

    db.commit()

    return {
        "status": "success",
        "decision_id": decision_id,
        "is_overridden": True,
        "message": "Override recorded. Effectiveness will be computed after outcome collection.",
    }


@router.get("/override-effectiveness/{site_key}", response_model=OverrideEffectivenessResponse)
async def get_override_effectiveness(
    site_key: str,
    days: int = Query(default=90, ge=7, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get override effectiveness metrics for a site.

    Computes how often human overrides led to better outcomes
    than the agent's original recommendation.
    """
    from app.models.powell_decision import SiteAgentDecision
    from sqlalchemy import func, case
    from datetime import timedelta

    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)

    # Base query: overridden decisions for this site
    base_q = db.query(SiteAgentDecision).filter(
        SiteAgentDecision.site_key == site_key,
        SiteAgentDecision.is_overridden == True,
        SiteAgentDecision.timestamp > cutoff,
    )

    total_overrides = base_q.count()

    # Count by classification
    classified = base_q.filter(SiteAgentDecision.override_classification.isnot(None))
    beneficial = classified.filter(SiteAgentDecision.override_classification == "BENEFICIAL").count()
    neutral = classified.filter(SiteAgentDecision.override_classification == "NEUTRAL").count()
    detrimental = classified.filter(SiteAgentDecision.override_classification == "DETRIMENTAL").count()
    classified_total = beneficial + neutral + detrimental
    pending = total_overrides - classified_total

    effectiveness_rate = (beneficial / classified_total * 100) if classified_total > 0 else 0.0

    # Net reward delta
    net_delta_result = db.query(func.sum(SiteAgentDecision.override_delta)).filter(
        SiteAgentDecision.site_key == site_key,
        SiteAgentDecision.is_overridden == True,
        SiteAgentDecision.override_delta.isnot(None),
        SiteAgentDecision.timestamp > cutoff,
    ).scalar() or 0.0

    # By TRM type breakdown
    type_rows = db.query(
        SiteAgentDecision.decision_type,
        func.count(SiteAgentDecision.id).label("count"),
        func.sum(case(
            (SiteAgentDecision.override_classification == "BENEFICIAL", 1), else_=0
        )).label("beneficial"),
        func.sum(case(
            (SiteAgentDecision.override_classification == "NEUTRAL", 1), else_=0
        )).label("neutral"),
        func.sum(case(
            (SiteAgentDecision.override_classification == "DETRIMENTAL", 1), else_=0
        )).label("detrimental"),
        func.coalesce(func.sum(SiteAgentDecision.override_delta), 0.0).label("net_delta"),
    ).filter(
        SiteAgentDecision.site_key == site_key,
        SiteAgentDecision.is_overridden == True,
        SiteAgentDecision.timestamp > cutoff,
    ).group_by(SiteAgentDecision.decision_type).all()

    by_trm_type = {}
    for row in type_rows:
        ct = int(row.beneficial or 0) + int(row.neutral or 0) + int(row.detrimental or 0)
        by_trm_type[row.decision_type] = {
            "total": row.count,
            "beneficial": int(row.beneficial or 0),
            "neutral": int(row.neutral or 0),
            "detrimental": int(row.detrimental or 0),
            "effectiveness_rate": (int(row.beneficial or 0) / ct * 100) if ct > 0 else 0.0,
            "net_delta": float(row.net_delta),
        }

    # Weekly trend (last N weeks)
    from sqlalchemy import extract
    trend_rows = db.query(
        func.date_trunc("week", SiteAgentDecision.timestamp).label("week"),
        func.count(SiteAgentDecision.id).label("count"),
        func.sum(case(
            (SiteAgentDecision.override_classification == "BENEFICIAL", 1), else_=0
        )).label("beneficial"),
        func.sum(case(
            (SiteAgentDecision.override_classification == "NEUTRAL", 1), else_=0
        )).label("neutral"),
        func.sum(case(
            (SiteAgentDecision.override_classification == "DETRIMENTAL", 1), else_=0
        )).label("detrimental"),
    ).filter(
        SiteAgentDecision.site_key == site_key,
        SiteAgentDecision.is_overridden == True,
        SiteAgentDecision.timestamp > cutoff,
    ).group_by("week").order_by("week").all()

    trend = []
    for row in trend_rows:
        ct = int(row.beneficial or 0) + int(row.neutral or 0) + int(row.detrimental or 0)
        trend.append({
            "week": row.week.isoformat() if row.week else None,
            "total": row.count,
            "beneficial": int(row.beneficial or 0),
            "neutral": int(row.neutral or 0),
            "detrimental": int(row.detrimental or 0),
            "effectiveness_rate": (int(row.beneficial or 0) / ct * 100) if ct > 0 else 0.0,
        })

    return OverrideEffectivenessResponse(
        effectiveness_rate=effectiveness_rate,
        net_reward_delta=float(net_delta_result),
        total_overrides=total_overrides,
        beneficial_count=beneficial,
        neutral_count=neutral,
        detrimental_count=detrimental,
        pending_count=pending,
        by_trm_type=by_trm_type,
        trend=trend,
    )


# =====================================================================
# Decision Governance — AIIO Gating, Worklist, Directives
# =====================================================================


# ----- Governance Request/Response Models -----

class GovernanceResolveRequest(BaseModel):
    """Resolve a held INSPECT decision."""
    resolution: Literal["approve", "reject", "override"]
    reason: Optional[str] = None
    override_action: Optional[Dict[str, Any]] = None
    override_reason: Optional[str] = Field(
        default=None, description="Required when resolution == 'override'")


class GovernancePolicyCreateRequest(BaseModel):
    """Create or update a governance policy."""
    action_type: Optional[str] = None
    category: Optional[str] = None
    agent_id: Optional[str] = None
    automate_below: float = 20.0
    inform_below: float = 50.0
    hold_minutes: int = 60
    max_hold_minutes: int = 1440
    auto_apply_on_expiry: bool = True
    escalate_after_minutes: int = 480
    weight_financial: float = 0.30
    weight_scope: float = 0.20
    weight_reversibility: float = 0.20
    weight_confidence: float = 0.15
    weight_override_rate: float = 0.15
    name: Optional[str] = None
    description: Optional[str] = None
    priority: int = 100


class GovernancePolicyUpdateRequest(BaseModel):
    """Update fields on an existing governance policy."""
    automate_below: Optional[float] = None
    inform_below: Optional[float] = None
    hold_minutes: Optional[int] = None
    max_hold_minutes: Optional[int] = None
    auto_apply_on_expiry: Optional[bool] = None
    escalate_after_minutes: Optional[int] = None
    weight_financial: Optional[float] = None
    weight_scope: Optional[float] = None
    weight_reversibility: Optional[float] = None
    weight_confidence: Optional[float] = None
    weight_override_rate: Optional[float] = None
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    priority: Optional[int] = None


class DirectiveCreateRequest(BaseModel):
    """Capture an executive guardrail directive."""
    source_user_id: int = Field(..., description="Executive who issued the directive")
    source_channel: Literal["voice", "email", "slack", "teams", "chat", "manual"]
    source_signal_id: Optional[int] = None
    received_at: datetime
    raw_content: str = Field(..., min_length=1, description="Original text/transcript")
    objective: str = Field(..., min_length=1, description="What the executive wants")
    context: Optional[str] = None
    reason: Optional[str] = None
    comment: Optional[str] = None
    extracted_parameters: Optional[Dict[str, Any]] = None
    affected_scope: Optional[Dict[str, Any]] = None
    effective_from: Optional[datetime] = None
    effective_until: Optional[datetime] = None
    extraction_confidence: Optional[float] = None
    extraction_model: Optional[str] = None


class DirectiveReviewRequest(BaseModel):
    """Apply or reject a guardrail directive."""
    action: Literal["apply", "reject"]
    review_comment: Optional[str] = Field(
        default=None, description="Required when action == 'reject'")


# ----- Governance Worklist -----

@router.get("/governance/pending")
def get_governance_pending(
    tenant_id: int = Query(...),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Governance worklist — INSPECT decisions awaiting human review.

    Returns actions ordered by urgency (nearest hold_until first,
    then highest impact_score).
    """
    actions = DecisionGovernanceService.get_pending_decisions(
        db, tenant_id, limit=limit, offset=offset,
    )
    return {
        "pending": [a.to_dict() for a in actions],
        "count": len(actions),
    }


@router.post("/governance/{action_id}/resolve")
def resolve_governance_decision(
    action_id: int,
    body: GovernanceResolveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Resolve a held INSPECT decision: approve, reject, or override.

    - **approve**: Execute the decision as proposed.
    - **reject**: Cancel the decision (reason required).
    - **override**: Execute with modifications (override_reason required).

    All resolutions record user_id, timestamp, and reason for full audit.
    """
    try:
        action = DecisionGovernanceService.resolve_decision(
            db=db,
            action_id=action_id,
            user_id=current_user.id,
            resolution=body.resolution,
            reason=body.reason,
            override_action=body.override_action,
            override_reason=body.override_reason,
        )
        db.commit()
        return action.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ----- Governance Policies CRUD -----

@router.get("/governance/policies")
def list_governance_policies(
    tenant_id: int = Query(...),
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List governance policies for a tenant."""
    query = db.query(DecisionGovernancePolicy).filter(
        DecisionGovernancePolicy.tenant_id == tenant_id,
    )
    if not include_inactive:
        query = query.filter(DecisionGovernancePolicy.is_active == True)

    policies = query.order_by(
        DecisionGovernancePolicy.priority.asc(),
        DecisionGovernancePolicy.action_type.asc(),
    ).all()

    return {"policies": [p.to_dict() for p in policies]}


@router.post("/governance/policies")
def create_governance_policy(
    tenant_id: int = Query(...),
    body: GovernancePolicyCreateRequest = ...,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new governance policy for a tenant."""
    policy = DecisionGovernancePolicy(
        tenant_id=tenant_id,
        action_type=body.action_type,
        category=body.category,
        agent_id=body.agent_id,
        automate_below=body.automate_below,
        inform_below=body.inform_below,
        hold_minutes=body.hold_minutes,
        max_hold_minutes=body.max_hold_minutes,
        auto_apply_on_expiry=body.auto_apply_on_expiry,
        escalate_after_minutes=body.escalate_after_minutes,
        weight_financial=body.weight_financial,
        weight_scope=body.weight_scope,
        weight_reversibility=body.weight_reversibility,
        weight_confidence=body.weight_confidence,
        weight_override_rate=body.weight_override_rate,
        name=body.name,
        description=body.description,
        priority=body.priority,
        is_active=True,
        created_by=current_user.id,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy.to_dict()


@router.put("/governance/policies/{policy_id}")
def update_governance_policy(
    policy_id: int,
    body: GovernancePolicyUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update an existing governance policy."""
    policy = db.query(DecisionGovernancePolicy).filter_by(id=policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(policy, field, value)

    db.commit()
    db.refresh(policy)
    return policy.to_dict()


@router.delete("/governance/policies/{policy_id}")
def deactivate_governance_policy(
    policy_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Deactivate (soft-delete) a governance policy."""
    policy = db.query(DecisionGovernancePolicy).filter_by(id=policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    policy.is_active = False
    db.commit()
    return {"status": "deactivated", "policy_id": policy_id}


# ----- Governance Stats -----

@router.get("/governance/stats")
def get_governance_stats(
    tenant_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Governance metrics dashboard.

    Returns: total_governed, mode distribution, pending count,
    avg impact score, auto-apply rate, human resolve rate,
    avg resolution time, override count.
    """
    stats = DecisionGovernanceService.get_governance_stats(db, tenant_id)
    return stats


# ----- Guardrail Directives -----

@router.get("/governance/directives")
def list_guardrail_directives(
    tenant_id: int = Query(...),
    status: Optional[str] = Query(default=None, description="Filter: PENDING|APPLIED|REJECTED|EXPIRED"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List guardrail directives for a tenant.

    Executive instructions captured from voice, email, Slack, Teams, chat,
    or manual entry — each with full provenance (who, when, channel,
    raw content) and extracted structured fields.
    """
    query = db.query(GuardrailDirective).filter(
        GuardrailDirective.tenant_id == tenant_id,
    )
    if status:
        query = query.filter(GuardrailDirective.status == status)

    directives = query.order_by(
        GuardrailDirective.received_at.desc(),
    ).offset(offset).limit(limit).all()

    total = db.query(GuardrailDirective).filter(
        GuardrailDirective.tenant_id == tenant_id,
        *([GuardrailDirective.status == status] if status else []),
    ).count()

    return {
        "directives": [d.to_dict() for d in directives],
        "total": total,
    }


@router.get("/governance/directives/{directive_id}")
def get_guardrail_directive(
    directive_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single guardrail directive with full provenance."""
    directive = db.query(GuardrailDirective).filter_by(id=directive_id).first()
    if not directive:
        raise HTTPException(status_code=404, detail="Directive not found")
    return directive.to_dict()


@router.post("/governance/directives")
def create_guardrail_directive(
    tenant_id: int = Query(...),
    body: DirectiveCreateRequest = ...,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Capture an executive guardrail directive.

    Records the full provenance: who issued it, when, via which channel,
    the original text/transcript, and the extracted objective, context,
    reason, and parsed governance parameters.

    The directive starts as PENDING until reviewed and applied/rejected.
    """
    directive = GuardrailDirective(
        tenant_id=tenant_id,
        source_user_id=body.source_user_id,
        source_channel=body.source_channel,
        source_signal_id=body.source_signal_id,
        received_at=body.received_at,
        raw_content=body.raw_content,
        objective=body.objective,
        context=body.context,
        reason=body.reason,
        comment=body.comment,
        extracted_parameters=body.extracted_parameters,
        affected_scope=body.affected_scope,
        effective_from=body.effective_from,
        effective_until=body.effective_until,
        extraction_confidence=body.extraction_confidence,
        extraction_model=body.extraction_model,
        status="PENDING",
    )
    db.add(directive)
    db.commit()
    db.refresh(directive)
    return directive.to_dict()


@router.post("/governance/directives/{directive_id}/review")
def review_guardrail_directive(
    directive_id: int,
    body: DirectiveReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Review a guardrail directive: apply or reject.

    - **apply**: Creates a governance policy from the directive's
      extracted_parameters. Links the new policy back to the directive.
    - **reject**: Marks directive as REJECTED (review_comment required).

    Records reviewer user_id, timestamp, and review comment for full audit.
    """
    try:
        if body.action == "apply":
            directive = DecisionGovernanceService.apply_directive(
                db=db,
                directive_id=directive_id,
                reviewer_user_id=current_user.id,
                review_comment=body.review_comment,
            )
        elif body.action == "reject":
            if not body.review_comment:
                raise ValueError("Review comment is required when rejecting a directive")
            directive = DecisionGovernanceService.reject_directive(
                db=db,
                directive_id=directive_id,
                reviewer_user_id=current_user.id,
                review_comment=body.review_comment,
            )
        else:
            raise ValueError(f"Invalid action: {body.action}")

        db.commit()
        return directive.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
