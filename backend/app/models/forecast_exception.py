"""TMS shim — canonical Forecast Exception ORMs in Core.

3 of 5 ORMs (ForecastException, ForecastExceptionRule,
ForecastExceptionComment) lifted to Core under
azirella_data_model.master.forecast_exception per
MIGRATION_REGISTER §3.78 Step E (2026-05-19). Re-exported here so
existing import paths
(from app.models.forecast_exception import ForecastException, ...)
keep working unchanged across TMS's detector + workflow + endpoint
consumers.

ExceptionWorkflowTemplate + ExceptionEscalationLog stay TMS-local —
they're plane-side workflow-management ORMs that wrap the canonical
forecast_exception table.

TMS pre-lift was a fork-leftover near-duplicate of SCP's same file;
post-lift both planes re-export from Core.
"""
from azirella_data_model.master.forecast_exception import (  # noqa: F401
    ForecastException,
    ForecastExceptionRule,
    ForecastExceptionComment,
)

from sqlalchemy import (
    Column,
    Integer,
    String,
    Double,
    ForeignKey,
    DateTime,
    Date,
    Text,
    Boolean,
    Index,
    JSON,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column
from datetime import datetime
from typing import Optional, Dict, Any
from .base import Base


class ExceptionWorkflowTemplate(Base):
    """
    Exception Workflow Template - Defines automated routing and escalation paths

    Supports:
    - Automatic assignment based on exception type, severity, product category
    - Multi-level escalation with time-based triggers
    - Notification configuration per level
    - Auto-resolution rules for low-priority exceptions
    """
    __tablename__ = "exception_workflow_template"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Template identification
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # Scope
    config_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("supply_chain_configs.id"))
    tenant_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"))

    # Matching criteria (when to apply this workflow)
    exception_types: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    # ["VARIANCE", "TREND_BREAK", "OUTLIER"]
    severity_levels: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    # ["HIGH", "CRITICAL"]
    product_categories: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    # Product category filters
    min_impact_value: Mapped[Optional[float]] = mapped_column(Double)
    # Only apply if impact > threshold

    # Initial assignment rules (JSON)
    initial_assignment: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    # {
    #   "type": "user" | "role" | "round_robin",
    #   "user_id": 123,  # if type=user
    #   "role": "demand_planner",  # if type=role
    #   "user_pool": [1, 2, 3],  # if type=round_robin
    #   "fallback_user_id": 1
    # }

    # Escalation levels (JSON array)
    escalation_levels: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    # [
    #   {"level": 1, "hours": 24, "assign_to": {"type": "role", "role": "supervisor"}, "notify": ["email", "slack"]},
    #   {"level": 2, "hours": 48, "assign_to": {"type": "user", "user_id": 1}, "notify": ["email", "sms"]}
    # ]

    # Auto-resolution rules
    auto_resolve_config: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    # {
    #   "enabled": true,
    #   "conditions": {"severity": ["LOW"], "age_hours": 168},  # Auto-resolve low severity after 7 days
    #   "resolution_action": "NO_ACTION",
    #   "resolution_notes": "Auto-resolved due to inactivity"
    # }

    # Notification defaults
    notification_channels: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    # {"email": true, "slack": true, "in_app": true, "sms": false}

    # SLA configuration
    sla_hours: Mapped[Optional[int]] = mapped_column(Integer)
    # Target resolution time
    sla_warning_hours: Mapped[Optional[int]] = mapped_column(Integer)
    # Warn when approaching SLA

    # Priority
    priority: Mapped[int] = mapped_column(Integer, default=100)  # Lower = higher priority
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    # Audit
    created_by_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index('idx_ewt_config', 'config_id'),
        Index('idx_ewt_tenant', 'tenant_id'),
        Index('idx_ewt_active', 'is_active', 'priority'),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "config_id": self.config_id,
            "tenant_id": self.tenant_id,
            "exception_types": self.exception_types,
            "severity_levels": self.severity_levels,
            "product_categories": self.product_categories,
            "min_impact_value": self.min_impact_value,
            "initial_assignment": self.initial_assignment,
            "escalation_levels": self.escalation_levels,
            "auto_resolve_config": self.auto_resolve_config,
            "notification_channels": self.notification_channels,
            "sla_hours": self.sla_hours,
            "sla_warning_hours": self.sla_warning_hours,
            "priority": self.priority,
            "is_active": self.is_active,
            "is_default": self.is_default,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ExceptionEscalationLog(Base):
    """
    Tracks escalation events for audit trail
    """
    __tablename__ = "exception_escalation_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exception_id: Mapped[int] = mapped_column(Integer, ForeignKey("forecast_exception.id", ondelete="CASCADE"), nullable=False)

    # Escalation details
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False)
    escalated_from_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    escalated_to_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    escalation_reason: Mapped[str] = mapped_column(String(200), nullable=False)
    # "SLA_BREACH", "MANUAL", "SEVERITY_UPGRADE", "NO_RESPONSE"

    # Trigger info
    triggered_by: Mapped[str] = mapped_column(String(50), nullable=False)
    # "SYSTEM", "USER", "WORKFLOW"
    trigger_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    workflow_template_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("exception_workflow_template.id"))

    # Notification tracking
    notifications_sent: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    # {"email": true, "slack": true, "sent_at": "2026-01-29T10:00:00Z"}

    # Timestamps
    escalated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        Index('idx_eel_exception', 'exception_id'),
        Index('idx_eel_escalated_to', 'escalated_to_id'),
        Index('idx_eel_escalated_at', 'escalated_at'),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "exception_id": self.exception_id,
            "escalation_level": self.escalation_level,
            "escalated_from_id": self.escalated_from_id,
            "escalated_to_id": self.escalated_to_id,
            "escalation_reason": self.escalation_reason,
            "triggered_by": self.triggered_by,
            "trigger_user_id": self.trigger_user_id,
            "workflow_template_id": self.workflow_template_id,
            "notifications_sent": self.notifications_sent,
            "escalated_at": self.escalated_at.isoformat() if self.escalated_at else None,
        }
