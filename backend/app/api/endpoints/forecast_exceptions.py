"""
Forecast Exception API Endpoints

Manages forecast exception alerts, variance tracking, and exception workflows.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from typing import Optional, List
from datetime import datetime, date
from pydantic import BaseModel, Field
import logging
import uuid

from ...db.session import get_sync_db as get_db
from ...models.forecast_exception import ForecastException, ForecastExceptionRule, ForecastExceptionComment
from ...models.sc_entities import Forecast
from ...models.user import User
from app.api.deps import get_current_user

# §3.62 — Core ``Alert`` is the unified plane-tagged alert ORM.
# `forecast_exception_detector` dual-writes a mirror row with
# ``plane=DEMAND, type=VARIANCE_RELIABILITY`` for every
# ForecastException; this endpoint can opt into reading from it via
# ``?source=alert``. Default stays ``legacy`` until the cutover soaks.
from azirella_data_model.risk_engine import (
    Alert,
    AlertStatus,
    AlertType,
    Plane,
)
from .forecast_exceptions_alert_mapper import (
    alert_status_to_legacy as _alert_status_to_legacy,
    alert_to_legacy_dict as _alert_to_legacy_dict,
)
# §3.62 workflow-write cutover (2026-05-18): propagates state +
# acknowledged/resolved timestamps to the mirror Alert row landed by
# the create-side dual-write in forecast_exception_detector. Best-
# effort; failure logs and is swallowed so the legacy write isn't
# blocked.
from app.services.forecast_exception_alert_sync import (
    sync_exception_state_to_alert,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/forecast-exceptions", tags=["Forecast Exceptions"])


# ============================================================================
# Pydantic Models
# ============================================================================

class ForecastExceptionCreate(BaseModel):
    product_id: str
    site_id: Optional[int] = None
    customer_id: Optional[str] = None
    period_start: date
    period_end: Optional[date] = None
    time_bucket: str = "WEEK"
    exception_type: str = "VARIANCE"
    severity: str = "MEDIUM"
    forecast_quantity: float
    actual_quantity: Optional[float] = None
    threshold_percent: float = 20.0
    detection_method: str = "MANUAL"
    detection_details: Optional[dict] = None
    config_id: Optional[int] = None
    tenant_id: Optional[int] = None


class ForecastExceptionUpdate(BaseModel):
    severity: Optional[str] = None
    priority: Optional[int] = None
    status: Optional[str] = None
    root_cause_category: Optional[str] = None
    root_cause_description: Optional[str] = None
    resolution_action: Optional[str] = None
    resolution_notes: Optional[str] = None
    forecast_adjustment: Optional[float] = None
    assigned_to_id: Optional[int] = None


class AcknowledgeRequest(BaseModel):
    notes: Optional[str] = None


class ResolveRequest(BaseModel):
    resolution_action: str
    resolution_notes: Optional[str] = None
    forecast_adjustment: Optional[float] = None
    root_cause_category: Optional[str] = None
    root_cause_description: Optional[str] = None


class EscalateRequest(BaseModel):
    escalate_to_id: int
    reason: str


class ExceptionRuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    rule_type: str = "VARIANCE_THRESHOLD"
    variance_threshold_percent: float = 20.0
    variance_threshold_absolute: Optional[float] = None
    consecutive_periods: int = 1
    min_quantity_threshold: Optional[float] = None
    severity_mapping: Optional[dict] = None
    auto_assign_to_id: Optional[int] = None
    notification_config: Optional[dict] = None
    product_ids: Optional[List[str]] = None
    site_ids: Optional[List[int]] = None
    config_id: Optional[int] = None
    tenant_id: Optional[int] = None


class CommentCreate(BaseModel):
    content: str


class DetectionRunRequest(BaseModel):
    config_id: Optional[int] = None
    product_ids: Optional[List[str]] = None
    period_start: date
    period_end: date
    threshold_percent: float = 20.0


# ============================================================================
# Exception CRUD Endpoints
# ============================================================================

@router.get("")
def list_exceptions(
    db: Session = Depends(get_db),
    config_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
    product_id: Optional[str] = None,
    site_id: Optional[int] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    exception_type: Optional[str] = None,
    assigned_to_id: Optional[int] = None,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    skip: int = 0,
    limit: int = 100,
    # §3.62 — read-source toggle. ``legacy`` (default) reads the
    # ForecastException table; ``alert`` reads Core Alert filtered by
    # ``plane=DEMAND, type=VARIANCE_RELIABILITY``. The dual-write
    # contract keeps both in sync. Default flipped legacy → alert
    # 2026-05-18 after the 2026-05-16 opt-in soaked without complaints.
    # ``source=legacy`` still works for one release as an escape hatch
    # before the legacy read path is deleted in §3.62 follow-up.
    source: str = Query(
        "alert",
        regex="^(legacy|alert)$",
        description="Read source: 'alert' (Core Alert table, §3.62 cutover; "
                    "default 2026-05-18+) or 'legacy' (ForecastException "
                    "table; deprecated, removed in next §3.62 follow-up).",
    ),
):
    """List forecast exceptions with filtering.

    ``source=alert`` switches the read path to Core Alert
    (§3.62). The dual-write contract in
    ``forecast_exception_detector._emit_core_alert`` keeps both
    tables in sync; this endpoint can transparently swap sources
    without affecting write paths. Workflow surface (assign /
    escalate / acknowledge / resolve) still operates on the legacy
    table — those write endpoints will cut over in a follow-up.
    """
    if source == "alert":
        return _list_exceptions_from_alert(
            db=db,
            config_id=config_id,
            tenant_id=tenant_id,
            product_id=product_id,
            site_id=site_id,
            severity=severity,
            status=status,
            period_start=period_start,
            period_end=period_end,
            skip=skip,
            limit=limit,
        )

    query = db.query(ForecastException)

    if config_id:
        query = query.filter(ForecastException.config_id == config_id)
    if tenant_id:
        query = query.filter(ForecastException.tenant_id == tenant_id)
    if product_id:
        query = query.filter(ForecastException.product_id == product_id)
    if site_id:
        query = query.filter(ForecastException.site_id == site_id)
    if status:
        query = query.filter(ForecastException.status == status)
    if severity:
        query = query.filter(ForecastException.severity == severity)
    if exception_type:
        query = query.filter(ForecastException.exception_type == exception_type)
    if assigned_to_id:
        query = query.filter(ForecastException.assigned_to_id == assigned_to_id)
    if period_start:
        query = query.filter(ForecastException.period_start >= period_start)
    if period_end:
        query = query.filter(ForecastException.period_end <= period_end)

    total = query.count()
    exceptions = query.order_by(ForecastException.priority.desc(), ForecastException.detected_at.desc()).offset(skip).limit(limit).all()

    # Attach forecast pipeline lineage metadata when available.
    items = []
    for e in exceptions:
        payload = e.to_dict()
        forecast_row = (
            db.query(Forecast)
            .filter(
                Forecast.product_id == str(e.product_id),
                Forecast.site_id == e.site_id,
                Forecast.forecast_date == e.period_start,
            )
            .order_by(Forecast.source_update_dttm.desc(), Forecast.created_dttm.desc())
            .first()
        )
        if forecast_row:
            payload["forecast_source"] = forecast_row.source
            payload["forecast_method"] = forecast_row.forecast_method
            payload["forecast_run_id"] = forecast_row.source_event_id
        else:
            payload["forecast_source"] = None
            payload["forecast_method"] = None
            payload["forecast_run_id"] = None
        items.append(payload)

    return {
        "total": total,
        "items": items,
    }


def _list_exceptions_from_alert(
    *,
    db: Session,
    config_id: Optional[int],
    tenant_id: Optional[int],
    product_id: Optional[str],
    site_id: Optional[int],
    severity: Optional[str],
    status: Optional[str],
    period_start: Optional[date],
    period_end: Optional[date],
    skip: int,
    limit: int,
) -> dict:
    """§3.62 read cutover — query Core Alert and shape into legacy response."""
    query = db.query(Alert).filter(
        Alert.plane == Plane.DEMAND.value,
        Alert.type == AlertType.VARIANCE_RELIABILITY.value,
    )
    if config_id is not None:
        query = query.filter(Alert.config_id == config_id)
    if product_id:
        query = query.filter(Alert.product_id == product_id)
    if site_id is not None:
        # Alert.site_id is String(255); compare as string.
        query = query.filter(Alert.site_id == str(site_id))
    if severity:
        query = query.filter(Alert.severity == severity)
    if status:
        # Caller passes legacy status (NEW/INVESTIGATING/RESOLVED). Translate
        # to the Alert timestamp shape; ``ACTIONED``/``OVERRIDDEN`` callers
        # using the AIIO vocabulary directly are supported too.
        legacy_to_alert_clauses = {
            "NEW": and_(
                Alert.acknowledged_at.is_(None),
                Alert.resolved_at.is_(None),
            ),
            "INVESTIGATING": and_(
                Alert.acknowledged_at.isnot(None),
                Alert.resolved_at.is_(None),
            ),
            "RESOLVED": Alert.resolved_at.isnot(None),
        }
        clause = legacy_to_alert_clauses.get(status)
        if clause is not None:
            query = query.filter(clause)
        else:
            # Pass-through for raw AIIO state.
            query = query.filter(Alert.status == status)
    if period_start:
        # ``period_start`` lives in factors JSON. Use PostgreSQL's ->> for
        # case-by-case string comparison; the ISO format sorts
        # lexicographically so >= works against the period_start date.
        query = query.filter(
            Alert.factors["period_start"].astext >= period_start.isoformat()
        )
    if period_end:
        query = query.filter(
            Alert.factors["period_end"].astext <= period_end.isoformat()
        )

    total = query.count()
    alerts = (
        query.order_by(Alert.severity.desc(), Alert.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    # Resolve tenant_id once per call (Alert rows don't carry it directly).
    # Use the caller-supplied tenant_id when present; otherwise pass None
    # — frontends typically scope by tenant via auth, not by URL filter.
    items = [
        _alert_to_legacy_dict(alert=a, tenant_id=tenant_id) for a in alerts
    ]
    return {"total": total, "items": items, "source": "alert"}


@router.get("/summary")
def get_exception_summary(
    db: Session = Depends(get_db),
    config_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
):
    """Get summary of exceptions by status and severity"""
    query = db.query(ForecastException)
    if config_id:
        query = query.filter(ForecastException.config_id == config_id)
    if tenant_id:
        query = query.filter(ForecastException.tenant_id == tenant_id)

    # Count by status
    status_counts = dict(
        db.query(ForecastException.status, func.count(ForecastException.id))
        .filter(ForecastException.config_id == config_id if config_id else True)
        .filter(ForecastException.tenant_id == tenant_id if tenant_id else True)
        .group_by(ForecastException.status)
        .all()
    )

    # Count by severity
    severity_counts = dict(
        db.query(ForecastException.severity, func.count(ForecastException.id))
        .filter(ForecastException.config_id == config_id if config_id else True)
        .filter(ForecastException.tenant_id == tenant_id if tenant_id else True)
        .group_by(ForecastException.severity)
        .all()
    )

    # Count by type
    type_counts = dict(
        db.query(ForecastException.exception_type, func.count(ForecastException.id))
        .filter(ForecastException.config_id == config_id if config_id else True)
        .filter(ForecastException.tenant_id == tenant_id if tenant_id else True)
        .group_by(ForecastException.exception_type)
        .all()
    )

    # Open exceptions (not resolved or dismissed)
    open_count = query.filter(
        ~ForecastException.status.in_(["RESOLVED", "DISMISSED"])
    ).count()

    # High priority (severity HIGH or CRITICAL, status not resolved)
    high_priority = query.filter(
        and_(
            ForecastException.severity.in_(["HIGH", "CRITICAL"]),
            ~ForecastException.status.in_(["RESOLVED", "DISMISSED"])
        )
    ).count()

    # Average time to resolve (for resolved exceptions)
    resolved = db.query(ForecastException).filter(
        and_(
            ForecastException.status == "RESOLVED",
            ForecastException.resolved_at.isnot(None),
            ForecastException.detected_at.isnot(None),
        )
    ).all()

    avg_resolution_hours = None
    if resolved:
        total_hours = sum(
            (e.resolved_at - e.detected_at).total_seconds() / 3600
            for e in resolved if e.resolved_at and e.detected_at
        )
        avg_resolution_hours = total_hours / len(resolved) if resolved else None

    return {
        "total_open": open_count,
        "high_priority": high_priority,
        "by_status": status_counts,
        "by_severity": severity_counts,
        "by_type": type_counts,
        "avg_resolution_hours": avg_resolution_hours,
    }


@router.post("")
def create_exception(
    data: ForecastExceptionCreate,
    db: Session = Depends(get_db),
):
    """Create a forecast exception manually"""
    exception = ForecastException(
        exception_number=f"FE-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}",
        product_id=data.product_id,
        site_id=data.site_id,
        customer_id=data.customer_id,
        period_start=data.period_start,
        period_end=data.period_end,
        time_bucket=data.time_bucket,
        exception_type=data.exception_type,
        severity=data.severity,
        forecast_quantity=data.forecast_quantity,
        actual_quantity=data.actual_quantity,
        threshold_percent=data.threshold_percent,
        detection_method=data.detection_method,
        detection_details=data.detection_details,
        config_id=data.config_id,
        tenant_id=data.tenant_id,
    )

    # Calculate variance if actual provided
    exception.calculate_variance()

    db.add(exception)
    db.commit()
    db.refresh(exception)

    return exception.to_dict()


@router.get("/{exception_id}")
def get_exception(
    exception_id: int,
    db: Session = Depends(get_db),
):
    """Get a specific exception by ID"""
    exception = db.query(ForecastException).filter(ForecastException.id == exception_id).first()
    if not exception:
        raise HTTPException(status_code=404, detail="Exception not found")
    return exception.to_dict()


@router.put("/{exception_id}")
def update_exception(
    exception_id: int,
    data: ForecastExceptionUpdate,
    db: Session = Depends(get_db),
):
    """Update an exception"""
    exception = db.query(ForecastException).filter(ForecastException.id == exception_id).first()
    if not exception:
        raise HTTPException(status_code=404, detail="Exception not found")

    for field, value in data.dict(exclude_unset=True).items():
        setattr(exception, field, value)

    db.commit()
    db.refresh(exception)

    return exception.to_dict()


@router.delete("/{exception_id}")
def delete_exception(
    exception_id: int,
    db: Session = Depends(get_db),
):
    """Delete an exception"""
    exception = db.query(ForecastException).filter(ForecastException.id == exception_id).first()
    if not exception:
        raise HTTPException(status_code=404, detail="Exception not found")

    db.delete(exception)
    db.commit()

    return {"success": True}


# ============================================================================
# Workflow Endpoints
# ============================================================================

@router.post("/{exception_id}/acknowledge")
def acknowledge_exception(
    exception_id: int,
    data: AcknowledgeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Acknowledge an exception"""
    exception = db.query(ForecastException).filter(ForecastException.id == exception_id).first()
    if not exception:
        raise HTTPException(status_code=404, detail="Exception not found")

    if exception.status not in ["NEW"]:
        raise HTTPException(status_code=400, detail=f"Cannot acknowledge exception in status {exception.status}")

    exception.status = "ACKNOWLEDGED"
    exception.acknowledged_at = datetime.now()
    # exception.acknowledged_by_id = current_user.id

    if data.notes:
        # Add comment
        comment = ForecastExceptionComment(
            exception_id=exception_id,
            author_id=current_user.id,
            content=f"Acknowledged: {data.notes}",
        )
        db.add(comment)

    # §3.62 workflow-write cutover: mirror state into Core Alert.
    try:
        sync_exception_state_to_alert(
            db, exception, actor_user_id=current_user.id
        )
    except Exception:
        logger.exception(
            "Failed to mirror ForecastException %s ACKNOWLEDGED state to Alert",
            exception.exception_number,
        )

    db.commit()
    db.refresh(exception)

    return exception.to_dict()


@router.post("/{exception_id}/investigate")
def start_investigation(
    exception_id: int,
    db: Session = Depends(get_db),
):
    """Start investigation on an exception"""
    exception = db.query(ForecastException).filter(ForecastException.id == exception_id).first()
    if not exception:
        raise HTTPException(status_code=404, detail="Exception not found")

    if exception.status not in ["NEW", "ACKNOWLEDGED"]:
        raise HTTPException(status_code=400, detail=f"Cannot investigate exception in status {exception.status}")

    exception.status = "INVESTIGATING"

    # §3.62 workflow-write cutover: mirror INVESTIGATING (→ INSPECTED) to Alert.
    try:
        sync_exception_state_to_alert(db, exception)
    except Exception:
        logger.exception(
            "Failed to mirror ForecastException %s INVESTIGATING state to Alert",
            exception.exception_number,
        )

    db.commit()
    db.refresh(exception)

    return exception.to_dict()


@router.post("/{exception_id}/resolve")
def resolve_exception(
    exception_id: int,
    data: ResolveRequest,
    db: Session = Depends(get_db),
):
    """Resolve an exception"""
    exception = db.query(ForecastException).filter(ForecastException.id == exception_id).first()
    if not exception:
        raise HTTPException(status_code=404, detail="Exception not found")

    if exception.status in ["RESOLVED", "DISMISSED"]:
        raise HTTPException(status_code=400, detail=f"Exception already in status {exception.status}")

    exception.status = "RESOLVED"
    exception.resolved_at = datetime.now()
    exception.resolution_action = data.resolution_action
    exception.resolution_notes = data.resolution_notes
    exception.forecast_adjustment = data.forecast_adjustment
    exception.root_cause_category = data.root_cause_category
    exception.root_cause_description = data.root_cause_description

    # §3.62 workflow-write cutover: mirror RESOLVED (→ ACTIONED) to Alert.
    try:
        sync_exception_state_to_alert(db, exception)
    except Exception:
        logger.exception(
            "Failed to mirror ForecastException %s RESOLVED state to Alert",
            exception.exception_number,
        )

    db.commit()
    db.refresh(exception)

    return exception.to_dict()


@router.post("/{exception_id}/escalate")
def escalate_exception(
    exception_id: int,
    data: EscalateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Escalate an exception"""
    exception = db.query(ForecastException).filter(ForecastException.id == exception_id).first()
    if not exception:
        raise HTTPException(status_code=404, detail="Exception not found")

    exception.status = "ESCALATED"
    exception.escalated_to_id = data.escalate_to_id

    # Add comment
    comment = ForecastExceptionComment(
        exception_id=exception_id,
        author_id=current_user.id,
        content=f"Escalated to user {data.escalate_to_id}: {data.reason}",
    )
    db.add(comment)

    # §3.62 workflow-write cutover: mirror ESCALATED (→ INSPECTED) to Alert.
    try:
        sync_exception_state_to_alert(
            db, exception, actor_user_id=current_user.id
        )
    except Exception:
        logger.exception(
            "Failed to mirror ForecastException %s ESCALATED state to Alert",
            exception.exception_number,
        )

    db.commit()
    db.refresh(exception)

    return exception.to_dict()


@router.post("/{exception_id}/dismiss")
def dismiss_exception(
    exception_id: int,
    reason: str = Query(..., description="Reason for dismissing"),
    db: Session = Depends(get_db),
):
    """Dismiss an exception as not actionable"""
    exception = db.query(ForecastException).filter(ForecastException.id == exception_id).first()
    if not exception:
        raise HTTPException(status_code=404, detail="Exception not found")

    exception.status = "DISMISSED"
    exception.resolution_notes = f"Dismissed: {reason}"

    # §3.62 workflow-write cutover: mirror DISMISSED (→ OVERRIDDEN) to Alert.
    try:
        sync_exception_state_to_alert(db, exception)
    except Exception:
        logger.exception(
            "Failed to mirror ForecastException %s DISMISSED state to Alert",
            exception.exception_number,
        )

    db.commit()
    db.refresh(exception)

    return exception.to_dict()


@router.post("/{exception_id}/assign")
def assign_exception(
    exception_id: int,
    user_id: int = Query(..., description="User ID to assign to"),
    db: Session = Depends(get_db),
):
    """Assign exception to a user"""
    exception = db.query(ForecastException).filter(ForecastException.id == exception_id).first()
    if not exception:
        raise HTTPException(status_code=404, detail="Exception not found")

    exception.assigned_to_id = user_id
    db.commit()
    db.refresh(exception)

    return exception.to_dict()


# ============================================================================
# Comment Endpoints
# ============================================================================

@router.get("/{exception_id}/comments")
def get_comments(
    exception_id: int,
    db: Session = Depends(get_db),
):
    """Get comments for an exception"""
    comments = db.query(ForecastExceptionComment).filter(
        ForecastExceptionComment.exception_id == exception_id
    ).order_by(ForecastExceptionComment.created_at.desc()).all()

    return [c.to_dict() for c in comments]


@router.post("/{exception_id}/comments")
def add_comment(
    exception_id: int,
    data: CommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a comment to an exception"""
    exception = db.query(ForecastException).filter(ForecastException.id == exception_id).first()
    if not exception:
        raise HTTPException(status_code=404, detail="Exception not found")

    comment = ForecastExceptionComment(
        exception_id=exception_id,
        author_id=current_user.id,
        content=data.content,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)

    return comment.to_dict()


# ============================================================================
# Rule Endpoints
# ============================================================================

@router.get("/rules/")
def list_rules(
    db: Session = Depends(get_db),
    config_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
    is_active: Optional[bool] = None,
):
    """List exception detection rules"""
    query = db.query(ForecastExceptionRule)

    if config_id:
        query = query.filter(ForecastExceptionRule.config_id == config_id)
    if tenant_id:
        query = query.filter(ForecastExceptionRule.tenant_id == tenant_id)
    if is_active is not None:
        query = query.filter(ForecastExceptionRule.is_active == is_active)

    rules = query.all()
    return [r.to_dict() for r in rules]


@router.post("/rules/")
def create_rule(
    data: ExceptionRuleCreate,
    db: Session = Depends(get_db),
):
    """Create an exception detection rule"""
    rule = ForecastExceptionRule(
        rule_id=f"RULE-{str(uuid.uuid4())[:8].upper()}",
        name=data.name,
        description=data.description,
        rule_type=data.rule_type,
        variance_threshold_percent=data.variance_threshold_percent,
        variance_threshold_absolute=data.variance_threshold_absolute,
        consecutive_periods=data.consecutive_periods,
        min_quantity_threshold=data.min_quantity_threshold,
        severity_mapping=data.severity_mapping,
        auto_assign_to_id=data.auto_assign_to_id,
        notification_config=data.notification_config,
        product_ids=data.product_ids,
        site_ids=data.site_ids,
        config_id=data.config_id,
        tenant_id=data.tenant_id,
    )

    db.add(rule)
    db.commit()
    db.refresh(rule)

    return rule.to_dict()


@router.put("/rules/{rule_id}")
def update_rule(
    rule_id: int,
    data: ExceptionRuleCreate,
    db: Session = Depends(get_db),
):
    """Update an exception rule"""
    rule = db.query(ForecastExceptionRule).filter(ForecastExceptionRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    for field, value in data.dict(exclude_unset=True).items():
        setattr(rule, field, value)

    db.commit()
    db.refresh(rule)

    return rule.to_dict()


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: int,
    db: Session = Depends(get_db),
):
    """Delete an exception rule"""
    rule = db.query(ForecastExceptionRule).filter(ForecastExceptionRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    db.delete(rule)
    db.commit()

    return {"success": True}


@router.post("/rules/{rule_id}/toggle")
def toggle_rule(
    rule_id: int,
    db: Session = Depends(get_db),
):
    """Toggle rule active/inactive"""
    rule = db.query(ForecastExceptionRule).filter(ForecastExceptionRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    rule.is_active = not rule.is_active
    db.commit()
    db.refresh(rule)

    return rule.to_dict()


# ============================================================================
# Detection Endpoints
# ============================================================================

@router.post("/detect")
def run_detection(
    data: DetectionRunRequest,
    db: Session = Depends(get_db),
):
    """
    Run exception detection for a given period.

    Compares forecast (P50) vs actual demand (OutboundOrderLine) and creates
    ForecastException records where variance exceeds detection rule thresholds.
    """
    from app.services.forecast_exception_detector import ForecastExceptionDetector

    detector = ForecastExceptionDetector(db)
    return detector.run_detection(
        config_id=data.config_id,
        period_start=data.period_start,
        period_end=data.period_end,
        threshold_percent=data.threshold_percent,
        product_ids=data.product_ids,
    )


@router.get("/my-assigned")
def get_my_assigned(
    db: Session = Depends(get_db),
    user_id: int = Query(1, description="Current user ID"),
):
    """Get exceptions assigned to current user"""
    exceptions = db.query(ForecastException).filter(
        and_(
            ForecastException.assigned_to_id == user_id,
            ~ForecastException.status.in_(["RESOLVED", "DISMISSED"])
        )
    ).order_by(ForecastException.priority.desc()).all()

    return [e.to_dict() for e in exceptions]
