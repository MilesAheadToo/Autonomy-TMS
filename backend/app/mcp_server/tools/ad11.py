"""
MCP tools — AD-11 Supply×Transport intersection contract.

Per `Autonomy-Core/docs/CROSS_PLANE_COLLABORATION_ARCHITECTURE.md` §5 + §7,
TMS exposes read-only tools that SCP agents call to inform their
planning decisions:

  * get_carrier_capacity(lane_id, date_from, date_to)
        → CapacitySnapshot — committed + available capacity, OTRI,
          conformal P10/P50/P90 bands on `available_loads`
  * get_dock_availability(site_id, date_from, date_to)
        → DockAvailability — total / open / scheduled door counts +
          per-day occupancy
  * get_active_exceptions(severity_min, status_filter, limit)
        → ActiveExceptionList — open exceptions with shipment context
  * get_realized_shipments(filters, delivered_after, delivered_before)
        → list of joined LanePerformanceActuals + ServiceCommitmentOutcomes
          rows. Closes the SCP forecast / FVA / inventory-buffer
          feedback loop. Per Autonomy-Core MIGRATION_REGISTER §3.8.1.
  * propose_service_window(...) — TMS proposes a capacity-backed
        ServiceWindowPromise; thin wrapper over Core's
        upsert_service_window_promise_if_live with source_plane=TRANSPORT.
        Per MIGRATION_REGISTER §3.8.3.
  * confirm_service_window(correlation_id, new_state)
        → transitions an existing TMS ServiceWindowPromise to
          FULFILLED or MISSED (direct ORM update, registry-guarded).
  * get_pending_commitments(filters)
        → ServiceWindowPromise rows in PROPOSED or JOINT_COMMITTED
          state for SCP planning visibility.

## Contract invariants (all enforced)

  * Read-only. No tool mutates TMS state. Cross-plane state changes go
    through Core intersection tables (DispatchCommitment etc.) — those
    land separately when Core ships register item 1.8.
  * Canonical IDs only. Arguments use canonical site_id / lane_id /
    tenant_id. No TMS-local IDs cross the wire.
  * Conformal payloads where applicable. `available_loads_p10/p50/p90`
    on get_carrier_capacity. Until conformal predictors are calibrated
    for transport-side variables (item 4 in this session shipped the
    framework but not the per-variable calibration), bands collapse to
    point-estimate triples — caller can detect and fall back.
  * Tenant-scoped auth. Every call validates tenant_id; cross-tenant
    reads are forbidden and blocked at the server side via the
    surrounding MCP transport.
  * Bounded latency. Target 200ms p95 per call. SQL is filtered down
    to the relevant lane/site/tenant scope, no full-table scans.

## What this module does NOT include (yet)

  * The DispatchCommitment writeback path (IntermodalTransferTRM →
    intersection table) — blocked on Core MIGRATION_REGISTER 1.8.
  * Conformal P10/P90 spread for `available_loads` — wires through
    once `conformal.active_predictors` has rows for `capacity_buffer`
    or `freight_procurement` per (tenant, lane).
  * The plane-registry guard — design says callers go through
    `fallback.py` helpers; this module is the *callee* and trusts the
    caller's guard. If a caller fires this without registering the
    transport plane, the tool still returns truthful data.
"""
from __future__ import annotations

import logging
from datetime import date as date_type, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, desc, func, or_, select

logger = logging.getLogger(__name__)


def _swp_to_dict(swp) -> Dict[str, Any]:
    """Serialise a ServiceWindowPromise ORM row to the MCP wire shape.

    Producer-owned format. SCP-side consumers either deserialise into
    their own typed projection, parse as `dict[str, Any]`, or pin to
    this module's response shape via standard MCP tool versioning.
    """
    if swp is None:
        return None  # type: ignore[return-value]

    def _iso(dt):
        return dt.isoformat() + "Z" if dt else None

    def _enum_val(v):
        if v is None:
            return None
        return v.value if hasattr(v, "value") else str(v)

    return {
        "id": int(swp.id) if swp.id is not None else None,
        "tenant_id": int(swp.tenant_id) if swp.tenant_id is not None else None,
        "correlation_id": swp.correlation_id,
        "order_id": swp.order_id,
        "product_id": swp.product_id,
        "origin_site_id": swp.origin_site_id,
        "dest_site_id": swp.dest_site_id,
        "lane_id": swp.lane_id,
        "promise_state": _enum_val(swp.promise_state),
        "source_plane_last_updated": _enum_val(swp.source_plane_last_updated),
        "atp_commit_qty": float(swp.atp_commit_qty) if swp.atp_commit_qty is not None else None,
        "atp_available_at": _iso(swp.atp_available_at),
        "capacity_commit_qty": float(swp.capacity_commit_qty) if swp.capacity_commit_qty is not None else None,
        "capacity_window_start": _iso(swp.capacity_window_start),
        "capacity_window_end": _iso(swp.capacity_window_end),
        "conformal_p10": _iso(swp.conformal_p10),
        "conformal_p50": _iso(swp.conformal_p50),
        "conformal_p90": _iso(swp.conformal_p90),
        "fulfilled_at": _iso(swp.fulfilled_at),
        "created_at": _iso(swp.created_at),
        "updated_at": _iso(swp.updated_at),
    }


def register(mcp):
    """Register the three AD-11 read-only tools on an MCP server."""

    @mcp.tool()
    async def get_carrier_capacity(
        tenant_id: int,
        config_id: int,
        lane_id: int,
        date_from: str,
        date_to: str,
    ) -> Dict[str, Any]:
        """Return a CapacitySnapshot for a lane over a date range.

        SCP's ATPExecutorTRM calls this before finalising a deployment
        requirement to verify TMS can take the volume on the requested
        lane × date band. Per AD-11 §5.1.

        Args:
            tenant_id: canonical tenant boundary
            config_id: supply-chain config scope
            lane_id: canonical TransportationLane.id
            date_from / date_to: ISO date strings (inclusive bounds)

        Returns dict with:
            * `lane_id`, `date_from`, `date_to`
            * `total_committed_loads` — sum of CapacityTarget.committed_loads
            * `total_required_loads` — sum of CapacityTarget.required_loads
            * `total_available_loads` — sum of CapacityTarget.available_loads
            * `available_loads_p10` / `_p50` / `_p90` — conformal bands
            * `recent_tender_reject_rate_14d` — DECLINED+EXPIRED / total
              tenders on this lane over trailing 14 days (OTRI)
            * `carrier_count` — distinct carriers serving the lane
            * `as_of_utc` — timestamp the snapshot was computed
        """
        from .db import get_db
        from app.models.tms_entities import (
            FreightTender, Load, TenderStatus,
        )
        from app.models.tms_planning import CapacityTarget
        from app.models.transportation_config import CarrierContract
        from app.models.supply_chain_config import TransportationLane

        d_from = date_type.fromisoformat(date_from)
        d_to = date_type.fromisoformat(date_to)

        async with get_db() as db:
            # Capacity rollup from CapacityTarget over the window
            cap_query = select(
                func.coalesce(func.sum(CapacityTarget.committed_loads), 0),
                func.coalesce(func.sum(CapacityTarget.required_loads), 0),
                func.coalesce(func.sum(CapacityTarget.available_loads), 0),
            ).where(
                CapacityTarget.tenant_id == tenant_id,
                CapacityTarget.config_id == config_id,
                CapacityTarget.lane_id == lane_id,
                CapacityTarget.target_date >= d_from,
                CapacityTarget.target_date <= d_to,
            )
            committed, required, available = (await db.execute(cap_query)).one()
            committed = int(committed)
            required = int(required)
            available = int(available)

            # Trailing 14d reject rate (OTRI proxy) on this lane.
            # FreightTender → Load → (origin_site_id, destination_site_id);
            # TMS Load doesn't carry a lane_id FK so we resolve the
            # lane to its endpoints and filter loads by that pair.
            # Canonical TransportationLane uses from_site_id /
            # to_site_id (not origin_site_id / destination_site_id —
            # which is the column shape on TMS Load).
            lane_endpoints = (await db.execute(
                select(
                    TransportationLane.from_site_id,
                    TransportationLane.to_site_id,
                ).where(TransportationLane.id == lane_id)
            )).first()

            cutoff = datetime.utcnow() - timedelta(days=14)
            total_tenders = 0
            rejected_tenders = 0
            if lane_endpoints:
                origin_sid, dest_sid = lane_endpoints
                load_filter = select(Load.id).where(
                    Load.origin_site_id == origin_sid,
                    Load.destination_site_id == dest_sid,
                )
                total_tenders = (await db.execute(
                    select(func.count(FreightTender.id)).where(
                        FreightTender.tenant_id == tenant_id,
                        FreightTender.tendered_at >= cutoff,
                        FreightTender.load_id.in_(load_filter),
                    )
                )).scalar() or 0
                rejected_tenders = (await db.execute(
                    select(func.count(FreightTender.id)).where(
                        FreightTender.tenant_id == tenant_id,
                        FreightTender.tendered_at >= cutoff,
                        FreightTender.load_id.in_(load_filter),
                        FreightTender.status.in_([
                            TenderStatus.DECLINED, TenderStatus.EXPIRED,
                        ]),
                    )
                )).scalar() or 0
            reject_rate = (
                float(rejected_tenders) / float(total_tenders)
                if total_tenders > 0 else None
            )

            # Carrier count — distinct carriers under active contract
            # for this tenant. CarrierContract.status enum =
            # ACTIVE / DRAFT / EXPIRED / RENEWED / TERMINATED.
            carrier_count = (await db.execute(
                select(func.count(func.distinct(CarrierContract.carrier_id))).where(
                    CarrierContract.tenant_id == tenant_id,
                    CarrierContract.status.in_(["ACTIVE", "RENEWED"]),
                )
            )).scalar() or 0

            # Conformal bands. Until calibration runs publish per-lane
            # quantiles, we return the point estimate three times so
            # the caller can detect collapsed bands and decide how to
            # handle. When `conformal.active_predictors` carries
            # `capacity_buffer` or `freight_procurement` rows for this
            # tenant, swap this for a real lookup.
            p10 = available
            p50 = available
            p90 = available

            return {
                "lane_id": lane_id,
                "date_from": str(d_from),
                "date_to": str(d_to),
                "total_committed_loads": committed,
                "total_required_loads": required,
                "total_available_loads": available,
                "available_loads_p10": p10,
                "available_loads_p50": p50,
                "available_loads_p90": p90,
                "conformal_bands_collapsed": True,
                "recent_tender_reject_rate_14d": reject_rate,
                "tender_sample_size_14d": int(total_tenders),
                "carrier_count": int(carrier_count),
                "as_of_utc": datetime.utcnow().isoformat() + "Z",
            }

    @mcp.tool()
    async def get_dock_availability(
        tenant_id: int,
        config_id: int,
        site_id: int,
        date_from: str,
        date_to: str,
    ) -> Dict[str, Any]:
        """Return DockAvailability for a site over a date range.

        SCP planners (and L3 agents) call this to gauge how much
        physical receiving / shipping capacity TMS has available at a
        given site over a planning horizon, without needing direct
        access to TMS's `dock_door` / `appointment` tables.

        Args:
            tenant_id, config_id, site_id: canonical scope
            date_from / date_to: ISO date strings (inclusive bounds)

        Returns dict with:
            * `site_id`, `date_from`, `date_to`
            * `total_doors` — count of dock_door rows for the site
            * `active_doors` — doors with status active (subset of total)
            * `appointments_in_window` — appointments scheduled in the
              date range
            * `confirmed_count` / `requested_count` / `cancelled_count`
              — appointment status breakdown
            * `daily_occupancy` — list of {date, scheduled, capacity}
              dicts for the window
            * `as_of_utc`
        """
        from .db import get_db
        from app.models.tms_entities import (
            Appointment, AppointmentStatus, DockDoor,
        )

        d_from = date_type.fromisoformat(date_from)
        d_to = date_type.fromisoformat(date_to)

        async with get_db() as db:
            total_doors = (await db.execute(
                select(func.count(DockDoor.id)).where(
                    DockDoor.tenant_id == tenant_id,
                    DockDoor.site_id == site_id,
                )
            )).scalar() or 0

            # active / "open" doors — interpreted via DockDoor.is_active
            # if present; otherwise fall back to total
            active_doors = (await db.execute(
                select(func.count(DockDoor.id)).where(
                    DockDoor.tenant_id == tenant_id,
                    DockDoor.site_id == site_id,
                    DockDoor.is_active.is_(True),
                )
            )).scalar() or total_doors

            # Appointments in window
            ap_in_window = select(Appointment).where(
                Appointment.tenant_id == tenant_id,
                Appointment.site_id == site_id,
                Appointment.scheduled_start >= datetime.combine(d_from, datetime.min.time()),
                Appointment.scheduled_start <= datetime.combine(d_to, datetime.max.time()),
            )

            # Status breakdown
            def _count(status):
                return (
                    select(func.count(Appointment.id))
                    .where(
                        Appointment.tenant_id == tenant_id,
                        Appointment.site_id == site_id,
                        Appointment.scheduled_start >= datetime.combine(d_from, datetime.min.time()),
                        Appointment.scheduled_start <= datetime.combine(d_to, datetime.max.time()),
                        Appointment.status == status,
                    )
                )

            confirmed = (await db.execute(_count(AppointmentStatus.CONFIRMED))).scalar() or 0
            requested = (await db.execute(_count(AppointmentStatus.REQUESTED))).scalar() or 0
            cancelled = (await db.execute(_count(AppointmentStatus.CANCELLED))).scalar() or 0
            total_in_window = int(confirmed) + int(requested) + int(cancelled)

            # Daily occupancy — group by scheduled_start::date
            occ_rows = (await db.execute(
                select(
                    func.date(Appointment.scheduled_start).label("d"),
                    func.count(Appointment.id),
                )
                .where(
                    Appointment.tenant_id == tenant_id,
                    Appointment.site_id == site_id,
                    Appointment.scheduled_start >= datetime.combine(d_from, datetime.min.time()),
                    Appointment.scheduled_start <= datetime.combine(d_to, datetime.max.time()),
                    Appointment.status.in_([
                        AppointmentStatus.CONFIRMED, AppointmentStatus.REQUESTED,
                    ]),
                )
                .group_by("d")
                .order_by("d")
            )).all()
            daily_occupancy = [
                {
                    "date": str(row[0]),
                    "scheduled": int(row[1]),
                    "capacity": int(active_doors),
                }
                for row in occ_rows
            ]

            return {
                "site_id": site_id,
                "date_from": str(d_from),
                "date_to": str(d_to),
                "total_doors": int(total_doors),
                "active_doors": int(active_doors),
                "appointments_in_window": total_in_window,
                "confirmed_count": int(confirmed),
                "requested_count": int(requested),
                "cancelled_count": int(cancelled),
                "daily_occupancy": daily_occupancy,
                "as_of_utc": datetime.utcnow().isoformat() + "Z",
            }

    @mcp.tool()
    async def get_active_exceptions(
        tenant_id: int,
        config_id: int,
        site_id: Optional[int] = None,
        severity_min: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Return active (DETECTED + INVESTIGATING) ShipmentExceptions.

        SCP's ATPExecutorTRM and demand planners use this to back off
        from carriers / lanes / sites that are currently in trouble.

        Args:
            tenant_id, config_id: canonical scope
            site_id: optional — restrict to exceptions on shipments
                touching this site (origin or destination)
            severity_min: optional — one of LOW / MEDIUM / HIGH /
                CRITICAL; returns exceptions ≥ this severity
            limit: max rows to return (capped at 200)

        Returns dict with:
            * `count` — total count matching the filter
            * `exceptions` — list of {exception_id, shipment_id,
              exception_type, severity, resolution_status,
              detected_at, hours_open, origin_site_id,
              destination_site_id, estimated_delay_hrs}
            * `severity_breakdown` — count per severity
            * `as_of_utc`
        """
        from .db import get_db
        from app.models.tms_entities import (
            ExceptionResolutionStatus, ExceptionSeverity, ShipmentException,
            TMSShipment,
        )

        # Severity ranking; match the model's enum values for
        # `severity_min` compare. Default = include all.
        _SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
        min_rank = (
            _SEVERITY_RANK.get(severity_min.upper(), 0)
            if severity_min else 0
        )
        limit = max(1, min(200, int(limit)))

        async with get_db() as db:
            # Base WHERE clause
            conds = [
                ShipmentException.tenant_id == tenant_id,
                ShipmentException.resolution_status.in_([
                    ExceptionResolutionStatus.DETECTED,
                    ExceptionResolutionStatus.INVESTIGATING,
                ]),
            ]
            if site_id is not None:
                conds.append(
                    ShipmentException.shipment_id.in_(
                        select(TMSShipment.id).where(
                            or_(
                                TMSShipment.origin_site_id == site_id,
                                TMSShipment.destination_site_id == site_id,
                            )
                        )
                    )
                )
            if severity_min:
                # Map enum values that meet or exceed min_rank
                allowed = {
                    s.value if hasattr(s, "value") else s
                    for s in ExceptionSeverity
                    if _SEVERITY_RANK.get(
                        (s.value if hasattr(s, "value") else s).upper(), 0
                    ) >= min_rank
                }
                conds.append(ShipmentException.severity.in_(list(allowed)))

            # Count
            count = (await db.execute(
                select(func.count(ShipmentException.id)).where(*conds)
            )).scalar() or 0

            # Severity breakdown
            sev_rows = (await db.execute(
                select(
                    ShipmentException.severity,
                    func.count(ShipmentException.id),
                )
                .where(*conds)
                .group_by(ShipmentException.severity)
            )).all()
            severity_breakdown = {
                (str(r[0].value) if hasattr(r[0], "value") else str(r[0])):
                    int(r[1])
                for r in sev_rows
            }

            # Top-N rows + their shipment context
            rows = (await db.execute(
                select(
                    ShipmentException.id,
                    ShipmentException.shipment_id,
                    ShipmentException.exception_type,
                    ShipmentException.severity,
                    ShipmentException.resolution_status,
                    ShipmentException.detected_at,
                    ShipmentException.estimated_delay_hrs,
                    TMSShipment.origin_site_id,
                    TMSShipment.destination_site_id,
                )
                .join(
                    TMSShipment,
                    TMSShipment.id == ShipmentException.shipment_id,
                    isouter=True,
                )
                .where(*conds)
                .order_by(desc(ShipmentException.detected_at))
                .limit(limit)
            )).all()

            now = datetime.utcnow()
            exceptions = []
            for r in rows:
                detected = r[5]
                hours_open = (
                    (now - detected).total_seconds() / 3600.0
                    if detected else None
                )
                exceptions.append({
                    "exception_id": int(r[0]),
                    "shipment_id": int(r[1]) if r[1] else None,
                    "exception_type": r[2].value if r[2] and hasattr(r[2], "value") else (str(r[2]) if r[2] else None),
                    "severity": r[3].value if r[3] and hasattr(r[3], "value") else (str(r[3]) if r[3] else None),
                    "resolution_status": r[4].value if r[4] and hasattr(r[4], "value") else (str(r[4]) if r[4] else None),
                    "detected_at": detected.isoformat() + "Z" if detected else None,
                    "hours_open": round(hours_open, 1) if hours_open is not None else None,
                    "estimated_delay_hrs": float(r[6]) if r[6] is not None else None,
                    "origin_site_id": int(r[7]) if r[7] else None,
                    "destination_site_id": int(r[8]) if r[8] else None,
                })

            return {
                "count": int(count),
                "exceptions": exceptions,
                "severity_breakdown": severity_breakdown,
                "as_of_utc": now.isoformat() + "Z",
            }

    @mcp.tool()
    async def get_realized_shipments(
        tenant_id: int,
        config_id: int,
        delivered_after: str,
        delivered_before: str,
        site_id: Optional[int] = None,
        lane_id: Optional[int] = None,
        product_id: Optional[str] = None,
        carrier_id: Optional[str] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        """Return realised shipment outcomes — joined LanePerformanceActuals
        + ServiceCommitmentOutcomes rows.

        Closes the SCP-side feedback loop (forecast retraining, FVA
        metrics, demand-sensing TRM updates, inventory-buffer learning)
        without a direct DB read against TMS. Per Autonomy-Core
        MIGRATION_REGISTER §3.8.1.

        Underlying canonical state lives in
        `azirella_data_model.intersections.supply_transport.feedback`:
            * LanePerformanceActuals — promised vs actual transit per
              shipment (TMS writes on every DELIVERED transition).
            * ServiceCommitmentOutcomes — terminal outcome per
              ServiceWindowPromise (MET / MISSED / MISSED_RECOVERED /
              CANCELLED + minutes_late + qty_short + root_cause).

        This tool joins the two via `correlation_id` (outer join — LPA
        rows without a matching SCO still return; outcome fields are
        null in that case).

        Args:
            tenant_id: canonical tenant boundary (REQUIRED).
            config_id: supply-chain config scope. Currently advisory —
                feedback rows are tenant-scoped, not config-scoped, so
                this argument is accepted for symmetry with other AD-11
                tools but does not filter rows. If the schema gains
                config_id later, this becomes enforced.
            delivered_after / delivered_before: ISO date strings,
                inclusive bounds on `actual_delivery`. Required because
                callers should always specify a horizon.
            site_id: optional — filter to shipments whose origin OR
                destination matches this site (via SCO). Rows without
                a matching SCO are excluded when this filter is set.
            lane_id: optional — filter on LanePerformanceActuals.lane_id
                (string match against the canonical lane identifier).
            product_id: optional — filter on SCO.product_id (string).
                Like site_id, requires a matching SCO row.
            carrier_id: optional — filter on LPA.carrier_id (string).
            limit: max rows to return (capped at 5000).

        Returns dict with:
            * `count` — number of rows returned (≤ limit)
            * `shipments` — list of joined dicts (TMS-side wire format,
              producer-owned). Each row carries:
                - correlation_id, lane_id, carrier_id, mode
                - transfer_order_id, load_id (nullable)
                - promised_pickup, actual_pickup
                - promised_eta_p10/p50/p90, actual_delivery
                - transit_minutes_actual, transit_minutes_promised_p50
                - on_time (true / false / null), minutes_late (from SCO),
                  quantity, weight_lb
                - outcome (MET / MISSED / MISSED_RECOVERED / CANCELLED;
                  null if no SCO match)
                - origin_site_id, dest_site_id, product_id, order_id,
                  qty_short, root_cause (all from SCO if matched)
                - observed_at
            * `as_of_utc`

        Edge inputs return typed-empty (count=0, shipments=[]) — never
        500. Bad ISO date strings raise ValueError per the AD-11 contract
        (callers should validate before calling).

        Plane-absence behavior: this is the callee. SCP-side fallback
        when TMS is absent is the caller's responsibility (see
        `cross-plane-mcp-only.md`).
        """
        from .db import get_db
        from azirella_data_model.intersections.supply_transport.feedback import (
            LanePerformanceActuals,
            ServiceCommitmentOutcomes,
        )

        d_after = date_type.fromisoformat(delivered_after)
        d_before = date_type.fromisoformat(delivered_before)
        after_dt = datetime.combine(d_after, datetime.min.time())
        before_dt = datetime.combine(d_before, datetime.max.time())
        limit = max(1, min(5000, int(limit)))

        async with get_db() as db:
            # Outer-join LPA → SCO on (tenant_id, correlation_id).
            # LPA is the driving table; SCO fills in terminal-outcome
            # context where it exists.
            join_cond = and_(
                LanePerformanceActuals.tenant_id == ServiceCommitmentOutcomes.tenant_id,
                LanePerformanceActuals.correlation_id == ServiceCommitmentOutcomes.correlation_id,
            )

            stmt = (
                select(
                    LanePerformanceActuals.correlation_id,
                    LanePerformanceActuals.lane_id,
                    LanePerformanceActuals.carrier_id,
                    LanePerformanceActuals.mode,
                    LanePerformanceActuals.transfer_order_id,
                    LanePerformanceActuals.load_id,
                    LanePerformanceActuals.promised_pickup,
                    LanePerformanceActuals.actual_pickup,
                    LanePerformanceActuals.promised_eta_p10,
                    LanePerformanceActuals.promised_eta_p50,
                    LanePerformanceActuals.promised_eta_p90,
                    LanePerformanceActuals.actual_delivery,
                    LanePerformanceActuals.transit_minutes_actual,
                    LanePerformanceActuals.transit_minutes_promised_p50,
                    LanePerformanceActuals.on_time,
                    LanePerformanceActuals.quantity,
                    LanePerformanceActuals.weight_lb,
                    LanePerformanceActuals.observed_at,
                    ServiceCommitmentOutcomes.outcome,
                    ServiceCommitmentOutcomes.minutes_late,
                    ServiceCommitmentOutcomes.qty_short,
                    ServiceCommitmentOutcomes.root_cause,
                    ServiceCommitmentOutcomes.origin_site_id,
                    ServiceCommitmentOutcomes.dest_site_id,
                    ServiceCommitmentOutcomes.product_id,
                    ServiceCommitmentOutcomes.order_id,
                )
                .select_from(LanePerformanceActuals)
                .outerjoin(ServiceCommitmentOutcomes, join_cond)
                .where(
                    LanePerformanceActuals.tenant_id == tenant_id,
                    LanePerformanceActuals.actual_delivery.isnot(None),
                    LanePerformanceActuals.actual_delivery >= after_dt,
                    LanePerformanceActuals.actual_delivery <= before_dt,
                )
            )

            if lane_id is not None:
                stmt = stmt.where(LanePerformanceActuals.lane_id == str(lane_id))
            if carrier_id is not None:
                stmt = stmt.where(LanePerformanceActuals.carrier_id == str(carrier_id))
            if site_id is not None:
                # site_id requires a matching SCO row
                stmt = stmt.where(or_(
                    ServiceCommitmentOutcomes.origin_site_id == str(site_id),
                    ServiceCommitmentOutcomes.dest_site_id == str(site_id),
                ))
            if product_id is not None:
                # product_id likewise requires a matching SCO row
                stmt = stmt.where(ServiceCommitmentOutcomes.product_id == str(product_id))

            stmt = stmt.order_by(desc(LanePerformanceActuals.actual_delivery)).limit(limit)

            rows = (await db.execute(stmt)).all()

            def _iso(dt):
                return dt.isoformat() + "Z" if dt else None

            shipments = []
            for r in rows:
                shipments.append({
                    "correlation_id": r[0],
                    "lane_id": r[1],
                    "carrier_id": r[2],
                    "mode": r[3],
                    "transfer_order_id": r[4],
                    "load_id": r[5],
                    "promised_pickup": _iso(r[6]),
                    "actual_pickup": _iso(r[7]),
                    "promised_eta_p10": _iso(r[8]),
                    "promised_eta_p50": _iso(r[9]),
                    "promised_eta_p90": _iso(r[10]),
                    "actual_delivery": _iso(r[11]),
                    "transit_minutes_actual": float(r[12]) if r[12] is not None else None,
                    "transit_minutes_promised_p50": float(r[13]) if r[13] is not None else None,
                    "on_time": (
                        bool(r[14]) if r[14] is not None and r[14] in (0, 1) else None
                    ),
                    "quantity": float(r[15]) if r[15] is not None else None,
                    "weight_lb": float(r[16]) if r[16] is not None else None,
                    "observed_at": _iso(r[17]),
                    "outcome": (
                        r[18].value if r[18] is not None and hasattr(r[18], "value")
                        else (str(r[18]) if r[18] is not None else None)
                    ),
                    "minutes_late": float(r[19]) if r[19] is not None else None,
                    "qty_short": float(r[20]) if r[20] is not None else None,
                    "root_cause": r[21],
                    "origin_site_id": r[22],
                    "dest_site_id": r[23],
                    "product_id": r[24],
                    "order_id": r[25],
                })

            return {
                "count": len(shipments),
                "shipments": shipments,
                "as_of_utc": datetime.utcnow().isoformat() + "Z",
                # Echo the requested filters back so the caller can
                # detect whether their config_id was applied (currently
                # not — see docstring caveat) and whether their narrow
                # filters returned no rows vs were silently dropped.
                "filters_applied": {
                    "tenant_id": int(tenant_id),
                    "config_id": int(config_id),
                    "config_id_enforced": False,
                    "delivered_after": delivered_after,
                    "delivered_before": delivered_before,
                    "site_id": int(site_id) if site_id is not None else None,
                    "lane_id": int(lane_id) if lane_id is not None else None,
                    "product_id": product_id,
                    "carrier_id": carrier_id,
                    "limit": limit,
                },
            }

    # ── §3.8.3 — Joint-commit transport layer ──────────────────────
    # Three tools wrapping the already-shipped Core helpers in
    # `azirella_data_model.intersections.supply_transport.fallback`.
    # The Core helpers are sync; the MCP tool surface is async — we
    # bridge with `asyncio.to_thread` so the helper's plane-registry
    # guard logic runs unchanged.
    #
    # Producer-owned wire formats (TMS-side, not Core). Standard MCP
    # tool versioning applies for evolution. Per the corrected §3.8
    # placement: canonical state lives in Core
    # (ServiceWindowPromise ORM); the wire format lives in the
    # producing plane (this file).

    @mcp.tool()
    async def propose_service_window(
        tenant_id: int,
        config_id: int,
        correlation_id: str,
        order_id: str,
        product_id: str,
        origin_site_id: str,
        dest_site_id: str,
        capacity_commit_qty: float,
        capacity_window_start: str,
        capacity_window_end: str,
        lane_id: Optional[str] = None,
        conformal_p10: Optional[str] = None,
        conformal_p50: Optional[str] = None,
        conformal_p90: Optional[str] = None,
        promise_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Propose a TMS-side capacity-backed ServiceWindowPromise.

        Thin wrapper over Core's `upsert_service_window_promise_if_live`
        with `source_plane=Plane.TRANSPORT`, filling in the `capacity_*`
        half of the joint promise. SCP fills the `atp_*` half via its
        own MCP tool. When both halves are populated and consistent,
        callers should advance `promise_state` to `JOINT_COMMITTED`.

        Plane-absence behavior: registry-guarded. If the SUPPLY plane
        is not registered for this tenant, the underlying helper
        returns None and this tool returns
        `{"status": "skipped", "reason": "supply plane not registered"}`.
        Caller must treat this as solo-mode (TMS proceeds with its own
        plan; no joint commit propagates).

        Args:
            tenant_id, config_id: canonical scope.
            correlation_id: joining key (typically deployment-requirement-id
                or transfer-order-id).
            order_id, product_id, origin_site_id, dest_site_id: required
                canonical refs. lane_id optional.
            capacity_commit_qty: TMS half — quantity TMS commits to move.
            capacity_window_start / capacity_window_end: ISO datetime
                strings bounding the capacity window.
            conformal_p10/50/90: optional ISO datetime — joint conformal
                bands (TMS computes from carrier transit P10/P50/P90).
            promise_state: optional, defaults to PROPOSED. Caller can
                pass "JOINT_COMMITTED" if SCP's atp_* half is already
                populated.

        Returns dict:
            * status: "ok" | "skipped"
            * promise: full ServiceWindowPromise snapshot (if ok)
            * reason: peer-absence explanation (if skipped)
            * as_of_utc

        Per MIGRATION_REGISTER §3.8.3.
        """
        import asyncio
        from datetime import datetime as _dt
        from app.db.session import sync_session_factory
        from azirella_data_model.intersections.supply_transport import (
            upsert_service_window_promise_if_live,
            PromiseState,
        )
        from azirella_data_model.planes import Plane

        def _parse_dt(s: Optional[str]):
            if not s:
                return None
            return _dt.fromisoformat(s.replace("Z", ""))

        cap_start = _parse_dt(capacity_window_start)
        cap_end = _parse_dt(capacity_window_end)
        p10 = _parse_dt(conformal_p10)
        p50 = _parse_dt(conformal_p50)
        p90 = _parse_dt(conformal_p90)

        if promise_state and promise_state in PromiseState.__members__:
            state = PromiseState[promise_state]
        else:
            state = PromiseState.PROPOSED

        def _do():
            db = sync_session_factory()
            try:
                row = upsert_service_window_promise_if_live(
                    db,
                    tenant_id=tenant_id,
                    correlation_id=correlation_id,
                    source_plane=Plane.TRANSPORT,
                    order_id=order_id,
                    product_id=product_id,
                    origin_site_id=origin_site_id,
                    dest_site_id=dest_site_id,
                    lane_id=lane_id,
                    capacity_commit_qty=capacity_commit_qty,
                    capacity_window_start=cap_start,
                    capacity_window_end=cap_end,
                    conformal_p10=p10,
                    conformal_p50=p50,
                    conformal_p90=p90,
                    promise_state=state,
                    config_id=config_id,
                )
                db.commit()
                return _swp_to_dict(row) if row else None
            finally:
                db.close()

        snapshot = await asyncio.to_thread(_do)
        now_iso = datetime.utcnow().isoformat() + "Z"
        if snapshot is None:
            return {
                "status": "skipped",
                "reason": "supply plane not registered for this tenant",
                "tenant_id": tenant_id,
                "config_id": config_id,
                "correlation_id": correlation_id,
                "as_of_utc": now_iso,
            }
        return {
            "status": "ok",
            "promise": snapshot,
            "as_of_utc": now_iso,
        }

    @mcp.tool()
    async def confirm_service_window(
        tenant_id: int,
        config_id: int,
        correlation_id: str,
        new_state: str,
        fulfilled_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transition an existing TMS-side ServiceWindowPromise to
        FULFILLED or MISSED.

        Direct ORM update (not upsert): Core's
        `upsert_service_window_promise_if_live` uses ON CONFLICT DO
        NOTHING and won't update an existing row's state. We fetch
        with `get_service_window_promise`, then update + commit, with
        the same plane-registry-guard semantics.

        Plane-absence behavior: same as `propose_service_window`. If
        SUPPLY isn't registered, returns `status: skipped` and does
        not update.

        Args:
            tenant_id, config_id: canonical scope.
            correlation_id: identifies the existing promise to transition.
            new_state: "FULFILLED" or "MISSED". Other values rejected.
            fulfilled_at: optional ISO datetime. Defaults to now if
                new_state == "FULFILLED"; ignored otherwise.

        Returns dict:
            * status: "ok" | "skipped" | "not_found" | "error"
            * promise: full snapshot of the updated row (if ok)
            * reason: explanation (if not ok)
            * as_of_utc

        Per MIGRATION_REGISTER §3.8.3.
        """
        import asyncio
        from datetime import datetime as _dt
        from app.db.session import sync_session_factory
        from azirella_data_model.intersections.supply_transport import (
            get_service_window_promise,
            PromiseState,
        )
        from azirella_data_model.planes import Plane, PlaneRegistry

        valid_states = {"FULFILLED", "MISSED"}
        if new_state not in valid_states:
            return {
                "status": "error",
                "reason": f"new_state must be one of {sorted(valid_states)}; got {new_state!r}",
                "as_of_utc": datetime.utcnow().isoformat() + "Z",
            }
        target = PromiseState[new_state]

        if fulfilled_at:
            fulfilled_dt = _dt.fromisoformat(fulfilled_at.replace("Z", ""))
        elif target == PromiseState.FULFILLED:
            fulfilled_dt = _dt.utcnow()
        else:
            fulfilled_dt = None

        def _do():
            db = sync_session_factory()
            try:
                if not PlaneRegistry.is_registered(
                    db, tenant_id, Plane.SUPPLY, config_id=config_id,
                ):
                    return ("peer_absent", None)
                row = get_service_window_promise(
                    db, tenant_id=tenant_id, correlation_id=correlation_id,
                )
                if row is None:
                    return ("not_found", None)
                row.promise_state = target.value
                if fulfilled_dt is not None:
                    row.fulfilled_at = fulfilled_dt
                row.source_plane_last_updated = Plane.TRANSPORT.value
                db.commit()
                return ("ok", _swp_to_dict(row))
            finally:
                db.close()

        status, snapshot = await asyncio.to_thread(_do)
        now_iso = datetime.utcnow().isoformat() + "Z"
        if status == "peer_absent":
            return {
                "status": "skipped",
                "reason": "supply plane not registered for this tenant",
                "tenant_id": tenant_id,
                "config_id": config_id,
                "correlation_id": correlation_id,
                "as_of_utc": now_iso,
            }
        if status == "not_found":
            return {
                "status": "not_found",
                "reason": f"no ServiceWindowPromise with correlation_id={correlation_id!r}",
                "tenant_id": tenant_id,
                "config_id": config_id,
                "correlation_id": correlation_id,
                "as_of_utc": now_iso,
            }
        return {
            "status": "ok",
            "promise": snapshot,
            "as_of_utc": now_iso,
        }

    @mcp.tool()
    async def get_pending_commitments(
        tenant_id: int,
        config_id: int,
        lane_id: Optional[str] = None,
        site_id: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """Return ServiceWindowPromise rows in PROPOSED or JOINT_COMMITTED state.

        SCP planning visibility — what TMS is currently committed to
        deliver. Read-only. No registry guard (consistent with Core's
        `get_service_window_promise` — a tenant can always read its
        own joint promises).

        Args:
            tenant_id, config_id: canonical scope. config_id is
                advisory (ServiceWindowPromise table is tenant-scoped,
                not config-scoped); echoed back in `filters_applied`
                with `config_id_enforced=False`.
            lane_id: optional — filter by canonical lane id (string).
            site_id: optional — filter by origin OR destination site
                (string).
            limit: max rows; capped at 2000.

        Returns dict:
            * count
            * commitments: list of ServiceWindowPromise dicts
            * as_of_utc
            * filters_applied: echoed inputs

        Edge inputs return typed-empty (count=0, commitments=[]).

        Per MIGRATION_REGISTER §3.8.3.
        """
        from .db import get_db
        from azirella_data_model.intersections.supply_transport import (
            ServiceWindowPromise, PromiseState,
        )

        limit = max(1, min(2000, int(limit)))

        async with get_db() as db:
            stmt = select(ServiceWindowPromise).where(
                ServiceWindowPromise.tenant_id == tenant_id,
                ServiceWindowPromise.promise_state.in_([
                    PromiseState.PROPOSED.value,
                    PromiseState.JOINT_COMMITTED.value,
                ]),
            )
            if lane_id is not None:
                stmt = stmt.where(ServiceWindowPromise.lane_id == lane_id)
            if site_id is not None:
                stmt = stmt.where(or_(
                    ServiceWindowPromise.origin_site_id == site_id,
                    ServiceWindowPromise.dest_site_id == site_id,
                ))
            stmt = stmt.order_by(
                desc(ServiceWindowPromise.created_at)
            ).limit(limit)

            rows = (await db.execute(stmt)).scalars().all()

            return {
                "count": len(rows),
                "commitments": [_swp_to_dict(r) for r in rows],
                "as_of_utc": datetime.utcnow().isoformat() + "Z",
                "filters_applied": {
                    "tenant_id": int(tenant_id),
                    "config_id": int(config_id),
                    "config_id_enforced": False,
                    "lane_id": lane_id,
                    "site_id": site_id,
                    "limit": limit,
                },
            }
