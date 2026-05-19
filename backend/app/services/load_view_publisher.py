"""Load → LoadView publisher (§3.79 Substep 2 Stage C).

SQLAlchemy ORM-event listener that emits a `LoadView` row (Core's
cross-plane projection of TMS execution state) every time a TMS
`Load` row is inserted or updated. DP-Ship's `LoadVolumeSensingTRM`
reads `LoadView` instead of `Load` directly — the projection avoids
DP-Ship's cross-plane import of `Load` and survives a TMS outage
gracefully (DP-Ship's velocity features degrade to stale rather
than failing).

The publisher is plane-registry-gated via `emit_load_view_if_live`:
when the tenant doesn't have DP-Ship (`Plane.DEMAND`) licensed, the
emit returns None and no projection row is written. Zero storage
cost for tenants without DP-Ship.

## Lane resolution

TMS's `Load` ORM doesn't carry a `lane_id` column directly — lanes
are resolved via `TransportationLane(origin_site_id, destination_site_id, mode)`.
The publisher does one cached lookup per (origin, dest, mode) tuple
to populate LoadView.lane_id. Cache is module-level + per-tenant so
re-emission is cheap.

## Activation

Import this module from `app/main.py` (or any module loaded at app
boot). The `event.listens_for` decorators register on import.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

from sqlalchemy import event
from sqlalchemy.orm import Session as SyncSession

from app.models.tms_entities import Load
from azirella_data_model.master import TransportationLane

logger = logging.getLogger(__name__)


# Per-tenant lane-id cache. Keyed on
# (tenant_id, origin_site_id, destination_site_id, mode) → lane_id.
# Bounded growth: lane vocabulary per tenant is small (10s to 100s).
# Resets on process restart — acceptable since LoadView is eventual-
# consistency and a one-time cache miss costs one extra SQL query.
_LANE_ID_CACHE: Dict[Tuple[int, Optional[int], Optional[int], Optional[str]], Optional[int]] = {}


def _resolve_lane_id(
    session: SyncSession,
    tenant_id: int,
    origin_site_id: Optional[int],
    destination_site_id: Optional[int],
    mode: Optional[str],
) -> Optional[int]:
    """Look up the lane_id for a Load's (origin, dest, mode) tuple."""
    key = (tenant_id, origin_site_id, destination_site_id, mode)
    if key in _LANE_ID_CACHE:
        return _LANE_ID_CACHE[key]

    if origin_site_id is None or destination_site_id is None:
        _LANE_ID_CACHE[key] = None
        return None

    row = (
        session.query(TransportationLane.id)
        .filter(
            TransportationLane.tenant_id == tenant_id,
            TransportationLane.origin_site_id == origin_site_id,
            TransportationLane.destination_site_id == destination_site_id,
        )
        .first()
    )
    lane_id = row[0] if row else None
    _LANE_ID_CACHE[key] = lane_id
    return lane_id


@event.listens_for(Load, "after_insert")
@event.listens_for(Load, "after_update")
def _publish_load_view(mapper, connection, target):
    """Emit / refresh LoadView for the changed Load row.

    Runs inside the same DB transaction as the Load write — failure
    here rolls back the Load. The plane-registry check inside
    `emit_load_view_if_live` short-circuits when DP-Ship isn't
    licensed, so the cost for non-DP-Ship tenants is one quick
    registry lookup.
    """
    # Lazy imports: keep module-load cost low.
    from azirella_data_model.intersections.transport_demand import (
        emit_load_view_if_live,
    )
    from app.db.session import sync_session_factory

    # Bind a transient Session to this connection so the publisher
    # writes inside the parent transaction. Don't commit / close
    # explicitly — the parent transaction owns lifecycle.
    sess = sync_session_factory(bind=connection)
    try:
        mode_value = (
            target.mode.value if hasattr(target.mode, "value")
            else str(target.mode) if target.mode else None
        )
        lane_id = _resolve_lane_id(
            sess,
            tenant_id=target.tenant_id,
            origin_site_id=target.origin_site_id,
            destination_site_id=target.destination_site_id,
            mode=mode_value,
        )

        emit_load_view_if_live(
            sess,
            tenant_id=target.tenant_id,
            source_load_id=target.id,
            config_id=target.config_id,
            lane_id=lane_id,
            origin_site_id=target.origin_site_id,
            destination_site_id=target.destination_site_id,
            mode=mode_value,
            actual_departure_dttm=target.actual_departure,
            actual_arrival_dttm=target.actual_arrival,
        )
        sess.flush()
    except Exception:
        # Failure in the publisher must not silently break the Load
        # write. Log + continue; the LoadView will be stale until
        # the next Load update on this row re-publishes (idempotent
        # via the upsert on `tenant_id, source_load_id`).
        logger.exception(
            "load_view_publisher: failed to emit LoadView for Load %s",
            getattr(target, "id", "?"),
        )


__all__ = ["_publish_load_view"]
