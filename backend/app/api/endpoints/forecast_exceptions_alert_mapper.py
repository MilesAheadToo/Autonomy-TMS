"""§3.62 — Core ``Alert`` → ForecastException response-shape mapper.

Pure-Python module with no app-state dependencies, so unit tests can
import it without dragging in the FastAPI app, the auth layer, or the
ORM declarative-base configuration. The actual endpoint module
re-exports these and uses them in ``list_exceptions(source='alert')``.

The dual-write contract in ``forecast_exception_detector._emit_core_alert``
stamps every demand-variance Alert with a deterministic ``factors``
JSON payload that mirrors the legacy ``ForecastException`` columns;
these helpers reverse that mapping back into the response shape that
existing clients (the shell's exception worklist) expect, so the
read cutover is transparent from the consumer's perspective.

Status-string translation: the legacy table uses
NEW/INVESTIGATING/RESOLVED/ESCALATED/DEFERRED; ``Alert`` uses the AIIO
states INFORMED/INSPECTED/ACTIONED/OVERRIDDEN. We surface both — the
canonical ``status`` field shows the legacy value derived from
``acknowledged_at`` / ``resolved_at`` timestamps, and ``alert_status``
carries the raw AIIO state for callers that want it. Mapping rule:

  resolved_at set   → RESOLVED
  acknowledged_at   → INVESTIGATING
  otherwise         → NEW
"""
from __future__ import annotations

from typing import Any, Optional


def alert_status_to_legacy(alert) -> str:
    """Translate the AIIO ``Alert.status`` state into the legacy
    ForecastException ``status`` vocabulary, derived from the
    ``acknowledged_at`` / ``resolved_at`` timestamps."""
    if alert.resolved_at is not None:
        return "RESOLVED"
    if alert.acknowledged_at is not None:
        return "INVESTIGATING"
    return "NEW"


def alert_to_legacy_dict(alert, *, tenant_id: Optional[int]) -> dict:
    """Map a Core ``Alert`` row → the ForecastException-shaped dict the
    API has historically returned.

    :param alert: a Core ``Alert`` ORM row (or a duck-typed
        SimpleNamespace in tests). Must have the canonical column set
        (``id``, ``alert_id``, ``severity``, ``status``, ``site_id``,
        ``product_id``, ``config_id``, ``factors``,
        ``acknowledged_at`` / ``resolved_at`` / ``created_at`` /
        ``updated_at``, ``recommended_action``, ``cost_impact``,
        ``resolution_notes``, ``acknowledged_by``).
    :param tenant_id: caller-supplied; ``Alert`` rows don't carry
        ``tenant_id`` directly (it's denormalised through
        ``SupplyChainConfig``). The endpoint resolves the tenant via
        the auth layer and passes it through.

    Plane-specific variance metrics come out of ``alert.factors``;
    legacy-only fields (revenue_impact, workflow_template_id, ...)
    return as ``None``.
    """
    factors = alert.factors or {}

    def _factor(key: str, default: Any = None) -> Any:
        return factors.get(key, default)

    site_id_raw = alert.site_id
    site_id: Optional[int] = None
    if site_id_raw is not None:
        try:
            site_id = int(site_id_raw)
        except (TypeError, ValueError):
            # Alert.site_id is String(255) — TMS lane endpoints don't
            # always parse to int. Drop to None rather than crash
            # ForecastException.site_id is Integer FK).
            site_id = None

    return {
        # ── identity ───────────────────────────────────────────────
        "id": alert.id,
        "exception_number": _factor("exception_number"),
        "alert_id": alert.alert_id,  # NEW — caller can pivot to Alert API
        # ── scope ──────────────────────────────────────────────────
        "config_id": alert.config_id,
        "tenant_id": tenant_id,
        "product_id": alert.product_id,
        "site_id": site_id,
        "customer_id": None,  # legacy-only column
        # ── period + bucket ────────────────────────────────────────
        "period_start": _factor("period_start"),
        "period_end": _factor("period_end"),
        "time_bucket": _factor("time_bucket", "WEEK"),
        # ── classification ────────────────────────────────────────
        "exception_type": _factor("exception_type"),
        "severity": alert.severity,
        "priority": None,  # legacy 1-100 scale; not modeled on Alert
        # ── variance metrics (from factors) ───────────────────────
        "forecast_quantity": _factor("forecast_quantity"),
        "actual_quantity": _factor("actual_quantity"),
        "variance_quantity": _factor("variance_quantity"),
        "variance_percent": _factor("variance_percent"),
        "threshold_percent": _factor("threshold_percent"),
        "direction": _factor("direction"),
        # ── impact (legacy-only — not modeled on Alert today) ──────
        "revenue_impact": None,
        "cost_impact": alert.cost_impact,
        "service_level_impact": None,
        # ── state ──────────────────────────────────────────────────
        "status": alert_status_to_legacy(alert),
        "alert_status": alert.status,  # raw AIIO state
        "root_cause_category": None,
        "root_cause_description": None,
        "resolution_action": alert.recommended_action,
        "resolution_notes": alert.resolution_notes,
        "forecast_adjustment": None,
        # ── detection metadata ────────────────────────────────────
        "detection_method": _factor("detection_method"),
        "detection_rule_id": _factor("detection_rule_id"),
        "detection_details": factors,  # full factors payload
        # ── analysis / assignment / workflow (legacy-only) ─────────
        "ai_analysis": None,
        "ai_recommendation": alert.recommended_action,
        "confidence_score": None,
        "assigned_to_id": None,
        "assigned_to_role": None,
        "escalated_to_id": None,
        "workflow_template_id": None,
        "current_escalation_level": 0,
        "last_escalated_at": None,
        "sla_deadline": None,
        "deferred_until": None,
        # ── notifications ─────────────────────────────────────────
        "notification_sent": False,
        "notification_count": 0,
        "last_notification_at": None,
        # ── timestamps ────────────────────────────────────────────
        "detected_at": alert.created_at.isoformat() if alert.created_at else None,
        "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "acknowledged_by_id": alert.acknowledged_by,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "updated_at": alert.updated_at.isoformat() if alert.updated_at else None,
    }


__all__ = ["alert_status_to_legacy", "alert_to_legacy_dict"]
