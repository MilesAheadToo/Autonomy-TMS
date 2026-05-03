"""LiveExceptionService — §3.29 Group A Phase 2.

Plane-side consumer of the Group A visibility substrate. Reads
``GeofenceEvent`` + ``ETAEstimate`` rows since a checkpoint timestamp,
classifies each into an exception type with urgency + AIIO mode,
and returns a list of :class:`ExceptionResult` dataclasses.

This module ships *pure detection logic* — it does not write to
``AgentDecision`` rows or emit ``HiveSignal`` itself. Slice 2
(``apply_to_decision_stream``) and Slice 3 (cron wiring) layer on
top.

Exception types
---------------

  - **LATE_ARRIVAL_DETECTED** — ETA P50 has slipped past the promised
    arrival, OR the promised arrival now sits inside the [P10, P50]
    band (significant schedule risk). Driven by ``ETAEstimate``.
  - **DWELL_BREACH_ALERT** — A tracked entity has been parked at a
    geofence longer than the geofence's configured dwell threshold.
    Driven by ``GeofenceEvent`` rows with
    ``event_type='DWELL_BREACH'`` OR ``EXIT`` events whose
    ``dwell_duration_seconds`` exceeded the threshold.

Urgency scoring + AIIO classification
-------------------------------------

Both exception types compute urgency in [0, 1]:

  - LATE_ARRIVAL: ``min(1.0, max(0.0, slip_minutes /
    LATE_ARRIVAL_URGENCY_HORIZON_MIN))``. Default horizon: 8 hours,
    so 4-hour slip → urgency 0.5.
  - DWELL_BREACH: ``min(1.0, max(0.0, breach_seconds /
    DWELL_BREACH_URGENCY_HORIZON_SEC))``. Default horizon: 4 hours
    over the threshold.

AIIO mode bands (CLAUDE.md governance pipeline):

  - urgency < ``AIIO_AUTOMATE_THRESHOLD`` (0.30) → ``AUTOMATE``
  - urgency < ``AIIO_INFORM_THRESHOLD`` (0.65) → ``INFORM``
  - else → ``INSPECT``

Defaults match the §3.29 Group A risk-routing draft and can be
overridden per-tenant once §3.33 heuristic-defaults registry support
is wired.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------


LATE_ARRIVAL_URGENCY_HORIZON_MIN = 8 * 60
"""8-hour slip → urgency 1.0. Beyond, the load is operationally late
regardless of remaining transit."""

DWELL_BREACH_URGENCY_HORIZON_SEC = 4 * 3600
"""4 hours past the geofence's dwell threshold → urgency 1.0."""

AIIO_AUTOMATE_THRESHOLD = 0.30
"""Below this urgency, agent recovery (AUTOMATE) is the right AIIO
mode."""

AIIO_INFORM_THRESHOLD = 0.65
"""Below this, INFORM (planner sees the exception + recommended
recovery, can override). At and above, INSPECT (planner reviews +
decides without an agent recommendation)."""

LATE_ARRIVAL_BAND_RISK_THRESHOLD_MIN = 30
"""Promised more than this many minutes past P10 (in the lower half
of the band) surfaces a low-urgency at-risk exception."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class ExceptionType(str, Enum):
    """The two live-exception categories the service emits.

    A third — ``GEOFENCE_DEPARTED_EARLY`` — is reserved in
    :class:`HiveSignalType` for a future iteration; it requires
    knowing the entity's scheduled-departure window, which the
    current substrate doesn't carry on ``GeofenceEvent`` directly.
    """

    LATE_ARRIVAL_DETECTED = "LATE_ARRIVAL_DETECTED"
    DWELL_BREACH_ALERT = "DWELL_BREACH_ALERT"


class AIIOMode(str, Enum):
    """AIIO governance gate per CLAUDE.md."""

    AUTOMATE = "AUTOMATE"
    INFORM = "INFORM"
    INSPECT = "INSPECT"


@dataclass(frozen=True)
class ExceptionResult:
    """One detected exception. Pure dataclass — no DB or signal-bus
    side effects.

    ``source_event_id`` and ``source_eta_id`` link back to the
    triggering substrate row so downstream consumers (Slice 2's
    Decision Stream wiring) can store the link in context_data
    JSON for audit-trail / drill-down purposes.
    """

    exception_type: ExceptionType
    tracked_entity_id: int
    tenant_id: int
    detected_at: datetime
    urgency: float  # in [0, 1]
    aiio_mode: AIIOMode
    reason_text: str
    """Human-readable summary surfaceable in the Decision Stream UI."""
    source_event_id: Optional[int] = None
    """Link to ``GeofenceEvent.id`` when the exception was triggered
    by a geofence event."""
    source_eta_id: Optional[int] = None
    """Link to ``ETAEstimate.id`` when the exception was triggered
    by a late ETA prediction."""
    metadata: dict = field(default_factory=dict)
    """Free-form payload for audit / debugging — slip_minutes,
    breach_seconds, ETA model name, etc."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class LiveExceptionService:
    """Real-time visibility-event consumer producing
    :class:`ExceptionResult` rows.

    Stateless beyond the ``Session`` parameter on each call.
    Designed to run on a 15-minute APScheduler tick (Slice 3).

    Construction takes the urgency-horizon + AIIO-threshold
    constants as kwargs so per-tenant tuning can override the
    defaults without subclassing.
    """

    def __init__(
        self,
        *,
        late_arrival_horizon_min: int = LATE_ARRIVAL_URGENCY_HORIZON_MIN,
        dwell_breach_horizon_sec: int = DWELL_BREACH_URGENCY_HORIZON_SEC,
        automate_threshold: float = AIIO_AUTOMATE_THRESHOLD,
        inform_threshold: float = AIIO_INFORM_THRESHOLD,
    ) -> None:
        if late_arrival_horizon_min <= 0:
            raise ValueError("late_arrival_horizon_min must be positive")
        if dwell_breach_horizon_sec <= 0:
            raise ValueError("dwell_breach_horizon_sec must be positive")
        if not 0.0 < automate_threshold <= inform_threshold <= 1.0:
            raise ValueError(
                "thresholds must satisfy "
                "0 < automate_threshold <= inform_threshold <= 1"
            )
        self.late_arrival_horizon_min = late_arrival_horizon_min
        self.dwell_breach_horizon_sec = dwell_breach_horizon_sec
        self.automate_threshold = automate_threshold
        self.inform_threshold = inform_threshold

    # ------------------------------------------------------------------
    # Pure-logic detection (testable without a DB)
    # ------------------------------------------------------------------

    def classify_late_arrival(
        self,
        *,
        eta_id: int,
        tracked_entity_id: int,
        tenant_id: int,
        promised_at: Optional[datetime],
        predicted_p50_at: Optional[datetime],
        predicted_p10_at: Optional[datetime] = None,
        predicted_p90_at: Optional[datetime] = None,
        predicted_at: Optional[datetime] = None,
        model_name: Optional[str] = None,
    ) -> Optional[ExceptionResult]:
        """Classify a single ETA estimate or return ``None`` if
        on-track.

        Logic:
          1. Missing promised_at or P50 → no exception.
          2. P50 > promised → committed-late, urgency = slip/horizon.
          3. P10 < promised ≤ P50 AND band_slack ≥ threshold → at-risk,
             low-urgency heads-up (capped at automate_threshold).
          4. Otherwise → on-track.
        """
        if promised_at is None or predicted_p50_at is None:
            return None

        slip_seconds = (predicted_p50_at - promised_at).total_seconds()

        if slip_seconds > 0:
            slip_min = slip_seconds / 60.0
            urgency = min(
                1.0, max(0.0, slip_min / self.late_arrival_horizon_min)
            )
            reason = (
                f"ETA P50 ({_fmt(predicted_p50_at)}) exceeds promised "
                f"arrival ({_fmt(promised_at)}) by {slip_min:.0f} min."
            )
            return ExceptionResult(
                exception_type=ExceptionType.LATE_ARRIVAL_DETECTED,
                tracked_entity_id=tracked_entity_id,
                tenant_id=tenant_id,
                detected_at=predicted_at or _utcnow(),
                urgency=urgency,
                aiio_mode=self._classify_aiio(urgency),
                reason_text=reason,
                source_eta_id=eta_id,
                metadata={
                    "slip_minutes": round(slip_min, 1),
                    "model_name": model_name,
                    "promised_at": _to_iso(promised_at),
                    "predicted_p50_at": _to_iso(predicted_p50_at),
                },
            )

        # At-risk: promised_at sits past P10 but at or before P50
        # (slip ≤ 0 → not committed-late, but the lower half of the
        # confidence band is consumed).
        if (
            predicted_p10_at is not None
            and predicted_p10_at < promised_at <= predicted_p50_at
        ):
            band_slack_min = (
                promised_at - predicted_p10_at
            ).total_seconds() / 60.0
            if band_slack_min >= LATE_ARRIVAL_BAND_RISK_THRESHOLD_MIN:
                lower_band_total_min = (
                    predicted_p50_at - predicted_p10_at
                ).total_seconds() / 60.0
                if lower_band_total_min > 0:
                    consumed_frac = band_slack_min / lower_band_total_min
                else:
                    consumed_frac = 1.0
                # Cap at-risk at automate_threshold (AUTOMATE band only).
                urgency = min(
                    self.automate_threshold,
                    consumed_frac * self.automate_threshold,
                )
                reason = (
                    f"Promised arrival ({_fmt(promised_at)}) is inside the "
                    f"[P10={_fmt(predicted_p10_at)}, P50={_fmt(predicted_p50_at)}] "
                    f"band ({band_slack_min:.0f} min from P10) — schedule at risk."
                )
                return ExceptionResult(
                    exception_type=ExceptionType.LATE_ARRIVAL_DETECTED,
                    tracked_entity_id=tracked_entity_id,
                    tenant_id=tenant_id,
                    detected_at=predicted_at or _utcnow(),
                    urgency=urgency,
                    aiio_mode=AIIOMode.AUTOMATE,
                    reason_text=reason,
                    source_eta_id=eta_id,
                    metadata={
                        "band_slack_min": round(band_slack_min, 1),
                        "consumed_band_fraction": round(consumed_frac, 3),
                        "model_name": model_name,
                        "promised_at": _to_iso(promised_at),
                        "predicted_p10_at": _to_iso(predicted_p10_at),
                        "predicted_p50_at": _to_iso(predicted_p50_at),
                    },
                )

        return None

    def classify_dwell_breach(
        self,
        *,
        event_id: int,
        tracked_entity_id: int,
        tenant_id: int,
        event_type: str,
        dwell_duration_seconds: Optional[int],
        dwell_threshold_seconds: Optional[int],
        occurred_at: datetime,
    ) -> Optional[ExceptionResult]:
        """Classify a single ``GeofenceEvent`` into a DWELL_BREACH
        exception or ``None``.

        Triggers:
          - ``event_type == 'DWELL_BREACH'`` (substrate emits live).
          - ``event_type == 'EXIT'`` AND
            ``dwell_duration_seconds > dwell_threshold_seconds``
            (substrate emits the EXIT with retroactive
            dwell_duration when the breach is detected
            post-departure).
        """
        if event_type not in ("DWELL_BREACH", "EXIT"):
            return None
        if dwell_duration_seconds is None or dwell_threshold_seconds is None:
            return None
        breach_seconds = dwell_duration_seconds - dwell_threshold_seconds
        if breach_seconds <= 0:
            return None

        urgency = min(
            1.0, max(0.0, breach_seconds / self.dwell_breach_horizon_sec)
        )
        breach_min = breach_seconds / 60.0
        threshold_min = dwell_threshold_seconds / 60.0
        reason = (
            f"Dwell breach: tracked entity exceeded the "
            f"{threshold_min:.0f}-min threshold by {breach_min:.0f} min "
            f"({event_type})."
        )
        return ExceptionResult(
            exception_type=ExceptionType.DWELL_BREACH_ALERT,
            tracked_entity_id=tracked_entity_id,
            tenant_id=tenant_id,
            detected_at=occurred_at,
            urgency=urgency,
            aiio_mode=self._classify_aiio(urgency),
            reason_text=reason,
            source_event_id=event_id,
            metadata={
                "breach_seconds": breach_seconds,
                "dwell_duration_seconds": dwell_duration_seconds,
                "dwell_threshold_seconds": dwell_threshold_seconds,
                "event_type": event_type,
            },
        )

    def _classify_aiio(self, urgency: float) -> AIIOMode:
        """Map a [0, 1] urgency to an AIIO mode per the configured
        thresholds."""
        if urgency < self.automate_threshold:
            return AIIOMode.AUTOMATE
        if urgency < self.inform_threshold:
            return AIIOMode.INFORM
        return AIIOMode.INSPECT

    # ------------------------------------------------------------------
    # DB-backed detection (queries the substrate)
    # ------------------------------------------------------------------

    def detect_exceptions(
        self,
        db: Session,
        *,
        tenant_id: int,
        since: datetime,
        until: Optional[datetime] = None,
    ) -> List[ExceptionResult]:
        """Read GeofenceEvent + ETAEstimate rows for the tenant in
        ``[since, until)`` and classify each into an exception
        (or skip if on-track).

        Filters on:
          - ``GeofenceEvent.detected_at`` (when the substrate
            detected the event).
          - ``ETAEstimate.predicted_at`` (when the estimate was
            generated).

        Returns the full list of exceptions in the window, regardless
        of urgency or AIIO mode. The caller (Slice 2) decides what
        to persist / surface to the Decision Stream.
        """
        # Lazy imports — keep module-load cost low for callers that
        # only need the dataclasses (e.g. unit tests).
        from azirella_data_model.visibility import (
            ETAEstimate,
            Geofence,
            GeofenceEvent,
        )

        if until is None:
            until = _utcnow()

        results: List[ExceptionResult] = []

        # ── GeofenceEvent → DWELL_BREACH classification ─────────────
        # Join Geofence so we can read dwell_threshold_seconds.
        event_stmt = (
            select(
                GeofenceEvent.id,
                GeofenceEvent.tracked_entity_id,
                GeofenceEvent.tenant_id,
                GeofenceEvent.event_type,
                GeofenceEvent.dwell_duration_seconds,
                GeofenceEvent.occurred_at,
                Geofence.dwell_threshold_seconds.label("threshold_seconds"),
            )
            .join(Geofence, Geofence.id == GeofenceEvent.geofence_id)
            .where(
                GeofenceEvent.tenant_id == tenant_id,
                GeofenceEvent.detected_at >= since,
                GeofenceEvent.detected_at < until,
            )
        )
        for row in db.execute(event_stmt).all():
            result = self.classify_dwell_breach(
                event_id=row.id,
                tracked_entity_id=row.tracked_entity_id,
                tenant_id=row.tenant_id,
                event_type=row.event_type,
                dwell_duration_seconds=row.dwell_duration_seconds,
                dwell_threshold_seconds=row.threshold_seconds,
                occurred_at=row.occurred_at,
            )
            if result is not None:
                results.append(result)

        # ── ETAEstimate → LATE_ARRIVAL_DETECTED classification ──────
        eta_stmt = (
            select(
                ETAEstimate.id,
                ETAEstimate.tracked_entity_id,
                ETAEstimate.tenant_id,
                ETAEstimate.promised_at,
                ETAEstimate.predicted_p50_at,
                ETAEstimate.predicted_p10_at,
                ETAEstimate.predicted_p90_at,
                ETAEstimate.predicted_at,
                ETAEstimate.model_name,
            )
            .where(
                ETAEstimate.tenant_id == tenant_id,
                ETAEstimate.predicted_at >= since,
                ETAEstimate.predicted_at < until,
            )
        )
        for row in db.execute(eta_stmt).all():
            result = self.classify_late_arrival(
                eta_id=row.id,
                tracked_entity_id=row.tracked_entity_id,
                tenant_id=row.tenant_id,
                promised_at=row.promised_at,
                predicted_p50_at=row.predicted_p50_at,
                predicted_p10_at=row.predicted_p10_at,
                predicted_p90_at=row.predicted_p90_at,
                predicted_at=row.predicted_at,
                model_name=row.model_name,
            )
            if result is not None:
                results.append(result)

        return results

    # ------------------------------------------------------------------
    # Decision Stream surface (Slice 2)
    # ------------------------------------------------------------------

    def apply_to_decision_stream(
        self,
        db: Session,
        exceptions: List[ExceptionResult],
    ) -> List[int]:
        """Persist each ``ExceptionResult`` as an ``AgentDecision`` row.

        Routes through :func:`record_trm_decision` with
        ``trm_type='exception_management'`` so the row lands in the
        canonical ``core.agent_decisions`` table the Decision Stream UI
        already reads from. Audit-link IDs (``source_event_id`` /
        ``source_eta_id``) ride along in ``context_data`` JSON for
        drilldown.

        Action-name mapping (drives the recommendation chip in the UI):

          - LATE_ARRIVAL_DETECTED → ``LATE_ARRIVAL_REROUTE``
          - DWELL_BREACH_ALERT    → ``DWELL_BREACH_DISPATCH``

        Recovery options (reroute / hold / contact-customer) are
        surfaced via the existing Decision Stream override flow —
        the planner picks one and the override is captured against
        the AgentDecision row, same as every other TRM decision.

        Returns the list of newly-written ``AgentDecision.id`` values
        (skipping any rows that failed to write — `record_trm_decision`
        is non-fatal and returns ``None`` on error).
        """
        from app.services.powell.agent_decision_writer import record_trm_decision

        ids: List[int] = []
        for exc in exceptions:
            result = _build_decision_payload(exc)
            row_id = record_trm_decision(
                db,
                tenant_id=exc.tenant_id,
                trm_type="exception_management",
                result=result,
                item_code=f"entity-{exc.tracked_entity_id}",
                item_name=f"Tracked entity {exc.tracked_entity_id}",
                category=exc.exception_type.value,
                impact_value=exc.urgency,
                impact_description=exc.reason_text[:255],
            )
            if row_id is not None:
                ids.append(row_id)
        return ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_EXCEPTION_ACTION_NAMES = {
    ExceptionType.LATE_ARRIVAL_DETECTED: "LATE_ARRIVAL_REROUTE",
    ExceptionType.DWELL_BREACH_ALERT: "DWELL_BREACH_DISPATCH",
}


def _build_decision_payload(exc: ExceptionResult) -> dict:
    """Translate an ``ExceptionResult`` into the ``result`` dict
    ``record_trm_decision`` expects.

    The substrate audit links (``source_event_id`` /
    ``source_eta_id``) and exception metadata flow into
    ``context_data`` so Decision Stream drilldown can show what
    GeofenceEvent / ETAEstimate row triggered the exception.
    """
    payload = {
        "action_name": _EXCEPTION_ACTION_NAMES[exc.exception_type],
        "reasoning": exc.reason_text,
        "confidence": 1.0,  # Detection is deterministic — confidence is in the urgency band, not the detection itself.
        "urgency": exc.urgency,
        "exception_type": exc.exception_type.value,
        "aiio_mode": exc.aiio_mode.value,
        "detected_at": _to_iso(exc.detected_at),
        "scoring_detail": dict(exc.metadata),
    }
    if exc.source_event_id is not None:
        payload["source_event_id"] = exc.source_event_id
    if exc.source_eta_id is not None:
        payload["source_eta_id"] = exc.source_eta_id
    return payload


# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """UTC now. Factored out so tests can monkeypatch."""
    return datetime.now(timezone.utc)


def _to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def _fmt(dt: datetime) -> str:
    """Human-friendly datetime for the reason text."""
    return dt.strftime("%Y-%m-%d %H:%M UTC")


__all__ = [
    "AIIOMode",
    "ExceptionResult",
    "ExceptionType",
    "LiveExceptionService",
]
