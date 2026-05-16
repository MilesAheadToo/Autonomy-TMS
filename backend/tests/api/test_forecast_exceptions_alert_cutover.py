"""§3.62 — forecast_exceptions read-from-Alert cutover unit tests.

Covers the ``_alert_to_legacy_dict`` mapper + the ``_alert_status_to_legacy``
status translation. End-to-end HTTP testing of the ``list_exceptions``
endpoint with ``source=alert`` requires a populated Alert table +
auth deps and is out of scope here — those run in the integration suite.
"""
from __future__ import annotations

import importlib.util
import pathlib
from datetime import datetime
from types import SimpleNamespace

import pytest

# ``forecast_exceptions_alert_mapper`` is a pure-Python module — no
# app-state, ORM, or auth dependencies. We direct-load it via
# importlib to bypass the ``app.api.endpoints`` package ``__init__``
# which eagerly imports every router (triggering the pre-existing
# ``DEFAULT_PLANNING_TEMPLATES`` ImportError unrelated to this PR).
_MAPPER_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "app" / "api" / "endpoints"
    / "forecast_exceptions_alert_mapper.py"
)
_spec = importlib.util.spec_from_file_location(
    "forecast_exceptions_alert_mapper", _MAPPER_PATH,
)
_mapper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mapper)

_alert_status_to_legacy = _mapper.alert_status_to_legacy
_alert_to_legacy_dict = _mapper.alert_to_legacy_dict


def _fake_alert(
    *,
    id: int = 100,
    alert_id: str = "DEMAND-VARIANCE-FE-2026-00042",
    config_id: int = 7,
    product_id: str = "PRD-A",
    site_id: str = "42",
    severity: str = "HIGH",
    status: str = "INFORMED",
    factors: dict | None = None,
    acknowledged_at: datetime | None = None,
    resolved_at: datetime | None = None,
    acknowledged_by: int | None = None,
    resolution_notes: str | None = None,
    recommended_action: str = "Investigate variance...",
    cost_impact: float | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
):
    return SimpleNamespace(
        id=id,
        alert_id=alert_id,
        type="VARIANCE_RELIABILITY",
        severity=severity,
        plane="DEMAND",
        config_id=config_id,
        product_id=product_id,
        site_id=site_id,
        vendor_id=None,
        probability=None,
        days_until_stockout=None,
        days_of_supply=None,
        excess_quantity=None,
        cost_impact=cost_impact,
        message="...",
        recommended_action=recommended_action,
        factors=factors or {},
        status=status,
        acknowledged_by=acknowledged_by,
        acknowledged_at=acknowledged_at,
        resolved_at=resolved_at,
        resolution_notes=resolution_notes,
        resolution_condition=None,
        created_at=created_at or datetime(2026, 5, 16, 12, 0),
        updated_at=updated_at or datetime(2026, 5, 16, 12, 0),
    )


# ── _alert_status_to_legacy ───────────────────────────────────────────────


def test_status_new_when_unack_unresolved():
    a = _fake_alert()
    assert _alert_status_to_legacy(a) == "NEW"


def test_status_investigating_when_ack_unresolved():
    a = _fake_alert(acknowledged_at=datetime(2026, 5, 16, 14, 0))
    assert _alert_status_to_legacy(a) == "INVESTIGATING"


def test_status_resolved_when_resolved_at_set():
    a = _fake_alert(
        acknowledged_at=datetime(2026, 5, 16, 14, 0),
        resolved_at=datetime(2026, 5, 16, 16, 0),
    )
    assert _alert_status_to_legacy(a) == "RESOLVED"


def test_status_resolved_overrides_unack():
    """Edge: resolved without ack (auto-resolution path). Reports
    RESOLVED, not stuck-in-NEW."""
    a = _fake_alert(resolved_at=datetime(2026, 5, 16, 16, 0))
    assert _alert_status_to_legacy(a) == "RESOLVED"


# ── _alert_to_legacy_dict ─────────────────────────────────────────────────


def test_mapper_pulls_variance_metrics_from_factors():
    factors = {
        "exception_number": "FE-2026-00042",
        "exception_type": "VARIANCE",
        "forecast_quantity": 1000.0,
        "actual_quantity": 700.0,
        "variance_quantity": -300.0,
        "variance_percent": -30.0,
        "threshold_percent": 20.0,
        "direction": "UNDER",
        "period_start": "2026-05-09",
        "period_end": "2026-05-15",
        "time_bucket": "WEEK",
        "detection_method": "AUTOMATED",
        "detection_rule_id": 5,
    }
    alert = _fake_alert(factors=factors)
    out = _alert_to_legacy_dict(alert, tenant_id=1)
    assert out["exception_number"] == "FE-2026-00042"
    assert out["exception_type"] == "VARIANCE"
    assert out["forecast_quantity"] == 1000.0
    assert out["actual_quantity"] == 700.0
    assert out["variance_quantity"] == -300.0
    assert out["variance_percent"] == -30.0
    assert out["threshold_percent"] == 20.0
    assert out["direction"] == "UNDER"
    assert out["period_start"] == "2026-05-09"
    assert out["period_end"] == "2026-05-15"
    assert out["time_bucket"] == "WEEK"


def test_mapper_uses_passed_tenant_id():
    alert = _fake_alert()
    out = _alert_to_legacy_dict(alert, tenant_id=99)
    assert out["tenant_id"] == 99


def test_mapper_tenant_id_none_when_unsupplied():
    alert = _fake_alert()
    out = _alert_to_legacy_dict(alert, tenant_id=None)
    assert out["tenant_id"] is None


def test_mapper_carries_site_id_as_int_when_numeric():
    alert = _fake_alert(site_id="42")
    out = _alert_to_legacy_dict(alert, tenant_id=1)
    assert out["site_id"] == 42
    assert isinstance(out["site_id"], int)


def test_mapper_handles_non_numeric_site_id():
    """Alert.site_id is String(255) for plane-flexibility; non-numeric
    values can't fit ForecastException.site_id (Integer FK). Map to
    None rather than crashing."""
    alert = _fake_alert(site_id="LANE-NJ-TX")
    out = _alert_to_legacy_dict(alert, tenant_id=1)
    assert out["site_id"] is None


def test_mapper_carries_alert_id_for_pivoting():
    """New field — lets callers pivot from the legacy-shaped row into
    the Alert API."""
    alert = _fake_alert(alert_id="DEMAND-VARIANCE-FE-2026-00042")
    out = _alert_to_legacy_dict(alert, tenant_id=1)
    assert out["alert_id"] == "DEMAND-VARIANCE-FE-2026-00042"


def test_mapper_exposes_raw_alert_status_alongside_legacy_status():
    """``status`` field translates to legacy semantics; ``alert_status``
    field carries the raw AIIO state for callers that want it."""
    alert = _fake_alert(
        status="INFORMED",
        acknowledged_at=datetime(2026, 5, 16, 14, 0),
    )
    out = _alert_to_legacy_dict(alert, tenant_id=1)
    assert out["status"] == "INVESTIGATING"  # legacy
    assert out["alert_status"] == "INFORMED"  # raw AIIO


def test_mapper_legacy_only_fields_default_to_none():
    alert = _fake_alert()
    out = _alert_to_legacy_dict(alert, tenant_id=1)
    for f in (
        "customer_id",
        "revenue_impact",
        "service_level_impact",
        "root_cause_category",
        "root_cause_description",
        "forecast_adjustment",
        "ai_analysis",
        "confidence_score",
        "assigned_to_id",
        "workflow_template_id",
        "sla_deadline",
    ):
        assert out[f] is None, f"expected {f}=None"


def test_mapper_emits_full_factors_payload_in_detection_details():
    """Operator UIs that want the raw factors payload (debug,
    rule-firing trace, ...) get it via ``detection_details``."""
    factors = {"exception_number": "FE-1", "x": 1, "y": 2}
    alert = _fake_alert(factors=factors)
    out = _alert_to_legacy_dict(alert, tenant_id=1)
    assert out["detection_details"] == factors


def test_mapper_timestamps_iso_serialised():
    alert = _fake_alert(
        created_at=datetime(2026, 5, 16, 12, 0),
        updated_at=datetime(2026, 5, 16, 13, 0),
        acknowledged_at=datetime(2026, 5, 16, 14, 0),
        resolved_at=datetime(2026, 5, 16, 16, 0),
    )
    out = _alert_to_legacy_dict(alert, tenant_id=1)
    assert out["detected_at"] == "2026-05-16T12:00:00"
    assert out["created_at"] == "2026-05-16T12:00:00"
    assert out["updated_at"] == "2026-05-16T13:00:00"
    assert out["acknowledged_at"] == "2026-05-16T14:00:00"
    assert out["resolved_at"] == "2026-05-16T16:00:00"


def test_mapper_resolution_action_from_alert_recommended_action():
    """Alert.recommended_action is the operator-facing prescription;
    legacy ForecastException carried two columns (resolution_action +
    ai_recommendation) that both flow from this same source."""
    alert = _fake_alert(recommended_action="URGENT: investigate root cause...")
    out = _alert_to_legacy_dict(alert, tenant_id=1)
    assert out["resolution_action"] == "URGENT: investigate root cause..."
    assert out["ai_recommendation"] == "URGENT: investigate root cause..."
