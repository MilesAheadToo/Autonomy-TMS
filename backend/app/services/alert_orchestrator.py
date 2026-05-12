"""TMS Alert Orchestrator — §3.62 Phase 3.

Plane-side orchestrator for the unified Risk Engine. TMS canonical
state (ShipmentLeg actual vs planned timestamps, ConditionAlert
legacy table, ForecastException legacy table) maps into Core's
``Alert`` ORM via this module. Plane-specific TRMs / dashboards
consume from the unified ``risk_alerts`` table.

Phase 3 scope:

* ``detect_carrier_reliability(lane_id, carrier_id)`` — concrete
  demonstration of the plane-orchestrator-over-Core-detector pattern.
  Reads ShipmentLeg rows for a (lane, carrier) cohort, computes
  realised transit times, feeds into Core's ``variance_reliability``,
  and persists an Alert(type=VARIANCE_RELIABILITY, plane=TRANSPORT)
  row when the cohort's CV% crosses the configured threshold.

* ``TMSRiskInputAdapter`` — type-checkable adapter that wires TMS
  entity keys (``(lane_id, carrier_id)``) to Core detector input
  dataclasses. Today only implements
  ``to_variance_reliability_input``; ``to_supply_risk_input`` and
  ``to_excess_capacity_input`` raise NotImplementedError (TMS has no
  inventory-style supply-risk surface yet; they'll be wired when
  TMS gains buffer / capacity entities).

* Severity ladder + recommended-action copy stay plane-side (TMS
  policy on what "high CV%" means for carrier selection differs
  from SCP's vendor-lead-time policy).

Deferred from Phase 3 (separate PR):

* TMS ``ForecastException`` migration — actively produced by
  ``forecast_exception_detector.py`` with full CRUD endpoints +
  workflow escalation. Its richer surface (root_cause_category,
  ai_recommendation, sla_deadline, workflow_template_id) deserves a
  focused PR that preserves UX semantics; folding it in here would
  rush it.

* TMS ``ConditionAlert`` retirement — no producer exists today but
  ``executive_briefing_service`` reads it as a fallback. Leave the
  ORM in place; remove when ConditionAlert is also migrated /
  graveyarded.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from azirella_data_model.risk_engine import (
    Alert,
    AlertSeverity,
    AlertStatus,
    AlertType,
    Plane,
    RiskInputAdapter,
    SupplyRiskInput,
    ExcessCapacityInput,
    VarianceReliabilityInput,
    build_resolution_condition,
    variance_reliability,
)

log = logging.getLogger(__name__)


# TMS severity ladder for carrier reliability. Less variance is more
# reliable; CV% values come from Core's variance_reliability().
TMS_VENDOR_LEADTIME_CV_LOW = 20.0   # below this → not flagged
TMS_VENDOR_LEADTIME_CV_MEDIUM = 30.0
TMS_VENDOR_LEADTIME_CV_HIGH = 40.0
# CRITICAL is any CV beyond HIGH.


class TMSRiskInputAdapter(RiskInputAdapter):
    """Marshal TMS canonical state into Core detector inputs.

    Each ``to_*_input`` method takes an opaque ``entity_key`` tuple:

    * ``to_variance_reliability_input((lane_id, carrier_id))`` — reads
      ShipmentLeg rows for the cohort and returns the realised
      transit-time observations.

    The other adapters raise NotImplementedError until TMS gains
    inventory-style entities (buffer stocks at cross-docks, etc.).
    """

    def __init__(self, db: Session):
        self.db = db

    def to_supply_risk_input(self, entity_key: tuple) -> SupplyRiskInput:
        raise NotImplementedError(
            "TMS has no supply-risk surface yet; carrier capacity "
            "monitoring is the closest analogue and lives outside "
            "the unified Alert flow."
        )

    def to_excess_capacity_input(self, entity_key: tuple) -> ExcessCapacityInput:
        raise NotImplementedError(
            "TMS has no inventory-style excess-capacity surface yet."
        )

    def to_variance_reliability_input(
        self, entity_key: tuple
    ) -> VarianceReliabilityInput:
        """Build ``VarianceReliabilityInput`` for a (lane_id, carrier_id) cohort.

        Reads ShipmentLeg rows with both actual_departure and
        actual_arrival populated; observations are realised transit
        durations in hours. ``target_value`` is set from the
        TransportationLane's planned-transit-hours when available so
        the output's ``target_breach_pct`` is meaningful.
        """
        lane_id, carrier_id = entity_key
        # Local imports to avoid registering TMS-specific classes at
        # module-load time (keeps the adapter testable without the full
        # TMS plane wrapper having executed).
        from app.models.tms_entities import ShipmentLeg

        rows = (
            self.db.query(ShipmentLeg)
            .filter(
                and_(
                    ShipmentLeg.carrier_id == carrier_id,
                    ShipmentLeg.actual_departure.is_not(None),
                    ShipmentLeg.actual_arrival.is_not(None),
                )
            )
            .all()
        )

        observations: list[float] = []
        for r in rows:
            delta = (r.actual_arrival - r.actual_departure).total_seconds() / 3600.0
            if delta > 0:
                observations.append(delta)

        # Target is the planned transit hours, averaged across this cohort.
        # Cheap proxy: mean of (planned_arrival - planned_departure) over the
        # same rows where both are set.
        planned = [
            (r.planned_arrival - r.planned_departure).total_seconds() / 3600.0
            for r in rows
            if r.planned_arrival is not None and r.planned_departure is not None
        ]
        target_value = (sum(planned) / len(planned)) if planned else None

        return VarianceReliabilityInput(
            observations=tuple(observations),
            target_value=target_value,
        )


def _severity_for_cv(cv_pct: float) -> Optional[AlertSeverity]:
    """TMS-policy mapping from CV% to severity.

    Returns None when the cohort is reliable enough to skip alerting.
    """
    if cv_pct < TMS_VENDOR_LEADTIME_CV_LOW:
        return None
    if cv_pct < TMS_VENDOR_LEADTIME_CV_MEDIUM:
        return AlertSeverity.MEDIUM
    if cv_pct < TMS_VENDOR_LEADTIME_CV_HIGH:
        return AlertSeverity.HIGH
    return AlertSeverity.CRITICAL


class TMSAlertOrchestrator:
    """Plane orchestrator over Core detectors for TMS.

    Pattern mirrors SCP's ``RiskDetectionService`` after its Phase 2
    rewrite: instantiate an adapter, call Core detectors, persist
    Alert rows with plane=TRANSPORT.
    """

    def __init__(self, db: Session):
        self.db = db
        self.adapter = TMSRiskInputAdapter(db)

    def detect_carrier_reliability(
        self,
        lane_id: int,
        carrier_id: int,
        *,
        config_id: Optional[int] = None,
        persist: bool = True,
    ) -> Optional[Alert]:
        """Run variance-reliability on the (lane, carrier) cohort and
        emit / upsert an Alert when CV% crosses MEDIUM threshold.

        Returns the Alert row when one was emitted/updated, or None
        when the cohort is reliable enough to skip.

        ``persist=False`` returns the would-be Alert object without
        adding it to the session — useful for dry-run / smoke tests.
        """
        inp = self.adapter.to_variance_reliability_input((lane_id, carrier_id))
        out = variance_reliability(inp)

        severity = _severity_for_cv(out.cv_pct)
        if severity is None:
            return None

        alert_id = f"TMS-CARRIER-CV-lane-{lane_id}-carrier-{carrier_id}"
        message = (
            f"Carrier {carrier_id} on lane {lane_id}: realised transit-time "
            f"CV {out.cv_pct:.1f}% (P10/P50/P90 = "
            f"{out.p10:.1f}h / {out.p50:.1f}h / {out.p90:.1f}h, "
            f"n={out.factors.get('sample_size', '?')})."
        )
        if out.target_breach_pct is not None:
            message += (
                f" {out.target_breach_pct:.0f}% of legs exceeded the "
                f"planned transit time."
            )

        if severity == AlertSeverity.CRITICAL:
            action = (
                f"URGENT: Re-rate carrier {carrier_id} on lane {lane_id} "
                f"and shift tenders to alternative providers."
            )
        elif severity == AlertSeverity.HIGH:
            action = (
                f"Review the carrier-{carrier_id}/lane-{lane_id} contract "
                f"and tighten guaranteed-transit SLA terms."
            )
        else:
            action = (
                f"Monitor carrier {carrier_id} performance on lane {lane_id}."
            )

        resolution = build_resolution_condition(
            metric="cv_pct",
            operator="lt",
            threshold=TMS_VENDOR_LEADTIME_CV_LOW,
            description=(
                f"Auto-resolve when realised-transit CV for carrier "
                f"{carrier_id} on lane {lane_id} drops below "
                f"{TMS_VENDOR_LEADTIME_CV_LOW:.0f}%."
            ),
        )

        factors = {
            "p10_hours": out.p10,
            "p50_hours": out.p50,
            "p90_hours": out.p90,
            "mean_hours": out.mean,
            "std_hours": out.std,
            "cv_pct": out.cv_pct,
            "reliability_score": out.reliability_score,
            "target_breach_pct": out.target_breach_pct,
            "sample_size": out.factors.get("sample_size"),
            "lane_id": lane_id,
            "carrier_id": carrier_id,
        }

        existing = (
            self.db.query(Alert)
            .filter(Alert.alert_id == alert_id)
            .one_or_none()
        )
        now = datetime.utcnow()
        if existing is None:
            alert = Alert(
                alert_id=alert_id,
                type=AlertType.VARIANCE_RELIABILITY.value,
                severity=severity.value,
                plane=Plane.TRANSPORT.value,
                config_id=config_id,
                # site_id stores the lane key in string form for cohort indexing;
                # the int FK to ``site`` doesn't apply (TMS cohorts are lane × carrier).
                site_id=f"lane-{lane_id}",
                vendor_id=str(carrier_id),
                probability=out.reliability_score,  # 0-100 reliability → reusable as score
                message=message,
                recommended_action=action,
                factors=factors,
                status=AlertStatus.INFORMED.value,
                resolution_condition=resolution,
                created_at=now,
                updated_at=now,
            )
            if persist:
                self.db.add(alert)
            return alert

        # Update mutable fields (don't touch acknowledged_* / status —
        # those are operator state).
        existing.severity = severity.value
        existing.probability = out.reliability_score
        existing.message = message
        existing.recommended_action = action
        existing.factors = factors
        existing.resolution_condition = resolution
        existing.updated_at = now
        return existing


__all__ = [
    "TMSAlertOrchestrator",
    "TMSRiskInputAdapter",
    "TMS_VENDOR_LEADTIME_CV_LOW",
    "TMS_VENDOR_LEADTIME_CV_MEDIUM",
    "TMS_VENDOR_LEADTIME_CV_HIGH",
]
