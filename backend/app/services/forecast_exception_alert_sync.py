"""ForecastException → Alert state sync (§3.62 workflow-write cutover).

The §3.62 create-side dual-write
(``forecast_exception_detector._emit_core_alert``) lands a mirror
``Alert`` row keyed on ``alert_id = f"DEMAND-VARIANCE-{exception_number}"``.

The workflow surface (acknowledge / investigate / resolve / escalate /
dismiss / assign) mutates ``ForecastException.status`` and a handful of
timestamps. This module ships the matching write that propagates the
state transition to the mirror Alert so ``GET /forecast-exceptions``
(default ``source=alert`` since 2026-05-18) sees the right state.

Failure to sync is logged and swallowed — the legacy ForecastException
write is the source of truth until the workflow surface itself moves
to Core Alert. A failed sync just leaves the mirror row stale; the
``ForecastException`` row is unaffected.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.forecast_exception import ForecastException

log = logging.getLogger(__name__)


# Map ForecastException workflow states → AlertStatus values.
# AlertStatus is AIIO-native (INFORMED / INSPECTED / ACTIONED /
# OVERRIDDEN); ForecastException uses NEW / ACKNOWLEDGED /
# INVESTIGATING / RESOLVED / DISMISSED / ESCALATED. Mapping:
#
#   NEW             → INFORMED   (default, no operator interaction)
#   ACKNOWLEDGED    → INSPECTED  (operator opened it)
#   INVESTIGATING   → INSPECTED  (operator still working; no separate
#                                 AIIO state for "in progress" yet)
#   ESCALATED       → INSPECTED  (still being worked, just by someone
#                                 else now)
#   RESOLVED        → ACTIONED   (resolution applied)
#   DISMISSED       → OVERRIDDEN (operator decided no action needed)
_EXC_TO_AIIO = {
    "NEW": "INFORMED",
    "ACKNOWLEDGED": "INSPECTED",
    "INVESTIGATING": "INSPECTED",
    "ESCALATED": "INSPECTED",
    "RESOLVED": "ACTIONED",
    "DISMISSED": "OVERRIDDEN",
}


def sync_exception_state_to_alert(
    db: Session,
    exception: ForecastException,
    *,
    actor_user_id: Optional[int] = None,
) -> None:
    """Propagate ForecastException state + timestamps to its mirror Alert.

    Resolves the Alert via the deterministic
    ``alert_id = f"DEMAND-VARIANCE-{exception.exception_number}"``
    contract used by the create-side dual-write. No-op when the mirror
    row is missing (e.g. a ForecastException created before the §3.62
    cutover landed); logged at debug level.

    ``actor_user_id`` is the user whose action triggered the
    transition. Stored on ``acknowledged_by`` if the transition is to
    INSPECTED or ACTIONED and the field is currently NULL — first
    actor wins, subsequent transitions don't overwrite the
    acknowledgement audit.
    """
    # Lazy import: the Core risk_engine module may not be importable
    # in every TMS test env (the AD-13 substrate is mounted at
    # container start). Keeping the import inside the function means
    # this module loads cleanly even when the legacy ForecastException
    # CRUD endpoints run outside that context.
    try:
        from azirella_data_model.risk_engine import Alert
    except Exception as exc:  # noqa: BLE001
        log.debug(
            "sync_exception_state_to_alert: Core Alert ORM unavailable "
            "(%s); skipping mirror update.", exc,
        )
        return

    alert_id = f"DEMAND-VARIANCE-{exception.exception_number}"
    alert = (
        db.query(Alert).filter(Alert.alert_id == alert_id).one_or_none()
    )
    if alert is None:
        log.debug(
            "sync_exception_state_to_alert: no mirror Alert for %s "
            "(pre-cutover ForecastException?); skipping.", alert_id,
        )
        return

    new_status = _EXC_TO_AIIO.get(exception.status)
    if new_status is None:
        log.warning(
            "sync_exception_state_to_alert: ForecastException %s has "
            "unmapped status %r; leaving mirror Alert untouched.",
            exception.exception_number, exception.status,
        )
        return

    alert.status = new_status

    now = datetime.utcnow()
    # Capture acknowledged_at on the first transition out of INFORMED
    # (operator first interacted). Don't overwrite on subsequent
    # transitions — the audit field reflects when the alert was first
    # taken seriously, not the most recent state change.
    if new_status != "INFORMED" and alert.acknowledged_at is None:
        alert.acknowledged_at = now
        if actor_user_id is not None and alert.acknowledged_by is None:
            alert.acknowledged_by = actor_user_id

    # Resolution timestamps: ACTIONED or OVERRIDDEN both fill
    # resolved_at; resolution_notes mirror the ForecastException's
    # resolution_notes if present.
    if new_status in ("ACTIONED", "OVERRIDDEN"):
        if alert.resolved_at is None:
            alert.resolved_at = now
        if getattr(exception, "resolution_notes", None):
            alert.resolution_notes = exception.resolution_notes

    # No db.commit() here — the caller's commit covers the
    # ForecastException + Alert updates atomically. Failure on either
    # rolls both back.
