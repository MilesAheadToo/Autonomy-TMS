"""
Transportation Management System (TMS) Entities

This module implements core transportation entities for the Autonomy TMS platform.
Follows the same patterns as sc_entities.py but adapted for freight transportation,
carrier management, shipment tracking, and logistics operations.

Entity Groups:
- Carrier & Equipment: Carrier, CarrierLane, Equipment, CarrierScorecard
- Shipment & Load: Shipment, ShipmentLeg, Load, LoadItem
- Freight Rates: FreightRate, RateCard, SpotQuote
- Appointments & Dock: Appointment, DockDoor
- Exceptions: ShipmentException, ExceptionResolution
- Documents: BillOfLading, ProofOfDelivery
- Commodity: Commodity, CommodityHierarchy

Shared entities from supply_chain_config.py:
- Site (used as Facility/Location in TMS context)
- TransportationLane (shared concept — origin-destination pair)
- Geography (shared — regional hierarchy)

All tenant-scoped entities include tenant_id with CASCADE delete.
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Double,
    ForeignKey,
    DateTime,
    Date,
    Boolean,
    JSON,
    Text,
    UniqueConstraint,
    Index,
    text,
    Enum as SAEnum,
)
from sqlalchemy.orm import relationship
from datetime import datetime, date
from typing import Optional
from enum import Enum as PyEnum
from .base import Base

# §3.47 — Core owns canonical carrier identity. Re-export so existing
# `from app.models.tms_entities import ..., Carrier, ...` callsites
# keep working; new code can import from Core directly.
from azirella_data_model.settlement.entities import Carrier  # noqa: F401


# ============================================================================
# Enums
# ============================================================================

class TransportMode(str, PyEnum):
    """Transportation mode classification"""
    FTL = "FTL"                 # Full Truckload
    LTL = "LTL"                 # Less-than-Truckload
    PARCEL = "PARCEL"           # Small package
    FCL = "FCL"                 # Full Container Load (ocean)
    LCL = "LCL"                 # Less-than-Container (ocean)
    BULK_OCEAN = "BULK_OCEAN"   # Bulk ocean freight
    AIR_STD = "AIR_STD"         # Standard air freight
    AIR_EXPRESS = "AIR_EXPRESS"  # Express air freight
    AIR_CHARTER = "AIR_CHARTER" # Charter air freight
    RAIL_CARLOAD = "RAIL_CARLOAD"
    RAIL_INTERMODAL = "RAIL_INTERMODAL"
    RAIL_UNIT = "RAIL_UNIT"     # Unit train
    INTERMODAL = "INTERMODAL"   # Generic intermodal
    DRAYAGE = "DRAYAGE"         # Short-haul port/rail to warehouse
    LAST_MILE = "LAST_MILE"     # Final delivery


class EquipmentType(str, PyEnum):
    """Equipment/trailer classification"""
    DRY_VAN = "DRY_VAN"
    REEFER = "REEFER"           # Temperature-controlled
    FLATBED = "FLATBED"
    STEP_DECK = "STEP_DECK"
    LOWBOY = "LOWBOY"
    TANKER = "TANKER"
    CONTAINER_20 = "CONTAINER_20"   # 20ft ocean container
    CONTAINER_40 = "CONTAINER_40"   # 40ft ocean container
    CONTAINER_40HC = "CONTAINER_40HC"  # 40ft high cube
    CONTAINER_45 = "CONTAINER_45"   # 45ft container
    REEFER_CONTAINER = "REEFER_CONTAINER"
    CHASSIS = "CHASSIS"
    RAILCAR_BOX = "RAILCAR_BOX"
    RAILCAR_HOPPER = "RAILCAR_HOPPER"
    RAILCAR_TANK = "RAILCAR_TANK"
    SPRINTER_VAN = "SPRINTER_VAN"
    BOX_TRUCK = "BOX_TRUCK"


class ShipmentStatus(str, PyEnum):
    """Shipment lifecycle states"""
    DRAFT = "DRAFT"
    TENDERED = "TENDERED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    DISPATCHED = "DISPATCHED"
    IN_TRANSIT = "IN_TRANSIT"
    AT_STOP = "AT_STOP"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    POD_RECEIVED = "POD_RECEIVED"
    INVOICED = "INVOICED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    EXCEPTION = "EXCEPTION"


class LoadStatus(str, PyEnum):
    """Load build lifecycle states"""
    PLANNING = "PLANNING"
    OPTIMIZING = "OPTIMIZING"
    READY = "READY"
    TENDERED = "TENDERED"
    ASSIGNED = "ASSIGNED"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    CLOSED = "CLOSED"


class ExceptionType(str, PyEnum):
    """Shipment exception classification"""
    LATE_PICKUP = "LATE_PICKUP"
    MISSED_PICKUP = "MISSED_PICKUP"
    LATE_DELIVERY = "LATE_DELIVERY"
    MISSED_DELIVERY = "MISSED_DELIVERY"
    ROUTE_DEVIATION = "ROUTE_DEVIATION"
    TEMPERATURE_EXCURSION = "TEMPERATURE_EXCURSION"
    DAMAGE = "DAMAGE"
    SHORTAGE = "SHORTAGE"
    OVERAGE = "OVERAGE"
    REFUSED = "REFUSED"
    ROLLED_CONTAINER = "ROLLED_CONTAINER"  # Ocean: container bumped from vessel
    PORT_CONGESTION = "PORT_CONGESTION"
    CUSTOMS_HOLD = "CUSTOMS_HOLD"
    WEATHER_DELAY = "WEATHER_DELAY"
    CARRIER_BREAKDOWN = "CARRIER_BREAKDOWN"
    DETENTION = "DETENTION"         # Excessive wait at facility
    DEMURRAGE = "DEMURRAGE"         # Container held at port/terminal
    ACCESSORIAL_DISPUTE = "ACCESSORIAL_DISPUTE"


class ExceptionSeverity(str, PyEnum):
    """Exception impact level"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ExceptionResolutionStatus(str, PyEnum):
    """Exception resolution lifecycle"""
    DETECTED = "DETECTED"
    INVESTIGATING = "INVESTIGATING"
    ACTION_TAKEN = "ACTION_TAKEN"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"


class CarrierType(str, PyEnum):
    """Carrier business classification"""
    ASSET = "ASSET"             # Owns trucks/equipment
    BROKER = "BROKER"           # Arranges capacity
    THREE_PL = "THREE_PL"      # Third-party logistics
    FOUR_PL = "FOUR_PL"        # Fourth-party logistics
    OCEAN_LINE = "OCEAN_LINE"
    AIRLINE = "AIRLINE"
    RAILROAD = "RAILROAD"
    COURIER = "COURIER"
    DRAYAGE_CARRIER = "DRAYAGE_CARRIER"


class TenderStatus(str, PyEnum):
    """Freight tender lifecycle"""
    CREATED = "CREATED"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    COUNTER_OFFERED = "COUNTER_OFFERED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class AppointmentType(str, PyEnum):
    """Dock appointment classification"""
    PICKUP = "PICKUP"
    DELIVERY = "DELIVERY"
    CROSS_DOCK = "CROSS_DOCK"
    DROP_TRAILER = "DROP_TRAILER"
    LIVE_LOAD = "LIVE_LOAD"
    LIVE_UNLOAD = "LIVE_UNLOAD"


class AppointmentStatus(str, PyEnum):
    """Appointment lifecycle"""
    REQUESTED = "REQUESTED"
    CONFIRMED = "CONFIRMED"
    CHECKED_IN = "CHECKED_IN"
    AT_DOCK = "AT_DOCK"
    LOADING = "LOADING"
    UNLOADING = "UNLOADING"
    COMPLETED = "COMPLETED"
    NO_SHOW = "NO_SHOW"
    CANCELLED = "CANCELLED"
    RESCHEDULED = "RESCHEDULED"


class RateType(str, PyEnum):
    """Freight rate classification"""
    CONTRACT = "CONTRACT"
    SPOT = "SPOT"
    MINI_BID = "MINI_BID"
    TARIFF = "TARIFF"
    BENCHMARK = "BENCHMARK"


# ============================================================================
# Commodity Entities
# ============================================================================

class CommodityHierarchy(Base):
    """
    Commodity/freight class hierarchy for transportation
    TMS Entity: commodity_hierarchy
    Replaces ProductHierarchy in SC context

    Hierarchy: Class → Subclass → Commodity
    Example: Dry Goods → Packaged Foods → Canned Vegetables
    """
    __tablename__ = "commodity_hierarchy"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(String(500))
    parent_id = Column(Integer, ForeignKey("commodity_hierarchy.id", ondelete="SET NULL"))
    level = Column(Integer, comment="0=Class, 1=Subclass, 2=Commodity")
    sort_order = Column(Integer)
    nmfc_class = Column(String(20), comment="National Motor Freight Classification")
    freight_class = Column(String(20), comment="Freight class (50-500)")
    is_hazmat = Column(Boolean, default=False)
    hazmat_class = Column(String(20))
    is_active = Column(Boolean, default=True)

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    config_id = Column(Integer, ForeignKey("supply_chain_configs.id", ondelete="CASCADE"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    parent = relationship("CommodityHierarchy", remote_side=[id], backref="children")
    commodities = relationship("Commodity", back_populates="hierarchy_node")

    __table_args__ = (
        Index('idx_commodity_hier_tenant', 'tenant_id', 'level'),
    )


class Commodity(Base):
    """
    Individual commodity/freight item
    TMS Entity: commodity
    Replaces Product in SC context — what is being shipped (not manufactured)
    """
    __tablename__ = "commodity"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(100), nullable=False, comment="Commodity code / SKU")
    description = Column(String(500))
    hierarchy_id = Column(Integer, ForeignKey("commodity_hierarchy.id", ondelete="SET NULL"))
    nmfc_class = Column(String(20))
    freight_class = Column(String(20), comment="Freight class (50-500)")
    base_uom = Column(String(20), comment="EA, CS, PAL, LB, KG")
    weight = Column(Double, comment="Unit weight")
    weight_uom = Column(String(20))
    volume = Column(Double, comment="Unit volume")
    volume_uom = Column(String(20))
    is_hazmat = Column(Boolean, default=False)
    hazmat_class = Column(String(20))
    hazmat_un_number = Column(String(20))
    is_stackable = Column(Boolean, default=True)
    is_temperature_sensitive = Column(Boolean, default=False)
    temp_min = Column(Double, comment="Min temp (Fahrenheit)")
    temp_max = Column(Double, comment="Max temp (Fahrenheit)")
    value_per_unit = Column(Double, comment="Declared value per unit for insurance")
    is_active = Column(Boolean, default=True)

    # Source tracking
    source = Column(String(100))
    source_event_id = Column(String(100))
    source_update_dttm = Column(DateTime)
    external_identifiers = Column(JSON, nullable=True, comment="Typed external IDs: {sap_material, gtin, upc, ...}")

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    config_id = Column(Integer, ForeignKey("supply_chain_configs.id", ondelete="CASCADE"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    hierarchy_node = relationship("CommodityHierarchy", back_populates="commodities")

    __table_args__ = (
        UniqueConstraint('tenant_id', 'code', name='uq_commodity_tenant_code'),
        Index('idx_commodity_lookup', 'tenant_id', 'freight_class'),
    )


# ============================================================================
# Carrier & Equipment Entities
# ============================================================================

# §3.47 Phase 3: TMS-side `class Carrier(Base)` deleted. The
# canonical Carrier identity now lives in Core
# (`azirella_data_model.settlement.entities.Carrier`); it's
# re-exported at the top of this file so existing
# `from app.models.tms_entities import Carrier` imports keep working.
# The TMS-specific dispatch fields (code, modes, equipment_types,
# tracking_api_*, p44_*, etc.) live on `CarrierTMSProfile` below,
# joined to `Carrier` via `carrier_id`.
#
# Relationships from CarrierLane / Equipment / FreightRate /
# CarrierScorecard / FreightTender to Carrier keep their
# `relationship("Carrier")` declarations but DROP `back_populates`
# (Core's Carrier doesn't expose `lanes` / `equipment` / etc.
# collections — those are TMS dispatch concerns and don't belong
# on a settlement-side identity row).


# ============================================================================
# CarrierTMSProfile — §3.47 Phase 1 substrate
# ============================================================================
# Per §3.47, Core (`azirella_data_model.settlement.entities.Carrier`)
# owns canonical carrier identity (id, scac, display_name, carrier_type,
# status, mc_number, dot_number, tax_id, currency, payment_terms_days,
# metadata). TMS owns dispatch / capacity / onboarding extensions in
# this sibling table, joined via `carrier_id`. Phase 1 lands the
# substrate (additive); Phase 2 retargets TMS code to read dispatch
# fields from here; Phase 3 drops the now-duplicated columns from
# `carrier` and deletes the TMS-side `Carrier` ORM above.


class CarrierTMSProfile(Base):
    """TMS dispatch / capacity / onboarding extension to Core's Carrier.

    One profile row per carrier (1:1, FK + unique). All fields here
    are TMS-specific dispatch policy that other Autonomy products
    don't consume — keeping them out of Core respects Rule 1 + the
    plane-module invariant.

    Phase 1 backfills this table from the existing `carrier` rows in
    the same Alembic migration. Phase 2's code cutover reads dispatch
    fields from here. Phase 3 drops the duplicated columns from
    `carrier`.
    """

    __tablename__ = "carrier_tms_profile"

    id = Column(Integer, primary_key=True, autoincrement=True)
    carrier_id = Column(
        Integer,
        ForeignKey("carrier.id", ondelete="CASCADE"),
        nullable=False, unique=True,
        comment="Core carrier identity FK — exactly one profile per carrier.",
    )
    tenant_id = Column(
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    # TMS-internal carrier code (distinct from SCAC; tenant-scoped uniqueness).
    code = Column(
        String(100),
        comment="TMS-internal carrier code; tenant-scoped unique.",
    )

    # FMCSA fleet-safety rating (TMS-side dispatch decision input).
    usdot_safety_rating = Column(
        String(20),
        comment="Satisfactory / Conditional / Unsatisfactory",
    )

    # Capabilities — what modes / equipment / regions the carrier serves.
    modes = Column(JSON, comment='e.g., ["FTL", "LTL", "DRAYAGE"]')
    equipment_types = Column(JSON, comment='e.g., ["DRY_VAN", "REEFER"]')
    service_regions = Column(JSON, comment='e.g., ["US_DOMESTIC", "TRANSPACIFIC"]')
    is_hazmat_certified = Column(Boolean, default=False)
    is_bonded = Column(Boolean, default=False)
    insurance_limit = Column(Double, comment="Cargo insurance limit (USD)")

    # Contact / dispatch.
    primary_contact_name = Column(String(200))
    primary_contact_email = Column(String(200))
    primary_contact_phone = Column(String(50))
    dispatch_email = Column(String(200))
    dispatch_phone = Column(String(50))
    tracking_api_type = Column(
        String(50),
        comment="EDI, API, PORTAL, P44, FOURKITES",
    )
    tracking_api_config = Column(
        JSON, comment="API credentials and endpoint config",
    )

    # Onboarding lifecycle (TMS-side; orthogonal to Core's settlement
    # `Carrier.status` which tracks settlement-payable state).
    onboarding_status = Column(
        String(20), default="PENDING",
        comment="PENDING, IN_PROGRESS, ACTIVE, SUSPENDED",
    )
    onboarding_date = Column(Date)
    last_shipment_date = Column(Date)

    # Source tracking + external identifiers.
    source = Column(String(100))
    external_identifiers = Column(
        JSON,
        comment="Typed external IDs: {sap_vendor, p44_id, ...}",
    )

    # project44 integration (TMS-specific).
    p44_carrier_id = Column(String(100))
    p44_identifier_type = Column(String(20))
    p44_account_group_code = Column(String(100))
    p44_account_code = Column(String(100))

    # Tenant-config link (TMS-specific).
    config_id = Column(
        Integer,
        ForeignKey("supply_chain_configs.id", ondelete="CASCADE"),
    )

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "code",
            name="uq_carrier_tms_profile_tenant_code",
        ),
        Index(
            "idx_carrier_tms_profile_tenant_onboarding",
            "tenant_id", "onboarding_status",
        ),
    )


class CarrierLane(Base):
    """
    Carrier lane coverage — which origin-destination pairs a carrier serves
    TMS Entity: carrier_lane

    Links Carrier to TransportationLane with mode and capacity details.
    """
    __tablename__ = "carrier_lane"

    id = Column(Integer, primary_key=True, autoincrement=True)
    carrier_id = Column(Integer, ForeignKey("carrier.id", ondelete="CASCADE"), nullable=False)
    lane_id = Column(Integer, ForeignKey("transportation_lane.id", ondelete="CASCADE"), nullable=False)
    mode = Column(SAEnum(TransportMode, name="transport_mode_enum"), nullable=False)
    equipment_type = Column(SAEnum(EquipmentType, name="equipment_type_enum"))

    # Capacity
    weekly_capacity = Column(Integer, comment="Max loads per week on this lane")
    avg_transit_days = Column(Double, comment="Average transit time in days")
    transit_time_dist = Column(JSON, comment='Stochastic: {"type": "lognormal", "mean": 3.2, "stddev": 0.5}')

    # Preference
    priority = Column(Integer, default=1, comment="Lower = preferred. Used in carrier waterfall")
    is_primary = Column(Boolean, default=False, comment="Primary carrier for this lane")
    is_active = Column(Boolean, default=True)
    eff_start_date = Column(Date)
    eff_end_date = Column(Date)

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    carrier = relationship("Carrier")
    lane = relationship("TransportationLane")

    __table_args__ = (
        Index('idx_carrier_lane_lookup', 'tenant_id', 'lane_id', 'mode'),
        Index('idx_carrier_lane_carrier', 'carrier_id', 'is_active'),
    )


class Equipment(Base):
    """
    Physical equipment: trailers, containers, railcars
    TMS Entity: equipment

    Tracks equipment availability and location for repositioning decisions.
    """
    __tablename__ = "equipment"

    id = Column(Integer, primary_key=True, autoincrement=True)
    equipment_id = Column(String(100), nullable=False, comment="Trailer/container number")
    equipment_type = Column(SAEnum(EquipmentType, name="equipment_type_enum"), nullable=False)
    carrier_id = Column(Integer, ForeignKey("carrier.id", ondelete="SET NULL"))

    # Specs
    length_ft = Column(Double)
    width_ft = Column(Double)
    height_ft = Column(Double)
    max_weight_lbs = Column(Double)
    max_volume_cuft = Column(Double)
    tare_weight_lbs = Column(Double)
    is_gps_tracked = Column(Boolean, default=False)

    # Temperature control
    is_temperature_controlled = Column(Boolean, default=False)
    temp_min = Column(Double)
    temp_max = Column(Double)

    # Current state
    status = Column(String(20), default="AVAILABLE", comment="AVAILABLE, IN_USE, MAINTENANCE, RETIRED")
    current_site_id = Column(Integer, ForeignKey("site.id"))
    last_known_lat = Column(Double)
    last_known_lon = Column(Double)
    last_position_update = Column(DateTime)

    is_active = Column(Boolean, default=True)
    source = Column(String(100))

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    config_id = Column(Integer, ForeignKey("supply_chain_configs.id", ondelete="CASCADE"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    carrier = relationship("Carrier")
    current_site = relationship("Site", foreign_keys=[current_site_id])

    __table_args__ = (
        UniqueConstraint('tenant_id', 'equipment_id', name='uq_equipment_tenant_id'),
        Index('idx_equipment_status', 'tenant_id', 'status', 'equipment_type'),
        Index('idx_equipment_site', 'current_site_id'),
    )


class CarrierScorecard(Base):
    """
    Carrier performance scorecard — rolling metrics
    TMS Entity: carrier_scorecard

    Aggregated periodically by the agent system. Feeds into
    FreightProcurementTRM carrier selection decisions.
    """
    __tablename__ = "carrier_scorecard"

    id = Column(Integer, primary_key=True, autoincrement=True)
    carrier_id = Column(Integer, ForeignKey("carrier.id", ondelete="CASCADE"), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)

    # Volume
    total_shipments = Column(Integer, default=0)
    total_loads = Column(Integer, default=0)

    # On-time performance
    on_time_pickup_pct = Column(Double, comment="% pickups within window")
    on_time_delivery_pct = Column(Double, comment="% deliveries within window")
    avg_transit_variance_hrs = Column(Double, comment="Avg hours early(-)/late(+) vs committed")

    # Cost
    avg_cost_per_mile = Column(Double)
    avg_cost_per_shipment = Column(Double)
    cost_vs_benchmark_pct = Column(Double, comment="% above/below market benchmark")

    # Quality
    damage_rate_pct = Column(Double)
    claims_count = Column(Integer, default=0)
    claims_value = Column(Double, default=0)
    exception_rate_pct = Column(Double)

    # Responsiveness
    tender_acceptance_rate_pct = Column(Double, comment="% tenders accepted")
    avg_tender_response_hrs = Column(Double, comment="Avg hours to respond to tender")
    tracking_compliance_pct = Column(Double, comment="% shipments with tracking updates")

    # Composite score (0-100, computed by agent)
    composite_score = Column(Double, comment="Weighted composite 0-100")
    score_components = Column(JSON, comment='{"on_time": 30, "cost": 25, "quality": 20, "responsiveness": 25}')

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    carrier = relationship("Carrier")

    __table_args__ = (
        Index('idx_scorecard_carrier_period', 'carrier_id', 'period_start'),
        Index('idx_scorecard_tenant', 'tenant_id', 'period_start'),
    )


# ============================================================================
# Shipment & Load Entities
# ============================================================================

class TMSShipment(Base):
    """
    Unit of freight movement from origin to destination
    TMS Entity: tms_shipment (distinct from sc_entities.Shipment)
    Maps to PurchaseOrder in SC context — the demand signal for transportation

    A shipment represents a customer's request to move freight. It may be
    consolidated into a Load with other shipments.

    NOTE: We use tms_shipment table name to avoid conflict with the
    sc_entities.Shipment class which uses String IDs and a different schema.
    The two represent different things: TMS Shipment is freight-domain;
    SC Shipment is material-visibility-domain.
    """
    __tablename__ = "tms_shipment"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shipment_number = Column(String(100), nullable=False, comment="Business reference number")
    status = Column(SAEnum(ShipmentStatus, name="shipment_status_enum"), nullable=False, default=ShipmentStatus.DRAFT)

    # Origin / Destination
    origin_site_id = Column(Integer, ForeignKey("site.id"), nullable=False)
    destination_site_id = Column(Integer, ForeignKey("site.id"), nullable=False)
    lane_id = Column(Integer, ForeignKey("transportation_lane.id"))

    # Commodity & Quantity
    commodity_id = Column(Integer, ForeignKey("commodity.id"))
    quantity = Column(Double, comment="Total units")
    weight = Column(Double, comment="Total weight")
    weight_uom = Column(String(20), default="LBS")
    volume = Column(Double, comment="Total volume")
    volume_uom = Column(String(20), default="CUFT")
    pallet_count = Column(Integer)
    piece_count = Column(Integer)
    declared_value = Column(Double)

    # Mode & Equipment
    mode = Column(SAEnum(TransportMode, name="transport_mode_enum"))
    required_equipment = Column(SAEnum(EquipmentType, name="equipment_type_enum"))
    is_hazmat = Column(Boolean, default=False)
    is_temperature_sensitive = Column(Boolean, default=False)
    temp_min = Column(Double)
    temp_max = Column(Double)

    # Dates
    requested_pickup_date = Column(DateTime, nullable=False)
    requested_delivery_date = Column(DateTime, nullable=False)
    earliest_pickup = Column(DateTime)
    latest_pickup = Column(DateTime)
    earliest_delivery = Column(DateTime)
    latest_delivery = Column(DateTime)
    actual_pickup_date = Column(DateTime)
    actual_delivery_date = Column(DateTime)

    # Assignment
    load_id = Column(Integer, ForeignKey("load.id", ondelete="SET NULL"))
    carrier_id = Column(Integer, ForeignKey("carrier.id"))
    equipment_id = Column(Integer, ForeignKey("equipment.id"))

    # Cost
    estimated_cost = Column(Double)
    actual_cost = Column(Double)
    freight_charge = Column(Double)
    accessorial_charges = Column(JSON, comment='[{"type": "FUEL_SURCHARGE", "amount": 125.50}, ...]')
    total_charge = Column(Double)

    # Priority & Service
    priority = Column(Integer, default=3, comment="1=critical, 5=low. Maps to AATP priority tiers")
    service_level = Column(String(50), comment="STANDARD, EXPEDITED, NEXT_DAY, WHITE_GLOVE")
    special_instructions = Column(Text)
    reference_numbers = Column(JSON, comment='{"po": "PO-12345", "so": "SO-67890", "bol": "BOL-111"}')

    # Tracking
    current_lat = Column(Double)
    current_lon = Column(Double)
    last_tracking_update = Column(DateTime)
    estimated_arrival = Column(DateTime, comment="Current ETA from tracking/conformal")
    eta_confidence = Column(JSON, comment='{"p44": {"p10": "...", "p50": "...", "p90": "..."}, "autonomy": {"p10": "...", "p50": "...", "p90": "..."}, "composite": {"p10": "...", "p50": "...", "p90": "...", "method": "ensemble"}}')

    # p44 integration
    p44_shipment_id = Column(String(200), comment="project44 shipment identifier")
    p44_tracking_url = Column(String(500))
    p44_derived_status = Column(String(50), comment="p44 derivedStatus from nested status object (e.g., EARLY, ON_TIME, LATE)")
    p44_health_score = Column(Double, comment="p44 health score (0.0-1.0) — leading indicator for exception prediction")
    p44_master_shipment_id = Column(String(200), comment="p44 masterShipmentId — maps to TMS Load for multi-stop load-level visibility")

    # Source
    source = Column(String(100), comment="TMS, ERP, EDI, MANUAL")
    source_event_id = Column(String(100))
    external_identifiers = Column(JSON, nullable=True)

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    config_id = Column(Integer, ForeignKey("supply_chain_configs.id", ondelete="CASCADE"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    origin = relationship("Site", foreign_keys=[origin_site_id])
    destination = relationship("Site", foreign_keys=[destination_site_id])
    lane = relationship("TransportationLane")
    commodity = relationship("Commodity")
    load = relationship("Load", back_populates="shipments")
    carrier = relationship("Carrier")
    equipment_assigned = relationship("Equipment", foreign_keys=[equipment_id])
    legs = relationship("ShipmentLeg", back_populates="shipment", cascade="all, delete-orphan",
                        order_by="ShipmentLeg.leg_sequence")
    exceptions = relationship("ShipmentException", back_populates="shipment", cascade="all, delete-orphan")
    tenders = relationship("FreightTender", back_populates="shipment", cascade="all, delete-orphan")
    bol = relationship("BillOfLading", back_populates="shipment", uselist=False)
    pod = relationship("ProofOfDelivery", back_populates="shipment", uselist=False)
    appointments = relationship("Appointment", back_populates="shipment")

    __table_args__ = (
        UniqueConstraint('tenant_id', 'shipment_number', name='uq_shipment_tenant_number'),
        Index('idx_tms_shipment_status', 'tenant_id', 'status'),
        Index('idx_shipment_dates', 'tenant_id', 'requested_pickup_date', 'requested_delivery_date'),
        Index('idx_shipment_carrier', 'carrier_id', 'status'),
        Index('idx_shipment_lane', 'lane_id', 'status'),
        Index('idx_shipment_origin_dest', 'origin_site_id', 'destination_site_id'),
    )


class ShipmentLeg(Base):
    """
    Individual leg of a multi-stop or intermodal shipment
    TMS Entity: shipment_leg

    A shipment may have multiple legs (e.g., drayage → ocean → drayage).
    Each leg has its own carrier, mode, and tracking.
    """
    __tablename__ = "shipment_leg"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shipment_id = Column(Integer, ForeignKey("tms_shipment.id", ondelete="CASCADE"), nullable=False)
    leg_sequence = Column(Integer, nullable=False, comment="1-based ordering")

    from_site_id = Column(Integer, ForeignKey("site.id"), nullable=False)
    to_site_id = Column(Integer, ForeignKey("site.id"), nullable=False)
    mode = Column(SAEnum(TransportMode, name="transport_mode_enum"), nullable=False)
    carrier_id = Column(Integer, ForeignKey("carrier.id"))
    equipment_id = Column(Integer, ForeignKey("equipment.id"))

    # Dates
    planned_departure = Column(DateTime)
    planned_arrival = Column(DateTime)
    actual_departure = Column(DateTime)
    actual_arrival = Column(DateTime)

    # Status
    status = Column(String(20), default="PLANNED", comment="PLANNED, IN_TRANSIT, COMPLETED, EXCEPTION")

    # Tracking
    current_lat = Column(Double)
    current_lon = Column(Double)
    last_tracking_update = Column(DateTime)

    # Ocean-specific
    vessel_name = Column(String(200))
    voyage_number = Column(String(100))
    container_number = Column(String(50))

    # Cost
    leg_cost = Column(Double)

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    shipment = relationship("app.models.tms_entities.TMSShipment", back_populates="legs")
    from_site = relationship("Site", foreign_keys=[from_site_id])
    to_site = relationship("Site", foreign_keys=[to_site_id])
    carrier = relationship("Carrier")

    __table_args__ = (
        Index('idx_leg_shipment', 'shipment_id', 'leg_sequence'),
    )


class Load(Base):
    """
    Physical grouping of shipments on equipment
    TMS Entity: load
    Maps to ManufacturingOrder in SC context — the consolidation/build step

    A load represents a single truck, container, or railcar movement.
    Multiple shipments may be consolidated into one load.
    """
    __tablename__ = "load"

    id = Column(Integer, primary_key=True, autoincrement=True)
    load_number = Column(String(100), nullable=False)
    status = Column(SAEnum(LoadStatus, name="load_status_enum"), nullable=False, default=LoadStatus.PLANNING)

    # Route
    origin_site_id = Column(Integer, ForeignKey("site.id"), nullable=False)
    destination_site_id = Column(Integer, ForeignKey("site.id"), nullable=False)
    mode = Column(SAEnum(TransportMode, name="transport_mode_enum"), nullable=False)

    # Equipment & Carrier
    equipment_type = Column(SAEnum(EquipmentType, name="equipment_type_enum"))
    equipment_id = Column(Integer, ForeignKey("equipment.id"))
    carrier_id = Column(Integer, ForeignKey("carrier.id"))

    # Multi-stop (ordered list of stops)
    stops = Column(JSON, comment='[{"site_id": 1, "type": "PICKUP", "sequence": 1}, ...]')

    # Utilization
    total_weight = Column(Double, default=0)
    total_volume = Column(Double, default=0)
    total_pallets = Column(Integer, default=0)
    weight_utilization_pct = Column(Double, comment="% of max weight used")
    volume_utilization_pct = Column(Double, comment="% of max volume used")
    linear_ft_used = Column(Double, comment="Linear feet of trailer used")

    # Dates
    planned_departure = Column(DateTime)
    planned_arrival = Column(DateTime)
    actual_departure = Column(DateTime)
    actual_arrival = Column(DateTime)

    # Cost
    total_cost = Column(Double)
    cost_per_mile = Column(Double)
    total_miles = Column(Double)
    empty_miles = Column(Double, default=0, comment="Deadhead miles")

    # Optimization metadata (set by LoadBuildTRM)
    optimization_score = Column(Double, comment="Agent optimization quality 0-1")
    optimization_metadata = Column(JSON, comment='{"method": "TRM", "alternatives_considered": 5}')

    source = Column(String(100))

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    config_id = Column(Integer, ForeignKey("supply_chain_configs.id", ondelete="CASCADE"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    origin = relationship("Site", foreign_keys=[origin_site_id])
    destination = relationship("Site", foreign_keys=[destination_site_id])
    carrier = relationship("Carrier")
    equipment_assigned = relationship("Equipment", foreign_keys=[equipment_id])
    shipments = relationship("app.models.tms_entities.TMSShipment", back_populates="load")
    items = relationship("LoadItem", back_populates="load", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('tenant_id', 'load_number', name='uq_load_tenant_number'),
        Index('idx_load_status', 'tenant_id', 'status'),
        Index('idx_load_dates', 'tenant_id', 'planned_departure'),
        Index('idx_load_carrier', 'carrier_id', 'status'),
    )


class LoadItem(Base):
    """
    Line item within a load — maps shipment quantity to load space
    TMS Entity: load_item
    Maps to BOM in SC context — how freight fills equipment
    """
    __tablename__ = "load_item"

    id = Column(Integer, primary_key=True, autoincrement=True)
    load_id = Column(Integer, ForeignKey("load.id", ondelete="CASCADE"), nullable=False)
    shipment_id = Column(Integer, ForeignKey("tms_shipment.id"), nullable=False)
    commodity_id = Column(Integer, ForeignKey("commodity.id"))

    quantity = Column(Double)
    weight = Column(Double)
    volume = Column(Double)
    pallet_count = Column(Integer)
    load_sequence = Column(Integer, comment="Loading order (first on = last off)")
    position = Column(String(50), comment="NOSE, CENTER, TAIL, UPPER, LOWER")

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    load = relationship("Load", back_populates="items")
    shipment = relationship("app.models.tms_entities.TMSShipment")
    commodity = relationship("Commodity")

    __table_args__ = (
        Index('idx_load_item_load', 'load_id'),
    )


# ============================================================================
# Freight Rate Entities
# ============================================================================

class FreightRate(Base):
    """
    Contracted or spot freight rate for a lane/carrier/mode combination
    TMS Entity: freight_rate

    Used by FreightProcurementTRM for carrier selection and cost estimation.
    """
    __tablename__ = "freight_rate"

    id = Column(Integer, primary_key=True, autoincrement=True)
    carrier_id = Column(Integer, ForeignKey("carrier.id", ondelete="CASCADE"), nullable=False)
    lane_id = Column(Integer, ForeignKey("transportation_lane.id"), nullable=False)
    mode = Column(SAEnum(TransportMode, name="transport_mode_enum"), nullable=False)
    equipment_type = Column(SAEnum(EquipmentType, name="equipment_type_enum"))

    rate_type = Column(SAEnum(RateType, name="rate_type_enum"), nullable=False)
    rate_per_mile = Column(Double)
    rate_flat = Column(Double, comment="Flat rate for the lane")
    rate_per_cwt = Column(Double, comment="Rate per hundredweight (LTL)")
    rate_per_unit = Column(Double, comment="Rate per pallet/container")
    min_charge = Column(Double)
    fuel_surcharge_pct = Column(Double, comment="Fuel surcharge as % of line haul")
    fuel_surcharge_method = Column(String(50), comment="DOE_INDEX, FLAT, INCLUDED")

    # Accessorials
    accessorial_schedule = Column(JSON, comment='{"DETENTION": 75.0, "LIFTGATE": 150.0, "INSIDE_DELIVERY": 200.0}')

    # Validity
    eff_start_date = Column(Date, nullable=False)
    eff_end_date = Column(Date, nullable=False)
    contract_number = Column(String(100))
    is_active = Column(Boolean, default=True)

    # Volume commitment
    min_volume_per_week = Column(Integer, comment="Committed loads/week for this rate")
    max_volume_per_week = Column(Integer)

    # Market context (for benchmarking)
    market_rate_at_contract = Column(Double, comment="Market rate when contract signed")

    source = Column(String(100))

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    config_id = Column(Integer, ForeignKey("supply_chain_configs.id", ondelete="CASCADE"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    carrier = relationship("Carrier")
    lane = relationship("TransportationLane")

    __table_args__ = (
        Index('idx_rate_lookup', 'tenant_id', 'lane_id', 'mode', 'carrier_id'),
        Index('idx_rate_validity', 'eff_start_date', 'eff_end_date', 'is_active'),
    )


class FreightTender(Base):
    """
    Tender offer sent to carrier for a shipment/load
    TMS Entity: freight_tender

    Tracks the carrier selection waterfall: tender → accept/decline → assign.
    """
    __tablename__ = "freight_tender"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shipment_id = Column(Integer, ForeignKey("tms_shipment.id", ondelete="CASCADE"))
    load_id = Column(Integer, ForeignKey("load.id", ondelete="CASCADE"))
    carrier_id = Column(Integer, ForeignKey("carrier.id"), nullable=False)
    rate_id = Column(Integer, ForeignKey("freight_rate.id"))

    tender_sequence = Column(Integer, nullable=False, comment="Position in carrier waterfall")
    status = Column(SAEnum(TenderStatus, name="tender_status_enum"), nullable=False, default=TenderStatus.CREATED)

    offered_rate = Column(Double, nullable=False)
    counter_rate = Column(Double, comment="Carrier's counter-offer rate")
    final_rate = Column(Double, comment="Agreed rate after negotiation")

    tendered_at = Column(DateTime)
    response_deadline = Column(DateTime)
    responded_at = Column(DateTime)
    decline_reason = Column(String(500))

    # Agent decision metadata
    agent_decision_id = Column(String(100), comment="FK to powell_decisions for traceability")
    selection_rationale = Column(JSON, comment='{"score": 0.87, "factors": {"cost": 0.3, "otd": 0.4, ...}}')

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    shipment = relationship("app.models.tms_entities.TMSShipment", back_populates="tenders")
    load = relationship("Load")
    carrier = relationship("Carrier")
    rate = relationship("FreightRate")

    __table_args__ = (
        Index('idx_tender_shipment', 'shipment_id', 'tender_sequence'),
        Index('idx_tender_carrier', 'carrier_id', 'status'),
    )


# ============================================================================
# Appointment & Dock Entities
# ============================================================================

class DockDoor(Base):
    """
    Physical dock door at a facility
    TMS Entity: dock_door

    Used by DockSchedulingTRM for appointment optimization.
    """
    __tablename__ = "dock_door"

    id = Column(Integer, primary_key=True, autoincrement=True)
    site_id = Column(Integer, ForeignKey("site.id", ondelete="CASCADE"), nullable=False)
    door_number = Column(String(20), nullable=False)
    door_type = Column(String(20), comment="INBOUND, OUTBOUND, BOTH")
    equipment_compatible = Column(JSON, comment='["DRY_VAN", "REEFER", "FLATBED"]')
    has_leveler = Column(Boolean, default=True)
    has_restraint = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    site = relationship("Site")
    appointments = relationship("Appointment", back_populates="dock_door")

    __table_args__ = (
        UniqueConstraint('site_id', 'door_number', name='uq_dock_door_site_number'),
    )


class Appointment(Base):
    """
    Dock appointment / delivery window
    TMS Entity: appointment

    Managed by DockSchedulingTRM. Links shipments to dock doors and time slots.
    """
    __tablename__ = "appointment"

    id = Column(Integer, primary_key=True, autoincrement=True)
    site_id = Column(Integer, ForeignKey("site.id", ondelete="CASCADE"), nullable=False)
    dock_door_id = Column(Integer, ForeignKey("dock_door.id"))
    shipment_id = Column(Integer, ForeignKey("tms_shipment.id"))
    load_id = Column(Integer, ForeignKey("load.id"))

    appointment_type = Column(SAEnum(AppointmentType, name="appointment_type_enum"), nullable=False)
    status = Column(SAEnum(AppointmentStatus, name="appointment_status_enum"), nullable=False, default=AppointmentStatus.REQUESTED)

    # Time window
    scheduled_start = Column(DateTime, nullable=False)
    scheduled_end = Column(DateTime, nullable=False)
    actual_arrival = Column(DateTime)
    actual_start = Column(DateTime)
    actual_end = Column(DateTime)
    actual_departure = Column(DateTime)

    # Metrics
    dwell_time_minutes = Column(Integer, comment="Total time at facility")
    dock_time_minutes = Column(Integer, comment="Time at dock door")
    wait_time_minutes = Column(Integer, comment="Time waiting for dock assignment")

    carrier_id = Column(Integer, ForeignKey("carrier.id"))
    driver_name = Column(String(200))
    driver_phone = Column(String(50))
    trailer_number = Column(String(100))

    special_instructions = Column(Text)
    reference_numbers = Column(JSON)

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    site = relationship("Site")
    dock_door = relationship("DockDoor", back_populates="appointments")
    shipment = relationship("app.models.tms_entities.TMSShipment", back_populates="appointments")
    load = relationship("Load")
    carrier = relationship("Carrier")

    __table_args__ = (
        Index('idx_appointment_site_time', 'site_id', 'scheduled_start', 'scheduled_end'),
        Index('idx_appointment_status', 'tenant_id', 'status', 'appointment_type'),
        Index('idx_appointment_dock', 'dock_door_id', 'scheduled_start'),
    )


# ============================================================================
# Exception Entities
# ============================================================================

class ShipmentException(Base):
    """
    Shipment exception / disruption event
    TMS Entity: shipment_exception

    Detected by ShipmentTrackingTRM or ExceptionManagementTRM.
    Fed from project44 visibility events, carrier EDI, or manual entry.
    """
    __tablename__ = "shipment_exception"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shipment_id = Column(Integer, ForeignKey("tms_shipment.id", ondelete="CASCADE"), nullable=False)
    leg_id = Column(Integer, ForeignKey("shipment_leg.id"))

    exception_type = Column(SAEnum(ExceptionType, name="exception_type_enum"), nullable=False)
    severity = Column(SAEnum(ExceptionSeverity, name="exception_severity_enum"), nullable=False, default=ExceptionSeverity.MEDIUM)
    resolution_status = Column(SAEnum(ExceptionResolutionStatus, name="exception_resolution_status_enum"),
                               nullable=False, default=ExceptionResolutionStatus.DETECTED)

    description = Column(Text, nullable=False)
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime)

    # Impact assessment (set by agent)
    estimated_delay_hrs = Column(Double)
    estimated_cost_impact = Column(Double)
    revenue_at_risk = Column(Double)
    impact_assessment = Column(JSON, comment='{"service": 0.8, "cost": 0.3, "risk": 0.6}')

    # Detection source
    detection_source = Column(String(50), comment="P44, CARRIER_EDI, AGENT, MANUAL, WEATHER_API")
    detection_event_id = Column(String(200), comment="External event ID from detection source")

    # Location at time of exception
    exception_lat = Column(Double)
    exception_lon = Column(Double)
    exception_location_desc = Column(String(500))

    # Agent decision link
    agent_decision_id = Column(String(100), comment="FK to powell_decisions for resolution traceability")

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    shipment = relationship("app.models.tms_entities.TMSShipment", back_populates="exceptions")
    leg = relationship("ShipmentLeg")
    resolutions = relationship("ExceptionResolution", back_populates="exception", cascade="all, delete-orphan")

    __table_args__ = (
        Index('idx_exception_shipment', 'shipment_id'),
        Index('idx_exception_status', 'tenant_id', 'resolution_status', 'severity'),
        Index('idx_tms_exception_type', 'tenant_id', 'exception_type', 'detected_at'),
    )


class ExceptionResolution(Base):
    """
    Resolution action taken for a shipment exception
    TMS Entity: exception_resolution

    Tracks the chain of actions: detection → investigation → resolution.
    Follows AIIO model: agent acts → user inspects/overrides.
    """
    __tablename__ = "exception_resolution"

    id = Column(Integer, primary_key=True, autoincrement=True)
    exception_id = Column(Integer, ForeignKey("shipment_exception.id", ondelete="CASCADE"), nullable=False)

    action_type = Column(String(50), nullable=False,
                         comment="REROUTE, REBOOK, EXPEDITE, CARRIER_CHANGE, RESCHEDULE, CLAIM, ACCEPT_DELAY")
    action_description = Column(Text)
    action_by = Column(String(20), nullable=False, comment="AGENT or USER")
    aiio_status = Column(String(20), nullable=False, comment="ACTIONED, INFORMED, INSPECTED, OVERRIDDEN")

    # If overridden
    override_reason = Column(Text)
    original_action = Column(JSON, comment="Agent's original action before user override")

    # Outcome
    outcome = Column(String(50), comment="RESOLVED, PARTIALLY_RESOLVED, INEFFECTIVE, PENDING")
    cost_of_resolution = Column(Double)
    delay_mitigated_hrs = Column(Double)

    resolved_by_user_id = Column(Integer, ForeignKey("users.id"))
    agent_decision_id = Column(String(100))

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    exception = relationship("ShipmentException", back_populates="resolutions")

    __table_args__ = (
        Index('idx_resolution_exception', 'exception_id'),
    )


# ============================================================================
# Document Entities
# ============================================================================

class BillOfLading(Base):
    """
    Bill of Lading — legal document for freight shipment
    TMS Entity: bill_of_lading
    """
    __tablename__ = "bill_of_lading"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shipment_id = Column(Integer, ForeignKey("tms_shipment.id", ondelete="CASCADE"), nullable=False)
    bol_number = Column(String(100), nullable=False)

    shipper_name = Column(String(200))
    shipper_address = Column(Text)
    consignee_name = Column(String(200))
    consignee_address = Column(Text)
    carrier_name = Column(String(200))

    # Freight details
    line_items = Column(JSON, comment='[{"description": "Canned goods", "weight": 5000, "class": "70", "nmfc": "12345"}]')
    total_weight = Column(Double)
    total_pieces = Column(Integer)
    freight_class = Column(String(20))

    # Terms
    freight_terms = Column(String(20), comment="PREPAID, COLLECT, THIRD_PARTY")
    special_instructions = Column(Text)

    issued_date = Column(Date)
    document_url = Column(String(500), comment="Link to scanned/generated BOL document")

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    shipment = relationship("app.models.tms_entities.TMSShipment", back_populates="bol")

    __table_args__ = (
        UniqueConstraint('tenant_id', 'bol_number', name='uq_bol_tenant_number'),
    )


class ProofOfDelivery(Base):
    """
    Proof of Delivery — delivery confirmation record
    TMS Entity: proof_of_delivery
    """
    __tablename__ = "proof_of_delivery"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shipment_id = Column(Integer, ForeignKey("tms_shipment.id", ondelete="CASCADE"), nullable=False)

    signed_by = Column(String(200))
    signed_at = Column(DateTime)
    delivery_date = Column(DateTime, nullable=False)
    delivery_status = Column(String(20), comment="FULL, PARTIAL, REFUSED, DAMAGED")

    # Discrepancies
    pieces_received = Column(Integer)
    pieces_expected = Column(Integer)
    damage_noted = Column(Boolean, default=False)
    damage_description = Column(Text)
    shortage_noted = Column(Boolean, default=False)
    shortage_description = Column(Text)

    # Photos/documents
    photo_urls = Column(JSON, comment='["https://...pod_photo_1.jpg", ...]')
    document_url = Column(String(500))
    signature_url = Column(String(500))

    notes = Column(Text)

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    shipment = relationship("app.models.tms_entities.TMSShipment", back_populates="pod")

    __table_args__ = (
        Index('idx_pod_shipment', 'shipment_id'),
    )


# ============================================================================
# Tracking Event Entities (p44-aligned)
# ============================================================================

class TrackingEventType(str, PyEnum):
    """
    Tracking event classification
    Aligned with project44 TrackedShipmentEvent.eventType values
    """
    # Movement events
    PICKED_UP = "PICKED_UP"
    DEPARTED = "DEPARTED"
    IN_TRANSIT = "IN_TRANSIT"
    ARRIVAL_AT_STOP = "ARRIVAL_AT_STOP"
    DEPARTED_STOP = "DEPARTED_STOP"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    # Terminal events (LTL)
    ARRIVED_AT_TERMINAL = "ARRIVED_AT_TERMINAL"
    DEPARTED_TERMINAL = "DEPARTED_TERMINAL"
    # Ocean events
    VESSEL_DEPARTED = "VESSEL_DEPARTED"
    VESSEL_ARRIVED = "VESSEL_ARRIVED"
    LOADED_ON_VESSEL = "LOADED_ON_VESSEL"
    DISCHARGED = "DISCHARGED"
    GATE_IN = "GATE_IN"
    GATE_OUT = "GATE_OUT"
    TRANSSHIPMENT = "TRANSSHIPMENT"
    CUSTOMS_CLEARED = "CUSTOMS_CLEARED"
    CUSTOMS_HOLD = "CUSTOMS_HOLD"
    # Intermodal
    RAIL_DEPARTED = "RAIL_DEPARTED"
    RAIL_ARRIVED = "RAIL_ARRIVED"
    # Administrative
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    CANCELLED = "CANCELLED"
    # Appointment
    APPOINTMENT_SET = "APPOINTMENT_SET"
    UPDATED_DELIVERY_APPT = "UPDATED_DELIVERY_APPT"
    # Exceptions
    DELAYED = "DELAYED"
    EXCEPTION = "EXCEPTION"
    RETURNED = "RETURNED"
    # ETA
    ETA_UPDATED = "ETA_UPDATED"


class TrackingEvent(Base):
    """
    Individual tracking event for a shipment
    TMS Entity: tracking_event

    Aligned with project44 TrackedShipmentEvent schema:
    - eventId (UUID), eventType, timestamp, location, statusCode

    Populated from p44 webhooks, carrier EDI (214), or carrier API updates.
    Fed to ShipmentTrackingTRM for ETA prediction and exception detection.
    """
    __tablename__ = "tracking_event"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shipment_id = Column(Integer, ForeignKey("tms_shipment.id", ondelete="CASCADE"), nullable=False)
    leg_id = Column(Integer, ForeignKey("shipment_leg.id"))

    # p44-aligned fields
    event_type = Column(SAEnum(TrackingEventType, name="tracking_event_type_enum"), nullable=False)
    event_timestamp = Column(DateTime, nullable=False, comment="When the event occurred")
    received_timestamp = Column(DateTime, default=datetime.utcnow, comment="When we received the event")

    # p44 identifiers for deduplication
    p44_event_id = Column(String(200), comment="project44 eventId (UUID)")
    p44_shipment_id = Column(String(200), comment="project44 masterShipmentId")
    p44_shipment_leg_id = Column(String(200), comment="project44 shipmentLegId")

    # Location (aligned with p44 Address schema)
    location_name = Column(String(200))
    address_line_1 = Column(String(255))
    city = Column(String(100))
    state = Column(String(100))
    postal_code = Column(String(50))
    country = Column(String(10))
    latitude = Column(Double)
    longitude = Column(Double)

    # Status
    status_code = Column(String(50), comment="Carrier-specific status code")
    status_description = Column(String(500))

    # ETA (p44: events.estimateDateTime for ARRIVAL_AT_STOP events)
    estimated_arrival = Column(DateTime, comment="ETA at next stop (from p44 or conformal)")
    estimated_departure = Column(DateTime)
    eta_confidence = Column(JSON, comment='{"p10": "...", "p50": "...", "p90": "..."} from conformal prediction')

    # Stop reference (p44: stopId from shipment legs)
    stop_id = Column(Integer, ForeignKey("site.id"))
    stop_sequence = Column(Integer)
    stop_type = Column(String(20), comment="PICKUP, DELIVERY, INTERMEDIATE")

    # Ocean-specific (aligned with p44 ocean tracking)
    vessel_name = Column(String(200))
    voyage_number = Column(String(100))
    vessel_imo = Column(String(20), comment="IMO vessel number")
    port_locode = Column(String(10), comment="UN/LOCODE port code")
    container_number = Column(String(50))
    seal_number = Column(String(50))

    # Equipment (aligned with p44 equipment identifier types)
    equipment_identifier_type = Column(String(20), comment="P44: CONTAINER_ID, RAIL_CAR_ID, TRAILER_ID")
    equipment_identifier_value = Column(String(100))

    # Temperature (for reefer tracking)
    temperature = Column(Double)
    temperature_uom = Column(String(5), default="F", comment="F or C")
    temperature_set_point = Column(Double)

    # Exception info (if event_type is EXCEPTION or DELAYED)
    exception_code = Column(String(50), comment="p44 exception code mapping")
    exception_description = Column(String(500))

    # Source
    source = Column(String(50), nullable=False, comment="P44, CARRIER_EDI, CARRIER_API, MANUAL, AGENT")
    raw_payload = Column(JSON, comment="Original p44/EDI payload for audit")

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    shipment = relationship("app.models.tms_entities.TMSShipment")
    leg = relationship("ShipmentLeg")
    stop = relationship("Site", foreign_keys=[stop_id])

    __table_args__ = (
        Index('idx_tracking_event_shipment', 'shipment_id', 'event_timestamp'),
        Index('idx_tracking_event_type', 'tenant_id', 'event_type', 'event_timestamp'),
        Index('idx_tracking_event_p44', 'p44_event_id'),
        Index('idx_tracking_event_container', 'container_number'),
    )


class ShipmentIdentifier(Base):
    """
    External identifiers for a shipment (p44-aligned)
    TMS Entity: shipment_identifier

    Aligned with project44 shipmentIdentifiers array:
    - type: BILL_OF_LADING, PURCHASE_ORDER, DELIVERY_NUMBER, etc.
    - value: The identifier value
    - primaryForType: Whether this is the primary identifier of its type

    Normalizes the reference_numbers JSON on Shipment into queryable records.
    """
    __tablename__ = "shipment_identifier"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shipment_id = Column(Integer, ForeignKey("tms_shipment.id", ondelete="CASCADE"), nullable=False)

    identifier_type = Column(String(50), nullable=False,
                             comment="P44 types: BILL_OF_LADING, PURCHASE_ORDER, DELIVERY_NUMBER, SKU, STOCK_KEEPING_UNIT, UNIVERSAL_PRODUCT_CODE")
    identifier_value = Column(String(200), nullable=False)
    is_primary = Column(Boolean, default=False, comment="P44: primaryForType flag")

    source = Column(String(50), comment="TMS, EDI, P44, MANUAL")

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    shipment = relationship("app.models.tms_entities.TMSShipment")

    __table_args__ = (
        Index('idx_shipment_ident_lookup', 'identifier_type', 'identifier_value'),
        Index('idx_shipment_ident_shipment', 'shipment_id'),
        UniqueConstraint('shipment_id', 'identifier_type', 'identifier_value',
                         name='uq_shipment_ident_type_value'),
    )


# ============================================================================
# Equipment Repositioning
# ============================================================================

class EquipmentMoveReason(str, PyEnum):
    """Why equipment was repositioned."""
    REBALANCE = "REBALANCE"           # Planned reposition to match demand
    EMPTY_RETURN = "EMPTY_RETURN"     # Return to origin / home terminal
    MAINTENANCE = "MAINTENANCE"       # Move to maintenance facility
    DEMURRAGE_AVOIDANCE = "DEMURRAGE_AVOIDANCE"
    DEADHEAD = "DEADHEAD"             # Unavoidable empty miles between loads
    CUSTOMER_REQUEST = "CUSTOMER_REQUEST"


class EquipmentMoveStatus(str, PyEnum):
    PLANNED = "PLANNED"
    DISPATCHED = "DISPATCHED"
    IN_TRANSIT = "IN_TRANSIT"
    ARRIVED = "ARRIVED"
    CANCELLED = "CANCELLED"


class EquipmentMove(Base):
    """
    Empty equipment repositioning move
    TMS Entity: equipment_move

    Tracks empty trailers/containers being moved between facilities to
    balance fleet supply against forecast demand. Primary data source for
    EquipmentRepositionTRM training and performance attribution.
    """
    __tablename__ = "equipment_move"

    id = Column(Integer, primary_key=True, autoincrement=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False)
    carrier_id = Column(Integer, ForeignKey("carrier.id", ondelete="SET NULL"))

    # Geography
    from_site_id = Column(Integer, ForeignKey("site.id"), nullable=False)
    to_site_id = Column(Integer, ForeignKey("site.id"), nullable=False)
    miles = Column(Double, nullable=False)

    # Timing
    dispatched_at = Column(DateTime)
    arrived_at = Column(DateTime)
    planned_arrival_at = Column(DateTime)

    # Economics
    cost = Column(Double, comment="All-in reposition cost (fuel + driver + opportunity)")
    cost_of_not_repositioning = Column(Double, comment="Estimated spot premium avoided")
    roi = Column(Double, comment="cost_of_not_repositioning / cost")

    # Classification
    reason = Column(SAEnum(EquipmentMoveReason, name="equipment_move_reason_enum"), nullable=False)
    status = Column(SAEnum(EquipmentMoveStatus, name="equipment_move_status_enum"),
                    nullable=False, default=EquipmentMoveStatus.PLANNED)

    # Agent traceability
    agent_decision_id = Column(String(100), comment="FK to powell_decisions for traceability")
    decision_rationale = Column(JSON, comment='{"surplus": 8, "deficit": 12, "roi": 2.3, ...}')

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    config_id = Column(Integer, ForeignKey("supply_chain_configs.id", ondelete="CASCADE"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    equipment = relationship("Equipment")
    carrier = relationship("Carrier")
    from_site = relationship("Site", foreign_keys=[from_site_id])
    to_site = relationship("Site", foreign_keys=[to_site_id])

    __table_args__ = (
        Index('idx_equipment_move_tenant', 'tenant_id', 'status'),
        Index('idx_equipment_move_equipment', 'equipment_id', 'dispatched_at'),
        Index('idx_equipment_move_lane', 'from_site_id', 'to_site_id'),
    )


# ── Backwards-compat alias ────────────────────────────────────────────
# The TMS-specific Shipment class was renamed to TMSShipment on
# 2026-04-15 so it no longer collides with the canonical
# azirella_data_model.master.entities.Shipment in Base's class
# registry. Existing callsites that import Shipment from this module
# keep working via this alias; new code should use TMSShipment.
Shipment = TMSShipment
