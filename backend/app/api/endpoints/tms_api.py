"""
TMS API Endpoints — Carriers, Loads, Operations, Rates

Provides the management/read-only API layer for the TMS frontend.
All queries are tenant/config scoped via the current user's context.
Tables that haven't been migrated yet return empty results with a warning header.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_active_user
from app.db.session import get_db
from app.models.user import User

# TMS entity models
from app.models.tms_entities import (
    Appointment,
    AppointmentStatus,
    AppointmentType,
    Carrier,
    CarrierTMSProfile,
    CarrierLane,
    CarrierScorecard,
    DockDoor,
    Equipment,
    ExceptionSeverity,
    ExceptionType,
    FreightRate,
    FreightTender,
    Load,
    LoadItem,
    LoadStatus,
    RateType,
    Shipment,
    ShipmentException,
    ShipmentStatus,
    TransportMode,
)
from azirella_data_model.transport_plan import TransportationPlan, TransportationPlanItem
from app.models.transportation_config import FacilityConfig, LaneProfile
from app.models.supply_chain_config import Site, TransportationLane, SupplyChainConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOT_PROVISIONED = {"items": [], "warning": "TMS tables not yet provisioned. Run migrations."}


def _safe_str(val: Any) -> Optional[str]:
    """Convert an enum or value to string, or None."""
    if val is None:
        return None
    return str(val.value) if hasattr(val, "value") else str(val)


async def _resolve_config_id(
    config_id: Optional[int],
    current_user: User,
    db: AsyncSession,
) -> Optional[int]:
    """Return the requested config_id, falling back to the user's active config."""
    if config_id is not None:
        return config_id
    # Use user's active_config_id if available
    return getattr(current_user, "active_config_id", None)


# ---------------------------------------------------------------------------
# Pydantic Response Schemas
# ---------------------------------------------------------------------------

class CarrierSummary(BaseModel):
    id: int
    code: str
    name: str
    carrier_type: Optional[str] = None
    scac: Optional[str] = None
    modes: Optional[List[str]] = None
    equipment_types: Optional[List[str]] = None
    service_regions: Optional[List[str]] = None
    is_active: bool = True
    onboarding_status: Optional[str] = None

    class Config:
        from_attributes = True


class CarrierDetail(CarrierSummary):
    mc_number: Optional[str] = None
    dot_number: Optional[str] = None
    usdot_safety_rating: Optional[str] = None
    is_hazmat_certified: bool = False
    primary_contact_name: Optional[str] = None
    primary_contact_email: Optional[str] = None
    dispatch_email: Optional[str] = None
    dispatch_phone: Optional[str] = None
    lane_count: int = 0
    lanes: List[Dict[str, Any]] = []

    class Config:
        from_attributes = True


class ScorecardResponse(BaseModel):
    carrier_id: int
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    total_shipments: int = 0
    total_loads: int = 0
    on_time_pickup_pct: Optional[float] = None
    on_time_delivery_pct: Optional[float] = None
    avg_cost_per_mile: Optional[float] = None
    avg_cost_per_shipment: Optional[float] = None
    damage_rate_pct: Optional[float] = None
    tender_acceptance_rate_pct: Optional[float] = None
    tracking_compliance_pct: Optional[float] = None
    composite_score: Optional[float] = None
    score_components: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class LoadSummary(BaseModel):
    id: int
    load_number: str
    status: Optional[str] = None
    origin_site_id: int
    destination_site_id: int
    mode: Optional[str] = None
    carrier_id: Optional[int] = None
    total_weight: Optional[float] = None
    total_volume: Optional[float] = None
    total_pallets: Optional[int] = None
    weight_utilization_pct: Optional[float] = None
    planned_departure: Optional[datetime] = None
    planned_arrival: Optional[datetime] = None
    total_cost: Optional[float] = None
    cost_per_mile: Optional[float] = None
    total_miles: Optional[float] = None

    class Config:
        from_attributes = True


class LoadDetail(LoadSummary):
    equipment_type: Optional[str] = None
    equipment_id: Optional[int] = None
    volume_utilization_pct: Optional[float] = None
    actual_departure: Optional[datetime] = None
    actual_arrival: Optional[datetime] = None
    empty_miles: Optional[float] = None
    optimization_score: Optional[float] = None
    stops: Optional[List[Dict[str, Any]]] = None
    items: List[Dict[str, Any]] = []
    tenders: List[Dict[str, Any]] = []

    class Config:
        from_attributes = True


class ShipmentMapPoint(BaseModel):
    id: int
    shipment_number: str
    status: Optional[str] = None
    lat: float
    lon: float
    mode: Optional[str] = None
    carrier_id: Optional[int] = None
    carrier_name: Optional[str] = None
    origin_site_id: int
    destination_site_id: int
    estimated_arrival: Optional[datetime] = None
    last_tracking_update: Optional[datetime] = None

    class Config:
        from_attributes = True


class LaneAnalyticsRow(BaseModel):
    lane_id: int
    origin_name: Optional[str] = None
    destination_name: Optional[str] = None
    primary_mode: Optional[str] = None
    volume: Optional[int] = None
    avg_rate: Optional[float] = None
    avg_cost_per_mile: Optional[float] = None
    avg_transit_days: Optional[float] = None
    carrier_count: Optional[int] = None

    class Config:
        from_attributes = True


class DockFacility(BaseModel):
    site_id: int
    site_name: str
    facility_type: Optional[str] = None
    total_dock_doors: int = 0
    inbound_dock_doors: int = 0
    outbound_dock_doors: int = 0

    class Config:
        from_attributes = True


class AppointmentResponse(BaseModel):
    id: int
    site_id: int
    dock_door_id: Optional[int] = None
    door_number: Optional[str] = None
    shipment_id: Optional[int] = None
    load_id: Optional[int] = None
    appointment_type: Optional[str] = None
    status: Optional[str] = None
    scheduled_start: datetime
    scheduled_end: datetime
    actual_arrival: Optional[datetime] = None
    carrier_id: Optional[int] = None
    driver_name: Optional[str] = None
    trailer_number: Optional[str] = None
    dwell_time_minutes: Optional[int] = None

    class Config:
        from_attributes = True


class ExceptionRow(BaseModel):
    id: int
    shipment_id: int
    shipment_number: Optional[str] = None
    exception_type: Optional[str] = None
    severity: Optional[str] = None
    resolution_status: Optional[str] = None
    description: Optional[str] = None
    detected_at: Optional[datetime] = None
    estimated_delay_hrs: Optional[float] = None
    estimated_cost_impact: Optional[float] = None
    detection_source: Optional[str] = None

    class Config:
        from_attributes = True


class ExceptionSummaryResponse(BaseModel):
    open_count: int = 0
    critical_count: int = 0
    avg_resolution_hours: Optional[float] = None
    total_cost_impact: float = 0.0

    class Config:
        from_attributes = True


class RateRow(BaseModel):
    id: int
    carrier_id: int
    carrier_name: Optional[str] = None
    lane_id: int
    mode: Optional[str] = None
    rate_type: Optional[str] = None
    rate_per_mile: Optional[float] = None
    rate_flat: Optional[float] = None
    min_charge: Optional[float] = None
    fuel_surcharge_pct: Optional[float] = None
    eff_start_date: Optional[date] = None
    eff_end_date: Optional[date] = None
    is_active: bool = True

    class Config:
        from_attributes = True


# ============================================================================
# Router 1: Carriers
# ============================================================================

carriers_router = APIRouter(tags=["carriers"])


@carriers_router.get("/", response_model=Dict[str, Any])
async def list_carriers(
    config_id: Optional[int] = Query(None, description="Config scope"),
    is_active: Optional[bool] = Query(None),
    carrier_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """List all carriers for the tenant."""
    try:
        stmt = select(Carrier).where(Carrier.tenant_id == current_user.tenant_id)
        cfg = await _resolve_config_id(config_id, current_user, db)
        if cfg is not None:
            stmt = stmt.where(or_(Carrier.config_id == cfg, Carrier.config_id.is_(None)))
        if is_active is not None:
            stmt = stmt.where(Carrier.is_active == is_active)
        if carrier_type is not None:
            stmt = stmt.where(Carrier.carrier_type == carrier_type)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(Carrier.name).limit(limit).offset(offset)
        rows = (await db.execute(stmt)).scalars().all()

        items = []
        for c in rows:
            items.append(CarrierSummary(
                id=c.id,
                code=c.code,
                name=c.name,
                carrier_type=_safe_str(c.carrier_type),
                scac=c.scac,
                modes=c.modes,
                equipment_types=c.equipment_types,
                service_regions=c.service_regions,
                is_active=c.is_active,
                onboarding_status=c.onboarding_status,
            ).model_dump())
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


@carriers_router.get("/{carrier_id}", response_model=Dict[str, Any])
async def get_carrier_detail(
    carrier_id: int,
    config_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """Carrier detail with lane coverage."""
    try:
        stmt = select(Carrier).where(
            Carrier.id == carrier_id,
            Carrier.tenant_id == current_user.tenant_id,
        )
        carrier = (await db.execute(stmt)).scalar_one_or_none()
        if carrier is None:
            return {"error": "Carrier not found"}

        # §3.47 Phase 2 — dispatch fields live on CarrierTMSProfile.
        profile_stmt = select(CarrierTMSProfile).where(
            CarrierTMSProfile.carrier_id == carrier_id,
            CarrierTMSProfile.tenant_id == current_user.tenant_id,
        )
        profile = (await db.execute(profile_stmt)).scalar_one_or_none()

        # Fetch carrier lanes
        lane_stmt = select(CarrierLane).where(
            CarrierLane.carrier_id == carrier_id,
            CarrierLane.tenant_id == current_user.tenant_id,
        )
        lanes_result = (await db.execute(lane_stmt)).scalars().all()
        lanes_data = []
        for cl in lanes_result:
            lanes_data.append({
                "id": cl.id,
                "lane_id": cl.lane_id,
                "mode": _safe_str(cl.mode),
                "equipment_type": _safe_str(cl.equipment_type),
                "weekly_capacity": cl.weekly_capacity,
                "avg_transit_days": cl.avg_transit_days,
                "priority": cl.priority,
                "is_primary": cl.is_primary,
                "is_active": cl.is_active,
            })

        detail = CarrierDetail(
            id=carrier.id,
            code=profile.code if profile else None,
            name=carrier.display_name,
            carrier_type=_safe_str(carrier.carrier_type),
            scac=carrier.scac,
            mc_number=carrier.mc_number,
            dot_number=carrier.dot_number,
            usdot_safety_rating=profile.usdot_safety_rating if profile else None,
            modes=profile.modes if profile else None,
            equipment_types=profile.equipment_types if profile else None,
            service_regions=profile.service_regions if profile else None,
            is_active=(carrier.status == "ACTIVE"),
            is_hazmat_certified=(
                profile.is_hazmat_certified if profile else False
            ),
            onboarding_status=profile.onboarding_status if profile else None,
            primary_contact_name=profile.primary_contact_name if profile else None,
            primary_contact_email=profile.primary_contact_email if profile else None,
            dispatch_email=profile.dispatch_email if profile else None,
            dispatch_phone=profile.dispatch_phone if profile else None,
            lane_count=len(lanes_data),
            lanes=lanes_data,
        )
        return detail.model_dump()

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


@carriers_router.get("/{carrier_id}/scorecard", response_model=Dict[str, Any])
async def get_carrier_scorecard(
    carrier_id: int,
    periods: int = Query(4, ge=1, le=12, description="Number of recent periods"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """Carrier scorecard metrics for recent periods."""
    try:
        stmt = (
            select(CarrierScorecard)
            .where(
                CarrierScorecard.carrier_id == carrier_id,
                CarrierScorecard.tenant_id == current_user.tenant_id,
            )
            .order_by(CarrierScorecard.period_start.desc())
            .limit(periods)
        )
        rows = (await db.execute(stmt)).scalars().all()
        items = [
            ScorecardResponse(
                carrier_id=r.carrier_id,
                period_start=r.period_start,
                period_end=r.period_end,
                total_shipments=r.total_shipments or 0,
                total_loads=r.total_loads or 0,
                on_time_pickup_pct=r.on_time_pickup_pct,
                on_time_delivery_pct=r.on_time_delivery_pct,
                avg_cost_per_mile=r.avg_cost_per_mile,
                avg_cost_per_shipment=r.avg_cost_per_shipment,
                damage_rate_pct=r.damage_rate_pct,
                tender_acceptance_rate_pct=r.tender_acceptance_rate_pct,
                tracking_compliance_pct=r.tracking_compliance_pct,
                composite_score=r.composite_score,
                score_components=r.score_components,
            ).model_dump()
            for r in rows
        ]
        return {"items": items, "carrier_id": carrier_id}

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


# ============================================================================
# Router 2: Loads
# ============================================================================

loads_router = APIRouter(tags=["loads"])


@loads_router.get("/", response_model=Dict[str, Any])
async def list_loads(
    config_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None, description="Comma-separated statuses"),
    mode: Optional[str] = Query(None),
    lane_search: Optional[str] = Query(None, description="ILIKE search on origin/dest site names"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """List loads with filters."""
    try:
        stmt = select(Load).where(Load.tenant_id == current_user.tenant_id)
        cfg = await _resolve_config_id(config_id, current_user, db)
        if cfg is not None:
            stmt = stmt.where(or_(Load.config_id == cfg, Load.config_id.is_(None)))

        if status:
            status_list = [s.strip() for s in status.split(",")]
            stmt = stmt.where(Load.status.in_(status_list))
        if mode:
            stmt = stmt.where(Load.mode == mode)
        if date_from:
            stmt = stmt.where(Load.planned_departure >= datetime.combine(date_from, datetime.min.time()))
        if date_to:
            stmt = stmt.where(Load.planned_departure <= datetime.combine(date_to, datetime.max.time()))

        # Lane search: join to origin/dest Site names
        if lane_search:
            origin_site = select(Site.id).where(Site.name.ilike(f"%{lane_search}%")).subquery()
            dest_site = select(Site.id).where(Site.name.ilike(f"%{lane_search}%")).subquery()
            stmt = stmt.where(
                or_(
                    Load.origin_site_id.in_(select(origin_site)),
                    Load.destination_site_id.in_(select(dest_site)),
                )
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(Load.planned_departure.desc().nullslast()).limit(limit).offset(offset)
        rows = (await db.execute(stmt)).scalars().all()

        items = [
            LoadSummary(
                id=r.id,
                load_number=r.load_number,
                status=_safe_str(r.status),
                origin_site_id=r.origin_site_id,
                destination_site_id=r.destination_site_id,
                mode=_safe_str(r.mode),
                carrier_id=r.carrier_id,
                total_weight=r.total_weight,
                total_volume=r.total_volume,
                total_pallets=r.total_pallets,
                weight_utilization_pct=r.weight_utilization_pct,
                planned_departure=r.planned_departure,
                planned_arrival=r.planned_arrival,
                total_cost=r.total_cost,
                cost_per_mile=r.cost_per_mile,
                total_miles=r.total_miles,
            ).model_dump()
            for r in rows
        ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


@loads_router.get("/{load_id}", response_model=Dict[str, Any])
async def get_load_detail(
    load_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """Load detail with items and tender history."""
    try:
        stmt = select(Load).where(
            Load.id == load_id,
            Load.tenant_id == current_user.tenant_id,
        )
        load = (await db.execute(stmt)).scalar_one_or_none()
        if load is None:
            return {"error": "Load not found"}

        # Load items
        items_stmt = select(LoadItem).where(LoadItem.load_id == load_id)
        load_items = (await db.execute(items_stmt)).scalars().all()
        items_data = [
            {
                "id": li.id,
                "shipment_id": li.shipment_id,
                "commodity_id": li.commodity_id,
                "quantity": li.quantity,
                "weight": li.weight,
                "volume": li.volume,
                "pallet_count": li.pallet_count,
                "load_sequence": li.load_sequence,
                "position": li.position,
            }
            for li in load_items
        ]

        # Tender history
        tender_stmt = (
            select(FreightTender)
            .where(FreightTender.load_id == load_id)
            .order_by(FreightTender.tender_sequence)
        )
        tenders = (await db.execute(tender_stmt)).scalars().all()
        tenders_data = [
            {
                "id": t.id,
                "carrier_id": t.carrier_id,
                "tender_sequence": t.tender_sequence,
                "status": _safe_str(t.status),
                "offered_rate": t.offered_rate,
                "counter_rate": t.counter_rate,
                "final_rate": t.final_rate,
                "tendered_at": t.tendered_at.isoformat() if t.tendered_at else None,
                "responded_at": t.responded_at.isoformat() if t.responded_at else None,
                "decline_reason": t.decline_reason,
            }
            for t in tenders
        ]

        detail = LoadDetail(
            id=load.id,
            load_number=load.load_number,
            status=_safe_str(load.status),
            origin_site_id=load.origin_site_id,
            destination_site_id=load.destination_site_id,
            mode=_safe_str(load.mode),
            carrier_id=load.carrier_id,
            equipment_type=_safe_str(load.equipment_type),
            equipment_id=load.equipment_id,
            total_weight=load.total_weight,
            total_volume=load.total_volume,
            total_pallets=load.total_pallets,
            weight_utilization_pct=load.weight_utilization_pct,
            volume_utilization_pct=load.volume_utilization_pct,
            planned_departure=load.planned_departure,
            planned_arrival=load.planned_arrival,
            actual_departure=load.actual_departure,
            actual_arrival=load.actual_arrival,
            total_cost=load.total_cost,
            cost_per_mile=load.cost_per_mile,
            total_miles=load.total_miles,
            empty_miles=load.empty_miles,
            optimization_score=load.optimization_score,
            stops=load.stops,
            items=items_data,
            tenders=tenders_data,
        )
        return detail.model_dump()

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


# ============================================================================
# Router 3: TMS Operations (shipments/map, lanes, dock, exceptions)
# ============================================================================

tms_operations_router = APIRouter(tags=["tms-operations"])


# --- Shipments Map ---

@tms_operations_router.get("/shipments/map", response_model=Dict[str, Any])
async def shipments_map(
    config_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None, description="Comma-separated statuses"),
    mode: Optional[str] = Query(None),
    carrier_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """Shipment positions for the tracking map. Only shipments with lat/lon."""
    try:
        stmt = select(
            Shipment.id,
            Shipment.shipment_number,
            Shipment.status,
            Shipment.current_lat,
            Shipment.current_lon,
            Shipment.mode,
            Shipment.carrier_id,
            Shipment.origin_site_id,
            Shipment.destination_site_id,
            Shipment.estimated_arrival,
            Shipment.last_tracking_update,
        ).where(
            Shipment.tenant_id == current_user.tenant_id,
            Shipment.current_lat.isnot(None),
            Shipment.current_lon.isnot(None),
        )

        cfg = await _resolve_config_id(config_id, current_user, db)
        if cfg is not None:
            stmt = stmt.where(or_(Shipment.config_id == cfg, Shipment.config_id.is_(None)))
        if status:
            status_list = [s.strip() for s in status.split(",")]
            stmt = stmt.where(Shipment.status.in_(status_list))
        if mode:
            stmt = stmt.where(Shipment.mode == mode)
        if carrier_id:
            stmt = stmt.where(Shipment.carrier_id == carrier_id)

        rows = (await db.execute(stmt)).all()

        # Batch-load carrier names for the result set
        carrier_ids = {r.carrier_id for r in rows if r.carrier_id is not None}
        carrier_name_map: Dict[int, str] = {}
        if carrier_ids:
            c_stmt = select(Carrier.id, Carrier.name).where(Carrier.id.in_(carrier_ids))
            for cid, cname in (await db.execute(c_stmt)).all():
                carrier_name_map[cid] = cname

        items = [
            ShipmentMapPoint(
                id=r.id,
                shipment_number=r.shipment_number,
                status=_safe_str(r.status),
                lat=r.current_lat,
                lon=r.current_lon,
                mode=_safe_str(r.mode),
                carrier_id=r.carrier_id,
                carrier_name=carrier_name_map.get(r.carrier_id) if r.carrier_id else None,
                origin_site_id=r.origin_site_id,
                destination_site_id=r.destination_site_id,
                estimated_arrival=r.estimated_arrival,
                last_tracking_update=r.last_tracking_update,
            ).model_dump()
            for r in rows
        ]
        return {"items": items, "total": len(items)}

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


# --- Lane Analytics ---

@tms_operations_router.get("/lanes/analytics", response_model=Dict[str, Any])
async def lane_analytics(
    config_id: Optional[int] = Query(None),
    mode: Optional[str] = Query(None),
    period: int = Query(30, description="Lookback days: 7, 30, or 90"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """Lane performance metrics aggregated from LaneProfile and shipment history."""
    try:
        cfg = await _resolve_config_id(config_id, current_user, db)

        # Start with LaneProfile for enriched lane data
        stmt = (
            select(
                LaneProfile.lane_id,
                LaneProfile.primary_mode,
                LaneProfile.avg_weekly_volume,
                LaneProfile.avg_cost_per_mile,
                LaneProfile.avg_transit_days,
            )
            .where(LaneProfile.tenant_id == current_user.tenant_id, LaneProfile.is_active.is_(True))
        )
        if cfg is not None:
            stmt = stmt.where(LaneProfile.config_id == cfg)
        if mode:
            stmt = stmt.where(LaneProfile.primary_mode == mode)

        stmt = stmt.limit(limit).offset(offset)
        lane_rows = (await db.execute(stmt)).all()

        # Collect lane_ids to resolve names
        lane_ids = [r.lane_id for r in lane_rows]
        lane_name_map: Dict[int, Dict[str, str]] = {}
        if lane_ids:
            # Join TransportationLane -> Site for origin/dest names
            lane_site_stmt = (
                select(
                    TransportationLane.id,
                    Site.name.label("origin_name"),
                )
                .outerjoin(Site, TransportationLane.from_site_id == Site.id)
                .where(TransportationLane.id.in_(lane_ids))
            )
            for lid, oname in (await db.execute(lane_site_stmt)).all():
                lane_name_map.setdefault(lid, {})["origin_name"] = oname

            lane_dest_stmt = (
                select(
                    TransportationLane.id,
                    Site.name.label("dest_name"),
                )
                .outerjoin(Site, TransportationLane.to_site_id == Site.id)
                .where(TransportationLane.id.in_(lane_ids))
            )
            for lid, dname in (await db.execute(lane_dest_stmt)).all():
                lane_name_map.setdefault(lid, {})["destination_name"] = dname

        # Count distinct carriers per lane from CarrierLane
        carrier_count_map: Dict[int, int] = {}
        if lane_ids:
            cc_stmt = (
                select(
                    CarrierLane.lane_id,
                    func.count(func.distinct(CarrierLane.carrier_id)).label("cnt"),
                )
                .where(CarrierLane.lane_id.in_(lane_ids), CarrierLane.is_active.is_(True))
                .group_by(CarrierLane.lane_id)
            )
            for lid, cnt in (await db.execute(cc_stmt)).all():
                carrier_count_map[lid] = cnt

        items = []
        for r in lane_rows:
            names = lane_name_map.get(r.lane_id, {})
            items.append(LaneAnalyticsRow(
                lane_id=r.lane_id,
                origin_name=names.get("origin_name"),
                destination_name=names.get("destination_name"),
                primary_mode=r.primary_mode,
                volume=r.avg_weekly_volume,
                avg_rate=None,
                avg_cost_per_mile=r.avg_cost_per_mile,
                avg_transit_days=r.avg_transit_days,
                carrier_count=carrier_count_map.get(r.lane_id, 0),
            ).model_dump())

        return {"items": items, "period_days": period, "total": len(items)}

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


# --- Dock Operations ---

@tms_operations_router.get("/dock/facilities", response_model=Dict[str, Any])
async def dock_facilities(
    config_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """List facilities with dock doors."""
    try:
        cfg = await _resolve_config_id(config_id, current_user, db)
        stmt = (
            select(
                FacilityConfig.site_id,
                Site.name.label("site_name"),
                FacilityConfig.facility_type,
                FacilityConfig.total_dock_doors,
                FacilityConfig.inbound_dock_doors,
                FacilityConfig.outbound_dock_doors,
            )
            .join(Site, FacilityConfig.site_id == Site.id)
            .where(
                FacilityConfig.tenant_id == current_user.tenant_id,
                FacilityConfig.total_dock_doors > 0,
            )
        )
        if cfg is not None:
            stmt = stmt.where(FacilityConfig.config_id == cfg)

        rows = (await db.execute(stmt)).all()
        items = [
            DockFacility(
                site_id=r.site_id,
                site_name=r.site_name,
                facility_type=_safe_str(r.facility_type),
                total_dock_doors=r.total_dock_doors or 0,
                inbound_dock_doors=r.inbound_dock_doors or 0,
                outbound_dock_doors=r.outbound_dock_doors or 0,
            ).model_dump()
            for r in rows
        ]
        return {"items": items}

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


@tms_operations_router.get("/dock/schedule", response_model=Dict[str, Any])
async def dock_schedule(
    site_id: int = Query(..., description="Facility site_id"),
    schedule_date: date = Query(..., description="Date to view"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """Dock appointments for a facility on a given date."""
    try:
        day_start = datetime.combine(schedule_date, datetime.min.time())
        day_end = datetime.combine(schedule_date, datetime.max.time())

        stmt = (
            select(Appointment, DockDoor.door_number)
            .outerjoin(DockDoor, Appointment.dock_door_id == DockDoor.id)
            .where(
                Appointment.site_id == site_id,
                Appointment.tenant_id == current_user.tenant_id,
                Appointment.scheduled_start >= day_start,
                Appointment.scheduled_start <= day_end,
            )
            .order_by(Appointment.scheduled_start)
        )
        rows = (await db.execute(stmt)).all()

        items = [
            AppointmentResponse(
                id=appt.id,
                site_id=appt.site_id,
                dock_door_id=appt.dock_door_id,
                door_number=door_num,
                shipment_id=appt.shipment_id,
                load_id=appt.load_id,
                appointment_type=_safe_str(appt.appointment_type),
                status=_safe_str(appt.status),
                scheduled_start=appt.scheduled_start,
                scheduled_end=appt.scheduled_end,
                actual_arrival=appt.actual_arrival,
                carrier_id=appt.carrier_id,
                driver_name=appt.driver_name,
                trailer_number=appt.trailer_number,
                dwell_time_minutes=appt.dwell_time_minutes,
            ).model_dump()
            for appt, door_num in rows
        ]
        return {"items": items, "site_id": site_id, "date": schedule_date.isoformat()}

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


@tms_operations_router.get("/dock/inbound-etas", response_model=Dict[str, Any])
async def dock_inbound_etas(
    site_id: int = Query(..., description="Destination facility"),
    hours_ahead: int = Query(24, ge=1, le=168, description="Lookahead window in hours"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """Inbound shipments with predicted arrival at a facility."""
    try:
        cutoff = datetime.utcnow() + timedelta(hours=hours_ahead)
        stmt = (
            select(
                Shipment.id,
                Shipment.shipment_number,
                Shipment.status,
                Shipment.carrier_id,
                Shipment.mode,
                Shipment.estimated_arrival,
                Shipment.last_tracking_update,
                Shipment.current_lat,
                Shipment.current_lon,
            )
            .where(
                Shipment.destination_site_id == site_id,
                Shipment.tenant_id == current_user.tenant_id,
                Shipment.status.in_([
                    ShipmentStatus.DISPATCHED,
                    ShipmentStatus.IN_TRANSIT,
                    ShipmentStatus.OUT_FOR_DELIVERY,
                ]),
                or_(
                    Shipment.estimated_arrival <= cutoff,
                    Shipment.estimated_arrival.is_(None),
                ),
            )
            .order_by(Shipment.estimated_arrival.asc().nullslast())
        )
        rows = (await db.execute(stmt)).all()
        items = [
            {
                "id": r.id,
                "shipment_number": r.shipment_number,
                "status": _safe_str(r.status),
                "carrier_id": r.carrier_id,
                "mode": _safe_str(r.mode),
                "estimated_arrival": r.estimated_arrival.isoformat() if r.estimated_arrival else None,
                "last_tracking_update": r.last_tracking_update.isoformat() if r.last_tracking_update else None,
                "current_lat": r.current_lat,
                "current_lon": r.current_lon,
            }
            for r in rows
        ]
        return {"items": items, "site_id": site_id, "hours_ahead": hours_ahead}

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


@tms_operations_router.get("/dock/gate-queue", response_model=Dict[str, Any])
async def dock_gate_queue(
    site_id: int = Query(..., description="Facility site_id"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """Current gate queue — appointments checked in but not yet at dock."""
    try:
        stmt = (
            select(Appointment, DockDoor.door_number)
            .outerjoin(DockDoor, Appointment.dock_door_id == DockDoor.id)
            .where(
                Appointment.site_id == site_id,
                Appointment.tenant_id == current_user.tenant_id,
                Appointment.status.in_([
                    AppointmentStatus.CHECKED_IN,
                    AppointmentStatus.CONFIRMED,
                    AppointmentStatus.AT_DOCK,
                    AppointmentStatus.LOADING,
                    AppointmentStatus.UNLOADING,
                ]),
            )
            .order_by(Appointment.actual_arrival.asc().nullslast(), Appointment.scheduled_start.asc())
        )
        rows = (await db.execute(stmt)).all()
        items = [
            {
                "id": appt.id,
                "dock_door_id": appt.dock_door_id,
                "door_number": door_num,
                "shipment_id": appt.shipment_id,
                "load_id": appt.load_id,
                "appointment_type": _safe_str(appt.appointment_type),
                "status": _safe_str(appt.status),
                "scheduled_start": appt.scheduled_start.isoformat(),
                "actual_arrival": appt.actual_arrival.isoformat() if appt.actual_arrival else None,
                "carrier_id": appt.carrier_id,
                "driver_name": appt.driver_name,
                "trailer_number": appt.trailer_number,
                "wait_time_minutes": appt.wait_time_minutes,
            }
            for appt, door_num in rows
        ]
        return {"items": items, "site_id": site_id}

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


# --- Exceptions ---

@tms_operations_router.get("/exceptions", response_model=Dict[str, Any])
async def list_exceptions(
    exception_type: Optional[str] = Query(None, alias="type"),
    severity: Optional[str] = Query(None),
    carrier_id: Optional[int] = Query(None),
    resolution_status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """List shipment exceptions, sorted by impact (severity weight * cost)."""
    try:
        # Severity weight for sort ordering
        severity_weight = case(
            (ShipmentException.severity == ExceptionSeverity.CRITICAL, 4),
            (ShipmentException.severity == ExceptionSeverity.HIGH, 3),
            (ShipmentException.severity == ExceptionSeverity.MEDIUM, 2),
            (ShipmentException.severity == ExceptionSeverity.LOW, 1),
            else_=0,
        )

        stmt = (
            select(ShipmentException, Shipment.shipment_number)
            .join(Shipment, ShipmentException.shipment_id == Shipment.id)
            .where(ShipmentException.tenant_id == current_user.tenant_id)
        )

        if exception_type:
            stmt = stmt.where(ShipmentException.exception_type == exception_type)
        if severity:
            stmt = stmt.where(ShipmentException.severity == severity)
        if carrier_id:
            stmt = stmt.where(Shipment.carrier_id == carrier_id)
        if resolution_status:
            stmt = stmt.where(ShipmentException.resolution_status == resolution_status)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(
            (severity_weight * func.coalesce(ShipmentException.estimated_cost_impact, 0)).desc(),
            ShipmentException.detected_at.desc(),
        ).limit(limit).offset(offset)

        rows = (await db.execute(stmt)).all()
        items = [
            ExceptionRow(
                id=exc.id,
                shipment_id=exc.shipment_id,
                shipment_number=snum,
                exception_type=_safe_str(exc.exception_type),
                severity=_safe_str(exc.severity),
                resolution_status=_safe_str(exc.resolution_status),
                description=exc.description,
                detected_at=exc.detected_at,
                estimated_delay_hrs=exc.estimated_delay_hrs,
                estimated_cost_impact=exc.estimated_cost_impact,
                detection_source=exc.detection_source,
            ).model_dump()
            for exc, snum in rows
        ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


@tms_operations_router.get("/exceptions/summary", response_model=Dict[str, Any])
async def exceptions_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """Exception KPI summary: open count, critical, avg resolution, cost impact."""
    try:
        open_statuses = [
            "DETECTED",
            "INVESTIGATING",
            "ACTION_TAKEN",
            "ESCALATED",
        ]

        base = select(ShipmentException).where(
            ShipmentException.tenant_id == current_user.tenant_id,
            ShipmentException.resolution_status.in_(open_statuses),
        )
        open_rows = (await db.execute(base)).scalars().all()

        open_count = len(open_rows)
        critical_count = sum(
            1 for r in open_rows if _safe_str(r.severity) == ExceptionSeverity.CRITICAL.value
        )
        total_cost = sum(r.estimated_cost_impact or 0 for r in open_rows)

        # Avg resolution hours from resolved exceptions (last 90 days)
        cutoff = datetime.utcnow() - timedelta(days=90)
        resolved_stmt = select(
            func.avg(
                func.extract("epoch", ShipmentException.resolved_at - ShipmentException.detected_at) / 3600
            )
        ).where(
            ShipmentException.tenant_id == current_user.tenant_id,
            ShipmentException.resolved_at.isnot(None),
            ShipmentException.detected_at >= cutoff,
        )
        avg_hrs = (await db.execute(resolved_stmt)).scalar()

        return ExceptionSummaryResponse(
            open_count=open_count,
            critical_count=critical_count,
            avg_resolution_hours=round(avg_hrs, 1) if avg_hrs else None,
            total_cost_impact=round(total_cost, 2),
        ).model_dump()

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return ExceptionSummaryResponse().model_dump()


# ============================================================================
# Router 4: Rates
# ============================================================================

rates_router = APIRouter(tags=["rates"])


@rates_router.get("/contracts", response_model=Dict[str, Any])
async def list_contract_rates(
    config_id: Optional[int] = Query(None),
    lane_id: Optional[int] = Query(None),
    mode: Optional[str] = Query(None),
    carrier_id: Optional[int] = Query(None),
    active_only: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """Contract freight rates with filters."""
    try:
        stmt = select(FreightRate, Carrier.name.label("carrier_name")).join(
            Carrier, FreightRate.carrier_id == Carrier.id
        ).where(
            FreightRate.tenant_id == current_user.tenant_id,
            FreightRate.rate_type == RateType.CONTRACT,
        )

        cfg = await _resolve_config_id(config_id, current_user, db)
        if cfg is not None:
            stmt = stmt.where(or_(FreightRate.config_id == cfg, FreightRate.config_id.is_(None)))
        if lane_id is not None:
            stmt = stmt.where(FreightRate.lane_id == lane_id)
        if mode:
            stmt = stmt.where(FreightRate.mode == mode)
        if carrier_id:
            stmt = stmt.where(FreightRate.carrier_id == carrier_id)
        if active_only:
            stmt = stmt.where(FreightRate.is_active.is_(True))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(FreightRate.eff_start_date.desc()).limit(limit).offset(offset)
        rows = (await db.execute(stmt)).all()

        items = [
            RateRow(
                id=rate.id,
                carrier_id=rate.carrier_id,
                carrier_name=cname,
                lane_id=rate.lane_id,
                mode=_safe_str(rate.mode),
                rate_type=_safe_str(rate.rate_type),
                rate_per_mile=rate.rate_per_mile,
                rate_flat=rate.rate_flat,
                min_charge=rate.min_charge,
                fuel_surcharge_pct=rate.fuel_surcharge_pct,
                eff_start_date=rate.eff_start_date,
                eff_end_date=rate.eff_end_date,
                is_active=rate.is_active,
            ).model_dump()
            for rate, cname in rows
        ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


@rates_router.get("/spots", response_model=Dict[str, Any])
async def list_spot_rates(
    config_id: Optional[int] = Query(None),
    lane_id: Optional[int] = Query(None),
    days_back: int = Query(30, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """Recent spot quotes from FreightRate where rate_type=SPOT."""
    try:
        cutoff = date.today() - timedelta(days=days_back)
        stmt = select(FreightRate, Carrier.name.label("carrier_name")).join(
            Carrier, FreightRate.carrier_id == Carrier.id
        ).where(
            FreightRate.tenant_id == current_user.tenant_id,
            FreightRate.rate_type == RateType.SPOT,
            FreightRate.created_at >= datetime.combine(cutoff, datetime.min.time()),
        )

        cfg = await _resolve_config_id(config_id, current_user, db)
        if cfg is not None:
            stmt = stmt.where(or_(FreightRate.config_id == cfg, FreightRate.config_id.is_(None)))
        if lane_id is not None:
            stmt = stmt.where(FreightRate.lane_id == lane_id)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(FreightRate.created_at.desc()).limit(limit).offset(offset)
        rows = (await db.execute(stmt)).all()

        items = [
            RateRow(
                id=rate.id,
                carrier_id=rate.carrier_id,
                carrier_name=cname,
                lane_id=rate.lane_id,
                mode=_safe_str(rate.mode),
                rate_type=_safe_str(rate.rate_type),
                rate_per_mile=rate.rate_per_mile,
                rate_flat=rate.rate_flat,
                min_charge=rate.min_charge,
                fuel_surcharge_pct=rate.fuel_surcharge_pct,
                eff_start_date=rate.eff_start_date,
                eff_end_date=rate.eff_end_date,
                is_active=rate.is_active,
            ).model_dump()
            for rate, cname in rows
        ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


@rates_router.get("/cards", response_model=Dict[str, Any])
async def rate_cards(
    config_id: Optional[int] = Query(None),
    carrier_id: Optional[int] = Query(None),
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """Rate cards grouped by carrier — all active contract rates per carrier."""
    try:
        stmt = select(FreightRate, Carrier.name.label("carrier_name"), Carrier.code.label("carrier_code")).join(
            Carrier, FreightRate.carrier_id == Carrier.id
        ).where(
            FreightRate.tenant_id == current_user.tenant_id,
            FreightRate.rate_type == RateType.CONTRACT,
        )

        cfg = await _resolve_config_id(config_id, current_user, db)
        if cfg is not None:
            stmt = stmt.where(or_(FreightRate.config_id == cfg, FreightRate.config_id.is_(None)))
        if carrier_id:
            stmt = stmt.where(FreightRate.carrier_id == carrier_id)
        if active_only:
            stmt = stmt.where(FreightRate.is_active.is_(True))

        stmt = stmt.order_by(Carrier.name, FreightRate.lane_id)
        rows = (await db.execute(stmt)).all()

        # Group by carrier
        cards: Dict[int, Dict[str, Any]] = {}
        for rate, cname, ccode in rows:
            cid = rate.carrier_id
            if cid not in cards:
                cards[cid] = {
                    "carrier_id": cid,
                    "carrier_name": cname,
                    "carrier_code": ccode,
                    "rates": [],
                }
            cards[cid]["rates"].append({
                "id": rate.id,
                "lane_id": rate.lane_id,
                "mode": _safe_str(rate.mode),
                "rate_per_mile": rate.rate_per_mile,
                "rate_flat": rate.rate_flat,
                "min_charge": rate.min_charge,
                "fuel_surcharge_pct": rate.fuel_surcharge_pct,
                "eff_start_date": rate.eff_start_date.isoformat() if rate.eff_start_date else None,
                "eff_end_date": rate.eff_end_date.isoformat() if rate.eff_end_date else None,
            })

        return {"items": list(cards.values()), "total": len(cards)}

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


# ============================================================================
# CRUD: Carrier write endpoints (POST / PATCH / DELETE)
# ============================================================================

class CarrierWritePayload(BaseModel):
    code: str
    name: str
    carrier_type: str = "FOR_HIRE"
    scac: Optional[str] = None
    mc_number: Optional[str] = None
    dot_number: Optional[str] = None
    modes: Optional[List[str]] = None
    equipment_types: Optional[List[str]] = None
    service_regions: Optional[List[str]] = None
    is_hazmat_certified: Optional[bool] = None
    is_bonded: Optional[bool] = None
    insurance_limit: Optional[float] = None
    primary_contact_name: Optional[str] = None
    primary_contact_email: Optional[str] = None
    primary_contact_phone: Optional[str] = None
    dispatch_email: Optional[str] = None
    dispatch_phone: Optional[str] = None
    is_active: Optional[bool] = True
    onboarding_status: Optional[str] = "ACTIVE"
    config_id: Optional[int] = None


@carriers_router.post("/", response_model=Dict[str, Any])
async def create_carrier(
    body: CarrierWritePayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Create a new carrier for the current tenant.

    §3.47 Phase 3: Carrier holds canonical identity (Core schema);
    dispatch fields land on a sibling CarrierTMSProfile row.
    """
    cfg = await _resolve_config_id(body.config_id, current_user, db)
    c = Carrier(
        tenant_id=current_user.tenant_id,
        scac=body.scac,
        display_name=body.name,
        carrier_type=body.carrier_type,
        status=(
            "ACTIVE" if (body.is_active is None or body.is_active) else "INACTIVE"
        ),
        mc_number=body.mc_number,
        dot_number=body.dot_number,
        currency="USD",
        payment_terms_days=30,
    )
    db.add(c)
    await db.flush()  # populate c.id

    profile = CarrierTMSProfile(
        carrier_id=c.id,
        tenant_id=current_user.tenant_id,
        config_id=cfg,
        code=body.code,
        modes=body.modes or [],
        equipment_types=body.equipment_types or [],
        service_regions=body.service_regions or [],
        is_hazmat_certified=body.is_hazmat_certified or False,
        is_bonded=body.is_bonded or False,
        insurance_limit=body.insurance_limit,
        primary_contact_name=body.primary_contact_name,
        primary_contact_email=body.primary_contact_email,
        primary_contact_phone=body.primary_contact_phone,
        dispatch_email=body.dispatch_email,
        dispatch_phone=body.dispatch_phone,
        onboarding_status=body.onboarding_status or "ACTIVE",
        source="manual",
    )
    db.add(profile)
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "code": body.code, "name": c.display_name}


@carriers_router.patch("/{carrier_id}", response_model=Dict[str, Any])
async def update_carrier(
    carrier_id: int,
    body: CarrierWritePayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Update an existing carrier.

    §3.47 Phase 3: split writes between Core's Carrier (canonical
    identity fields) and TMS's CarrierTMSProfile (dispatch fields).
    """
    stmt = select(Carrier).where(
        Carrier.id == carrier_id,
        Carrier.tenant_id == current_user.tenant_id,
    )
    c = (await db.execute(stmt)).scalar_one_or_none()
    if not c:
        raise HTTPException(404, f"Carrier {carrier_id} not found")

    # Core canonical fields on Carrier itself.
    if body.name is not None:
        c.display_name = body.name
    for f in ("carrier_type", "scac", "mc_number", "dot_number"):
        v = getattr(body, f)
        if v is not None:
            setattr(c, f, v)
    if body.is_active is not None:
        c.status = "ACTIVE" if body.is_active else "INACTIVE"

    # Dispatch fields on CarrierTMSProfile (upsert).
    profile_stmt = select(CarrierTMSProfile).where(
        CarrierTMSProfile.carrier_id == carrier_id,
        CarrierTMSProfile.tenant_id == current_user.tenant_id,
    )
    profile = (await db.execute(profile_stmt)).scalar_one_or_none()
    if profile is None:
        profile = CarrierTMSProfile(
            carrier_id=carrier_id, tenant_id=current_user.tenant_id,
        )
        db.add(profile)
    for f in (
        "code", "modes", "equipment_types", "service_regions",
        "is_hazmat_certified", "is_bonded", "insurance_limit",
        "primary_contact_name", "primary_contact_email",
        "primary_contact_phone", "dispatch_email", "dispatch_phone",
        "onboarding_status",
    ):
        v = getattr(body, f, None)
        if v is not None:
            setattr(profile, f, v)

    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "code": profile.code, "name": c.display_name, "updated": True}


@carriers_router.delete("/{carrier_id}", response_model=Dict[str, Any])
async def delete_carrier(
    carrier_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Soft-delete a carrier (Core status=INACTIVE; profile
    onboarding_status=SUSPENDED).

    §3.47 Phase 3: ``status`` lives on Core's Carrier;
    ``onboarding_status`` lives on TMS's CarrierTMSProfile.
    """
    stmt = select(Carrier).where(
        Carrier.id == carrier_id,
        Carrier.tenant_id == current_user.tenant_id,
    )
    c = (await db.execute(stmt)).scalar_one_or_none()
    if not c:
        raise HTTPException(404, f"Carrier {carrier_id} not found")
    c.status = "INACTIVE"

    profile_stmt = select(CarrierTMSProfile).where(
        CarrierTMSProfile.carrier_id == carrier_id,
        CarrierTMSProfile.tenant_id == current_user.tenant_id,
    )
    profile = (await db.execute(profile_stmt)).scalar_one_or_none()
    if profile is not None:
        profile.onboarding_status = "SUSPENDED"

    await db.commit()
    return {"id": carrier_id, "deleted": True}


# ============================================================================
# CRUD: FreightRate write endpoints (POST / PATCH / DELETE)
# ============================================================================

class FreightRateWritePayload(BaseModel):
    carrier_id: int
    lane_id: int
    mode: str
    equipment_type: Optional[str] = None
    rate_type: str = "CONTRACT"
    rate_per_mile: Optional[float] = None
    rate_flat: Optional[float] = None
    rate_per_cwt: Optional[float] = None
    rate_per_unit: Optional[float] = None
    min_charge: Optional[float] = None
    fuel_surcharge_pct: Optional[float] = None
    fuel_surcharge_method: Optional[str] = None
    accessorial_schedule: Optional[Dict[str, float]] = None
    eff_start_date: str  # ISO date
    eff_end_date: str
    contract_number: Optional[str] = None
    is_active: Optional[bool] = True
    min_volume_per_week: Optional[int] = None
    max_volume_per_week: Optional[int] = None
    config_id: Optional[int] = None


def _parse_iso_date(s: str):
    from datetime import date
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, f"Invalid ISO date: {s}")


@rates_router.post("/", response_model=Dict[str, Any])
async def create_rate(
    body: FreightRateWritePayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Create a new freight rate (contract or spot)."""
    cfg = await _resolve_config_id(body.config_id, current_user, db)
    r = FreightRate(
        tenant_id=current_user.tenant_id,
        config_id=cfg,
        carrier_id=body.carrier_id,
        lane_id=body.lane_id,
        mode=body.mode,
        equipment_type=body.equipment_type,
        rate_type=body.rate_type,
        rate_per_mile=body.rate_per_mile,
        rate_flat=body.rate_flat,
        rate_per_cwt=body.rate_per_cwt,
        rate_per_unit=body.rate_per_unit,
        min_charge=body.min_charge,
        fuel_surcharge_pct=body.fuel_surcharge_pct,
        fuel_surcharge_method=body.fuel_surcharge_method,
        accessorial_schedule=body.accessorial_schedule,
        eff_start_date=_parse_iso_date(body.eff_start_date),
        eff_end_date=_parse_iso_date(body.eff_end_date),
        contract_number=body.contract_number,
        is_active=body.is_active if body.is_active is not None else True,
        min_volume_per_week=body.min_volume_per_week,
        max_volume_per_week=body.max_volume_per_week,
        source="manual",
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return {"id": r.id, "carrier_id": r.carrier_id, "lane_id": r.lane_id}


@rates_router.patch("/{rate_id}", response_model=Dict[str, Any])
async def update_rate(
    rate_id: int,
    body: FreightRateWritePayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Update an existing freight rate."""
    stmt = select(FreightRate).where(
        FreightRate.id == rate_id,
        FreightRate.tenant_id == current_user.tenant_id,
    )
    r = (await db.execute(stmt)).scalar_one_or_none()
    if not r:
        raise HTTPException(404, f"Rate {rate_id} not found")
    for f in ["carrier_id", "lane_id", "mode", "equipment_type", "rate_type",
              "rate_per_mile", "rate_flat", "rate_per_cwt", "rate_per_unit",
              "min_charge", "fuel_surcharge_pct", "fuel_surcharge_method",
              "accessorial_schedule", "contract_number", "is_active",
              "min_volume_per_week", "max_volume_per_week"]:
        v = getattr(body, f)
        if v is not None:
            setattr(r, f, v)
    if body.eff_start_date:
        r.eff_start_date = _parse_iso_date(body.eff_start_date)
    if body.eff_end_date:
        r.eff_end_date = _parse_iso_date(body.eff_end_date)
    await db.commit()
    await db.refresh(r)
    return {"id": r.id, "updated": True}


@rates_router.delete("/{rate_id}", response_model=Dict[str, Any])
async def delete_rate(
    rate_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Soft-delete a freight rate (set is_active=False)."""
    stmt = select(FreightRate).where(
        FreightRate.id == rate_id,
        FreightRate.tenant_id == current_user.tenant_id,
    )
    r = (await db.execute(stmt)).scalar_one_or_none()
    if not r:
        raise HTTPException(404, f"Rate {rate_id} not found")
    r.is_active = False
    await db.commit()
    return {"id": rate_id, "deleted": True}


# ============================================================================
# Shipment Route Geometry — lazy OSRM cache
# ============================================================================

import json as _json
import os as _os

routes_router = APIRouter(tags=["shipment-routes"])


@routes_router.get("/{shipment_id}/route", response_model=Dict[str, Any])
async def get_shipment_route(
    shipment_id: int,
    refresh: bool = Query(False, description="Bypass cache and re-fetch from OSRM"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return the road-network route for a shipment as GeoJSON LineString.

    On first call, fetches from OSRM (public router.project-osrm.org by
    default; override with `OSRM_BASE_URL` env var for a self-hosted
    instance). Result cached in `shipment_route_cache` keyed by
    shipment_id; subsequent calls return the cache.

    Falls back to a straight-line GeoJSON from origin → destination
    coordinates if the routing API is unreachable, so the map always has
    SOMETHING to draw.
    """
    from app.models.shipment_route_cache import ShipmentRouteCache
    from app.models.supply_chain_config import Site
    import httpx

    # Tenant-scoped shipment lookup
    s_stmt = select(Shipment).where(
        Shipment.id == shipment_id,
        Shipment.tenant_id == current_user.tenant_id,
    )
    shipment = (await db.execute(s_stmt)).scalar_one_or_none()
    if not shipment:
        raise HTTPException(404, f"Shipment {shipment_id} not found")

    # Cache hit?
    if not refresh:
        cache_stmt = select(ShipmentRouteCache).where(
            ShipmentRouteCache.shipment_id == shipment_id,
        )
        cached = (await db.execute(cache_stmt)).scalar_one_or_none()
        if cached:
            return {
                "shipment_id": shipment_id,
                "geometry": _json.loads(cached.geometry_geojson),
                "distance_meters": cached.distance_meters,
                "duration_seconds": cached.duration_seconds,
                "source": cached.source,
                "cached": True,
            }

    # Resolve origin + destination coordinates via Site
    origin_stmt = select(Site).where(Site.id == shipment.origin_site_id)
    dest_stmt = select(Site).where(Site.id == shipment.destination_site_id)
    origin = (await db.execute(origin_stmt)).scalar_one_or_none()
    dest = (await db.execute(dest_stmt)).scalar_one_or_none()

    def _coord(site, fallback_lat, fallback_lon):
        lat = getattr(site, "latitude", None) or fallback_lat
        lon = getattr(site, "longitude", None) or fallback_lon
        return lat, lon

    o_lat, o_lon = _coord(origin, shipment.current_lat, shipment.current_lon)
    d_lat, d_lon = _coord(dest, None, None)

    if not (o_lat and o_lon and d_lat and d_lon):
        raise HTTPException(
            422,
            "Origin or destination has no lat/lon — populate Site coordinates "
            "first or wait for tracking to populate current_lat/lon",
        )

    osrm_base = _os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org")
    osrm_url = (
        f"{osrm_base}/route/v1/driving/"
        f"{o_lon},{o_lat};{d_lon},{d_lat}"
        "?overview=full&geometries=geojson"
    )

    geometry = None
    distance = None
    duration = None
    source = "osrm"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(osrm_url)
        if r.status_code == 200:
            data = r.json()
            if data.get("routes"):
                rt = data["routes"][0]
                geometry = rt.get("geometry")  # GeoJSON LineString
                distance = rt.get("distance")
                duration = rt.get("duration")
    except Exception as e:  # noqa: BLE001
        logger.warning("OSRM lookup failed for shipment %s: %s", shipment_id, e)

    if geometry is None:
        # Fallback: straight line
        geometry = {
            "type": "LineString",
            "coordinates": [[o_lon, o_lat], [d_lon, d_lat]],
        }
        source = "straight_line"

    # Upsert into cache
    cache_stmt = select(ShipmentRouteCache).where(
        ShipmentRouteCache.shipment_id == shipment_id,
    )
    cached = (await db.execute(cache_stmt)).scalar_one_or_none()
    if cached:
        cached.geometry_geojson = _json.dumps(geometry)
        cached.distance_meters = distance
        cached.duration_seconds = duration
        cached.source = source
    else:
        cached = ShipmentRouteCache(
            shipment_id=shipment_id,
            geometry_geojson=_json.dumps(geometry),
            distance_meters=distance,
            duration_seconds=duration,
            source=source,
        )
        db.add(cached)
    await db.commit()

    return {
        "shipment_id": shipment_id,
        "geometry": geometry,
        "distance_meters": distance,
        "duration_seconds": duration,
        "source": source,
        "cached": False,
    }


# ============================================================================
# CRUD: Load status update (drives Load Board drag-and-drop)
# ============================================================================

class LoadStatusUpdate(BaseModel):
    status: str


@loads_router.patch("/{load_id}/status", response_model=Dict[str, Any])
async def update_load_status(
    load_id: int,
    body: LoadStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Move a load between Kanban columns. Validates the new status against
    the LoadStatus enum. Backwards transitions (e.g. IN_TRANSIT → PLANNING)
    are allowed since this is also used to undo accidental drops; auditors
    see every transition in the row's updated_at timestamp."""
    valid = {s.value for s in LoadStatus}
    if body.status not in valid:
        raise HTTPException(400, f"Invalid status: {body.status}. Must be one of {sorted(valid)}")
    stmt = select(Load).where(
        Load.id == load_id,
        Load.tenant_id == current_user.tenant_id,
    )
    load = (await db.execute(stmt)).scalar_one_or_none()
    if not load:
        raise HTTPException(404, f"Load {load_id} not found")
    prev = _safe_str(load.status)
    load.status = body.status
    await db.commit()
    return {"id": load_id, "status": body.status, "previous_status": prev}


# ============================================================================
# Transportation Plan view — supports plan_version toggle per Tactical
# Planning Re-Architecture Phase 0. See
# docs/TACTICAL_PLANNING_REARCHITECTURE.md for the four canonical values.
# ============================================================================

plans_router = APIRouter(tags=["transportation-plans"])


@plans_router.get("/", response_model=Dict[str, Any])
async def list_transportation_plans(
    config_id: Optional[int] = Query(None, description="Config scope"),
    plan_version: Optional[str] = Query(
        None,
        description=(
            "One of: unconstrained_reference, constrained_live, "
            "erp_baseline, decision_action. Omit to return all versions."
        ),
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    response: Response = None,
):
    """List transportation plans for the tenant, filtered by plan_version.

    Today, only `constrained_live` rows are populated. Phase 1 of the
    Tactical Planning Re-Architecture will start producing
    `unconstrained_reference` rows nightly; ERP extractors will seed
    `erp_baseline`; the Decision Stream override flow will seed
    `decision_action`. Until then this endpoint still returns the full
    list so the UI can show "coming soon" placeholders.
    """
    try:
        from app.models.tms_planning import PlanVersion, PLANNING_IS_CONSTRAINED

        stmt = select(TransportationPlan).where(
            TransportationPlan.tenant_id == current_user.tenant_id,
        )
        cfg = await _resolve_config_id(config_id, current_user, db)
        if cfg is not None:
            stmt = stmt.where(TransportationPlan.config_id == cfg)
        if plan_version:
            stmt = stmt.where(TransportationPlan.plan_version == plan_version)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = (
            stmt.order_by(TransportationPlan.plan_start_date.desc())
            .limit(limit).offset(offset)
        )
        rows = (await db.execute(stmt)).scalars().all()

        items = [
            {
                "id": p.id,
                "config_id": p.config_id,
                "plan_version": p.plan_version,
                "plan_name": p.plan_name,
                "status": _safe_str(p.status),
                "plan_start_date": p.plan_start_date.isoformat() if p.plan_start_date else None,
                "plan_end_date": p.plan_end_date.isoformat() if p.plan_end_date else None,
                "total_planned_loads": p.total_planned_loads,
                "total_planned_shipments": p.total_planned_shipments,
                "total_estimated_cost": p.total_estimated_cost,
                "avg_utilization_pct": p.avg_utilization_pct,
                "optimization_method": p.optimization_method,
                "generated_by": p.generated_by,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in rows
        ]

        # Per-version counts help the UI render its toggle
        counts_stmt = (
            select(TransportationPlan.plan_version, func.count())
            .where(TransportationPlan.tenant_id == current_user.tenant_id)
            .group_by(TransportationPlan.plan_version)
        )
        counts_rows = (await db.execute(counts_stmt)).all()
        counts_by_version = {r[0]: r[1] for r in counts_rows}
        for v in PlanVersion:
            counts_by_version.setdefault(v.value, 0)

        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "counts_by_version": counts_by_version,
            "planning_is_constrained": PLANNING_IS_CONSTRAINED,
            "supported_versions": [v.value for v in PlanVersion],
        }

    except (OperationalError, ProgrammingError):
        if response:
            response.headers["X-TMS-Warning"] = "TMS tables not yet provisioned"
        return _NOT_PROVISIONED


# ============================================================================
# Movement Gap Analysis — §10.1 of the Tactical Planning Re-Architecture.
# Same 3-variant component shape as SCP's gap_analysis but keyed by lane /
# equipment instead of product / site.
# ============================================================================

@plans_router.get("/gap-analysis/{config_id}", response_model=Dict[str, Any])
async def transportation_plan_gap_analysis(
    config_id: int,
    top_n: int = Query(10, ge=1, le=100, description="Worst-N lanes by capacity gap"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Per-lane / per-equipment gap between unconstrained, constrained_live, and
    decision_action plan versions for a tenant's config. See
    docs/TACTICAL_PLANNING_REARCHITECTURE.md §10.1."""
    from app.services.tms_planning.movement_gap_analyzer import MovementGapAnalyzer

    # Tenant-scope config_id
    cfg_check = await db.execute(
        select(SupplyChainConfig.id).where(
            SupplyChainConfig.id == config_id,
            SupplyChainConfig.tenant_id == current_user.tenant_id,
        )
    )
    if cfg_check.scalar_one_or_none() is None:
        raise HTTPException(404, f"Config {config_id} not found for this tenant")

    analyzer = MovementGapAnalyzer(db)
    return await analyzer.analyze(config_id, top_n=top_n)


# ============================================================================
# §10.2 Synthetic capacity seeder endpoint
# ============================================================================

class CapacitySeedRequest(BaseModel):
    config_id: Optional[int] = None
    history_window_weeks: int = 12


@plans_router.post("/seed-capacity", response_model=Dict[str, Any])
async def seed_synthetic_capacity(
    body: CapacitySeedRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Run the synthetic capacity seeder for the current tenant. Idempotent."""
    from app.services.tms_planning.synthetic_capacity_seeder import SyntheticCapacitySeeder
    return await SyntheticCapacitySeeder(db).seed(
        tenant_id=current_user.tenant_id,
        config_id=body.config_id,
        history_window_weeks=body.history_window_weeks,
    )


# ============================================================================
# §10.3 TMS demo importer endpoint
# ============================================================================

class TMSDemoImportRequest(BaseModel):
    payload: Optional[Dict[str, Any]] = None
    source_url: Optional[str] = None


@plans_router.post("/tms-demo-import", response_model=Dict[str, Any])
async def tms_demo_import(
    body: TMSDemoImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Ingest an SCP `tms-demo-export` package (schema 1.0). Either pass the
    full JSON in `payload` or provide `source_url` to fetch from SCP directly.
    See docs/TACTICAL_PLANNING_REARCHITECTURE.md §10.3."""
    from app.services.tms_planning.tms_demo_importer import TMSDemoImporter

    if not body.payload and not body.source_url:
        raise HTTPException(400, "Provide either payload or source_url")
    if body.payload and body.source_url:
        raise HTTPException(400, "Provide payload OR source_url, not both")

    importer = TMSDemoImporter(db)
    try:
        if body.source_url:
            return await importer.import_from_url(body.source_url)
        return await importer.import_from_payload(body.payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
