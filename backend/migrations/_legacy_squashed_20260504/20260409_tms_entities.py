"""Create TMS entity tables

Revision ID: 20260409_tms_01
Revises: 8145adf51ea2
Create Date: 2026-04-09 12:00:00.000000

Creates all TMS-specific tables: commodity hierarchy, carrier management,
equipment, loads, freight rates, tenders, dock scheduling, appointments,
exceptions, documents, tracking events, shipment identifiers, facility
config, operating schedules, yard locations, lane profiles, carrier
contracts, shipping forecasts, capacity targets, and transportation plans.

Also alters the existing shipment table to add TMS-specific columns.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260409_tms_01'
down_revision = '8145adf51ea2'
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Enum type names (created once, referenced by multiple tables)
# ---------------------------------------------------------------------------
transport_mode_enum = postgresql.ENUM(
    'FTL', 'LTL', 'PARCEL', 'FCL', 'LCL', 'BULK_OCEAN',
    'AIR_STD', 'AIR_EXPRESS', 'AIR_CHARTER',
    'RAIL_CARLOAD', 'RAIL_INTERMODAL', 'RAIL_UNIT',
    'INTERMODAL', 'DRAYAGE', 'LAST_MILE',
    name='transport_mode_enum', create_type=False)

equipment_type_enum = postgresql.ENUM(
    'DRY_VAN', 'REEFER', 'FLATBED', 'STEP_DECK', 'LOWBOY', 'TANKER',
    'CONTAINER_20', 'CONTAINER_40', 'CONTAINER_40HC', 'CONTAINER_45',
    'REEFER_CONTAINER', 'CHASSIS', 'RAILCAR_BOX', 'RAILCAR_HOPPER',
    'RAILCAR_TANK', 'SPRINTER_VAN', 'BOX_TRUCK',
    name='equipment_type_enum', create_type=False)

shipment_status_enum = postgresql.ENUM(
    'DRAFT', 'TENDERED', 'ACCEPTED', 'DECLINED', 'DISPATCHED',
    'IN_TRANSIT', 'AT_STOP', 'OUT_FOR_DELIVERY', 'DELIVERED',
    'POD_RECEIVED', 'INVOICED', 'CLOSED', 'CANCELLED', 'EXCEPTION',
    name='shipment_status_enum', create_type=False)

load_status_enum = postgresql.ENUM(
    'PLANNING', 'OPTIMIZING', 'READY', 'TENDERED', 'ASSIGNED',
    'IN_TRANSIT', 'DELIVERED', 'CLOSED',
    name='load_status_enum', create_type=False)

exception_type_enum = postgresql.ENUM(
    'LATE_PICKUP', 'MISSED_PICKUP', 'LATE_DELIVERY', 'MISSED_DELIVERY',
    'ROUTE_DEVIATION', 'TEMPERATURE_EXCURSION', 'DAMAGE', 'SHORTAGE',
    'OVERAGE', 'REFUSED', 'ROLLED_CONTAINER', 'PORT_CONGESTION',
    'CUSTOMS_HOLD', 'WEATHER_DELAY', 'CARRIER_BREAKDOWN',
    'DETENTION', 'DEMURRAGE', 'ACCESSORIAL_DISPUTE',
    name='exception_type_enum', create_type=False)

exception_severity_enum = postgresql.ENUM(
    'LOW', 'MEDIUM', 'HIGH', 'CRITICAL',
    name='exception_severity_enum', create_type=False)

exception_resolution_status_enum = postgresql.ENUM(
    'DETECTED', 'INVESTIGATING', 'ACTION_TAKEN', 'RESOLVED',
    'ESCALATED', 'CLOSED',
    name='exception_resolution_status_enum', create_type=False)

carrier_type_enum = postgresql.ENUM(
    'ASSET', 'BROKER', 'THREE_PL', 'FOUR_PL', 'OCEAN_LINE',
    'AIRLINE', 'RAILROAD', 'COURIER', 'DRAYAGE_CARRIER',
    name='carrier_type_enum', create_type=False)

tender_status_enum = postgresql.ENUM(
    'CREATED', 'SENT', 'ACCEPTED', 'DECLINED', 'COUNTER_OFFERED',
    'EXPIRED', 'CANCELLED',
    name='tender_status_enum', create_type=False)

appointment_type_enum = postgresql.ENUM(
    'PICKUP', 'DELIVERY', 'CROSS_DOCK', 'DROP_TRAILER',
    'LIVE_LOAD', 'LIVE_UNLOAD',
    name='appointment_type_enum', create_type=False)

appointment_status_enum = postgresql.ENUM(
    'REQUESTED', 'CONFIRMED', 'CHECKED_IN', 'AT_DOCK',
    'LOADING', 'UNLOADING', 'COMPLETED', 'NO_SHOW',
    'CANCELLED', 'RESCHEDULED',
    name='appointment_status_enum', create_type=False)

rate_type_enum = postgresql.ENUM(
    'CONTRACT', 'SPOT', 'MINI_BID', 'TARIFF', 'BENCHMARK',
    name='rate_type_enum', create_type=False)

tracking_event_type_enum = postgresql.ENUM(
    'PICKED_UP', 'DEPARTED', 'IN_TRANSIT', 'ARRIVAL_AT_STOP',
    'DEPARTED_STOP', 'OUT_FOR_DELIVERY', 'DELIVERED',
    'ARRIVED_AT_TERMINAL', 'DEPARTED_TERMINAL',
    'VESSEL_DEPARTED', 'VESSEL_ARRIVED', 'LOADED_ON_VESSEL',
    'DISCHARGED', 'GATE_IN', 'GATE_OUT', 'TRANSSHIPMENT',
    'CUSTOMS_CLEARED', 'CUSTOMS_HOLD',
    'RAIL_DEPARTED', 'RAIL_ARRIVED',
    'CREATED', 'UPDATED', 'CANCELLED',
    'APPOINTMENT_SET', 'UPDATED_DELIVERY_APPT',
    'DELAYED', 'EXCEPTION', 'RETURNED',
    'ETA_UPDATED',
    name='tracking_event_type_enum', create_type=False)

# transportation_config enums
facility_type_enum = postgresql.ENUM(
    'SHIPPER', 'CONSIGNEE', 'TERMINAL', 'CROSS_DOCK', 'YARD',
    'PORT', 'RAIL_TERMINAL', 'AIRPORT', 'DEPOT', 'BORDER_CROSSING',
    name='facility_type_enum', create_type=False)

contract_status_enum = postgresql.ENUM(
    'DRAFT', 'ACTIVE', 'EXPIRED', 'TERMINATED', 'RENEWED',
    name='contract_status_enum', create_type=False)

lane_direction_enum = postgresql.ENUM(
    'OUTBOUND', 'INBOUND', 'INTER_FACILITY', 'RETURN',
    name='lane_direction_enum', create_type=False)

# tms_planning enums
forecast_method_enum = postgresql.ENUM(
    'STATISTICAL', 'ML', 'CONSENSUS', 'EXTERNAL', 'CONFORMAL',
    name='forecast_method_enum', create_type=False)

plan_status_enum = postgresql.ENUM(
    'DRAFT', 'OPTIMIZING', 'READY', 'APPROVED', 'EXECUTING',
    'COMPLETED', 'SUPERSEDED',
    name='plan_status_enum', create_type=False)

plan_item_status_enum = postgresql.ENUM(
    'PLANNED', 'CARRIER_ASSIGNED', 'TENDERED', 'CONFIRMED',
    'IN_EXECUTION', 'COMPLETED', 'CANCELLED',
    name='plan_item_status_enum', create_type=False)


def upgrade():
    # ------------------------------------------------------------------
    # 1. Create all enum types
    # ------------------------------------------------------------------
    _enum_names = [
        'transport_mode_enum', 'equipment_type_enum', 'shipment_status_enum',
        'load_status_enum', 'exception_type_enum', 'exception_severity_enum',
        'exception_resolution_status_enum', 'carrier_type_enum',
        'tender_status_enum', 'appointment_type_enum', 'appointment_status_enum',
        'rate_type_enum', 'tracking_event_type_enum',
        'facility_type_enum', 'contract_status_enum', 'lane_direction_enum',
        'forecast_method_enum', 'plan_status_enum', 'plan_item_status_enum',
    ]
    _enum_defs = [
        transport_mode_enum, equipment_type_enum, shipment_status_enum,
        load_status_enum, exception_type_enum, exception_severity_enum,
        exception_resolution_status_enum, carrier_type_enum,
        tender_status_enum, appointment_type_enum, appointment_status_enum,
        rate_type_enum, tracking_event_type_enum,
        facility_type_enum, contract_status_enum, lane_direction_enum,
        forecast_method_enum, plan_status_enum, plan_item_status_enum,
    ]
    bind = op.get_bind()
    # Use raw SQL with IF NOT EXISTS for true idempotency within a transaction.
    # SQLAlchemy's bind.dialect.has_type() doesn't see types created in the
    # same uncommitted transaction, so it can issue duplicate CREATE TYPE.
    for enum_def in _enum_defs:
        enum_name = enum_def.name
        values_sql = ", ".join(f"'{v}'" for v in enum_def.enums)
        # Check if exists via system catalog (works inside transaction)
        result = bind.exec_driver_sql(
            f"SELECT 1 FROM pg_type WHERE typname = '{enum_name}'"
        ).first()
        if not result:
            bind.exec_driver_sql(
                f"CREATE TYPE {enum_name} AS ENUM ({values_sql})"
            )

    # ------------------------------------------------------------------
    # 2. Tables — created in dependency order (parents before children)
    # ------------------------------------------------------------------

    # ---- commodity_hierarchy (self-referencing) ----
    op.create_table(
        'commodity_hierarchy',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.String(500)),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('commodity_hierarchy.id', ondelete='SET NULL')),
        sa.Column('level', sa.Integer(), comment='0=Class, 1=Subclass, 2=Commodity'),
        sa.Column('sort_order', sa.Integer()),
        sa.Column('nmfc_class', sa.String(20), comment='National Motor Freight Classification'),
        sa.Column('freight_class', sa.String(20), comment='Freight class (50-500)'),
        sa.Column('is_hazmat', sa.Boolean(), server_default='false'),
        sa.Column('hazmat_class', sa.String(20)),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
    )
    op.create_index('idx_commodity_hier_tenant', 'commodity_hierarchy', ['tenant_id', 'level'])

    # ---- commodity ----
    op.create_table(
        'commodity',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('code', sa.String(100), nullable=False, comment='Commodity code / SKU'),
        sa.Column('description', sa.String(500)),
        sa.Column('hierarchy_id', sa.Integer(), sa.ForeignKey('commodity_hierarchy.id', ondelete='SET NULL')),
        sa.Column('nmfc_class', sa.String(20)),
        sa.Column('freight_class', sa.String(20), comment='Freight class (50-500)'),
        sa.Column('base_uom', sa.String(20), comment='EA, CS, PAL, LB, KG'),
        sa.Column('weight', sa.Double()),
        sa.Column('weight_uom', sa.String(20)),
        sa.Column('volume', sa.Double()),
        sa.Column('volume_uom', sa.String(20)),
        sa.Column('is_hazmat', sa.Boolean(), server_default='false'),
        sa.Column('hazmat_class', sa.String(20)),
        sa.Column('hazmat_un_number', sa.String(20)),
        sa.Column('is_stackable', sa.Boolean(), server_default='true'),
        sa.Column('is_temperature_sensitive', sa.Boolean(), server_default='false'),
        sa.Column('temp_min', sa.Double(), comment='Min temp (Fahrenheit)'),
        sa.Column('temp_max', sa.Double(), comment='Max temp (Fahrenheit)'),
        sa.Column('value_per_unit', sa.Double(), comment='Declared value per unit for insurance'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('source', sa.String(100)),
        sa.Column('source_event_id', sa.String(100)),
        sa.Column('source_update_dttm', sa.DateTime()),
        sa.Column('external_identifiers', postgresql.JSON(astext_type=sa.Text()), nullable=True,
                  comment='Typed external IDs: {sap_material, gtin, upc, ...}'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
        sa.UniqueConstraint('tenant_id', 'code', name='uq_commodity_tenant_code'),
    )
    op.create_index('idx_commodity_lookup', 'commodity', ['tenant_id', 'freight_class'])

    # ---- carrier ----
    op.create_table(
        'carrier',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('code', sa.String(100), nullable=False, comment='Carrier SCAC or internal code'),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('carrier_type', postgresql.ENUM(
            'ASSET', 'BROKER', 'THREE_PL', 'FOUR_PL', 'OCEAN_LINE',
            'AIRLINE', 'RAILROAD', 'COURIER', 'DRAYAGE_CARRIER',
            name='carrier_type_enum', create_type=False), nullable=False),
        sa.Column('scac', sa.String(10), comment='Standard Carrier Alpha Code'),
        sa.Column('mc_number', sa.String(20), comment='Motor Carrier number (FMCSA)'),
        sa.Column('dot_number', sa.String(20), comment='USDOT number'),
        sa.Column('usdot_safety_rating', sa.String(20), comment='Satisfactory/Conditional/Unsatisfactory'),
        sa.Column('modes', postgresql.JSON(astext_type=sa.Text()), comment='Supported modes: ["FTL", "LTL", "DRAYAGE"]'),
        sa.Column('equipment_types', postgresql.JSON(astext_type=sa.Text()), comment='Supported equipment: ["DRY_VAN", "REEFER"]'),
        sa.Column('service_regions', postgresql.JSON(astext_type=sa.Text()), comment='Geographic coverage'),
        sa.Column('is_hazmat_certified', sa.Boolean(), server_default='false'),
        sa.Column('is_bonded', sa.Boolean(), server_default='false'),
        sa.Column('insurance_limit', sa.Double(), comment='Cargo insurance limit (USD)'),
        sa.Column('primary_contact_name', sa.String(200)),
        sa.Column('primary_contact_email', sa.String(200)),
        sa.Column('primary_contact_phone', sa.String(50)),
        sa.Column('dispatch_email', sa.String(200)),
        sa.Column('dispatch_phone', sa.String(50)),
        sa.Column('tracking_api_type', sa.String(50), comment='EDI, API, PORTAL, P44, FOURKITES'),
        sa.Column('tracking_api_config', postgresql.JSON(astext_type=sa.Text()), comment='API credentials and endpoint config'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('onboarding_status', sa.String(20), server_default='PENDING',
                  comment='PENDING, IN_PROGRESS, ACTIVE, SUSPENDED'),
        sa.Column('onboarding_date', sa.Date()),
        sa.Column('last_shipment_date', sa.Date()),
        sa.Column('source', sa.String(100)),
        sa.Column('external_identifiers', postgresql.JSON(astext_type=sa.Text()), nullable=True,
                  comment='Typed external IDs: {sap_vendor, p44_id, ...}'),
        sa.Column('p44_carrier_id', sa.String(100), comment='project44 carrier identifier'),
        sa.Column('p44_identifier_type', sa.String(20),
                  comment='P44 CapacityProviderIdentifier.type: SCAC, DOT_NUMBER, MC_NUMBER, P44_EU, P44_GLOBAL, VAT, SYSTEM'),
        sa.Column('p44_account_group_code', sa.String(100), comment='P44 CapacityProviderAccountGroupInfo.code'),
        sa.Column('p44_account_code', sa.String(100), comment='P44 CapacityProviderAccountInfos.code'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
        sa.UniqueConstraint('tenant_id', 'code', name='uq_carrier_tenant_code'),
    )
    op.create_index('idx_carrier_tenant_active', 'carrier', ['tenant_id', 'is_active'])
    op.create_index('idx_carrier_scac', 'carrier', ['scac'])

    # ---- carrier_lane ----
    op.create_table(
        'carrier_lane',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('carrier_id', sa.Integer(), sa.ForeignKey('carrier.id', ondelete='CASCADE'), nullable=False),
        sa.Column('lane_id', sa.Integer(), sa.ForeignKey('transportation_lane.id', ondelete='CASCADE'), nullable=False),
        sa.Column('mode', postgresql.ENUM(
            'FTL', 'LTL', 'PARCEL', 'FCL', 'LCL', 'BULK_OCEAN',
            'AIR_STD', 'AIR_EXPRESS', 'AIR_CHARTER',
            'RAIL_CARLOAD', 'RAIL_INTERMODAL', 'RAIL_UNIT',
            'INTERMODAL', 'DRAYAGE', 'LAST_MILE',
            name='transport_mode_enum', create_type=False), nullable=False),
        sa.Column('equipment_type', postgresql.ENUM(
            'DRY_VAN', 'REEFER', 'FLATBED', 'STEP_DECK', 'LOWBOY', 'TANKER',
            'CONTAINER_20', 'CONTAINER_40', 'CONTAINER_40HC', 'CONTAINER_45',
            'REEFER_CONTAINER', 'CHASSIS', 'RAILCAR_BOX', 'RAILCAR_HOPPER',
            'RAILCAR_TANK', 'SPRINTER_VAN', 'BOX_TRUCK',
            name='equipment_type_enum', create_type=False)),
        sa.Column('weekly_capacity', sa.Integer(), comment='Max loads per week on this lane'),
        sa.Column('avg_transit_days', sa.Double(), comment='Average transit time in days'),
        sa.Column('transit_time_dist', postgresql.JSON(astext_type=sa.Text()),
                  comment='Stochastic: {"type": "lognormal", "mean": 3.2, "stddev": 0.5}'),
        sa.Column('priority', sa.Integer(), server_default='1', comment='Lower = preferred. Used in carrier waterfall'),
        sa.Column('is_primary', sa.Boolean(), server_default='false', comment='Primary carrier for this lane'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('eff_start_date', sa.Date()),
        sa.Column('eff_end_date', sa.Date()),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
    )
    op.create_index('idx_carrier_lane_lookup', 'carrier_lane', ['tenant_id', 'lane_id', 'mode'])
    op.create_index('idx_carrier_lane_carrier', 'carrier_lane', ['carrier_id', 'is_active'])

    # ---- equipment ----
    op.create_table(
        'equipment',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('equipment_id', sa.String(100), nullable=False, comment='Trailer/container number'),
        sa.Column('equipment_type', postgresql.ENUM(
            'DRY_VAN', 'REEFER', 'FLATBED', 'STEP_DECK', 'LOWBOY', 'TANKER',
            'CONTAINER_20', 'CONTAINER_40', 'CONTAINER_40HC', 'CONTAINER_45',
            'REEFER_CONTAINER', 'CHASSIS', 'RAILCAR_BOX', 'RAILCAR_HOPPER',
            'RAILCAR_TANK', 'SPRINTER_VAN', 'BOX_TRUCK',
            name='equipment_type_enum', create_type=False), nullable=False),
        sa.Column('carrier_id', sa.Integer(), sa.ForeignKey('carrier.id', ondelete='SET NULL')),
        sa.Column('length_ft', sa.Double()),
        sa.Column('width_ft', sa.Double()),
        sa.Column('height_ft', sa.Double()),
        sa.Column('max_weight_lbs', sa.Double()),
        sa.Column('max_volume_cuft', sa.Double()),
        sa.Column('tare_weight_lbs', sa.Double()),
        sa.Column('is_gps_tracked', sa.Boolean(), server_default='false'),
        sa.Column('is_temperature_controlled', sa.Boolean(), server_default='false'),
        sa.Column('temp_min', sa.Double()),
        sa.Column('temp_max', sa.Double()),
        sa.Column('status', sa.String(20), server_default='AVAILABLE',
                  comment='AVAILABLE, IN_USE, MAINTENANCE, RETIRED'),
        sa.Column('current_site_id', sa.Integer(), sa.ForeignKey('site.id')),
        sa.Column('last_known_lat', sa.Double()),
        sa.Column('last_known_lon', sa.Double()),
        sa.Column('last_position_update', sa.DateTime()),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('source', sa.String(100)),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
        sa.UniqueConstraint('tenant_id', 'equipment_id', name='uq_equipment_tenant_id'),
    )
    op.create_index('idx_equipment_status', 'equipment', ['tenant_id', 'status', 'equipment_type'])
    op.create_index('idx_equipment_site', 'equipment', ['current_site_id'])

    # ---- carrier_scorecard ----
    op.create_table(
        'carrier_scorecard',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('carrier_id', sa.Integer(), sa.ForeignKey('carrier.id', ondelete='CASCADE'), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('total_shipments', sa.Integer(), server_default='0'),
        sa.Column('total_loads', sa.Integer(), server_default='0'),
        sa.Column('on_time_pickup_pct', sa.Double(), comment='% pickups within window'),
        sa.Column('on_time_delivery_pct', sa.Double(), comment='% deliveries within window'),
        sa.Column('avg_transit_variance_hrs', sa.Double(), comment='Avg hours early(-)/late(+) vs committed'),
        sa.Column('avg_cost_per_mile', sa.Double()),
        sa.Column('avg_cost_per_shipment', sa.Double()),
        sa.Column('cost_vs_benchmark_pct', sa.Double(), comment='% above/below market benchmark'),
        sa.Column('damage_rate_pct', sa.Double()),
        sa.Column('claims_count', sa.Integer(), server_default='0'),
        sa.Column('claims_value', sa.Double(), server_default='0'),
        sa.Column('exception_rate_pct', sa.Double()),
        sa.Column('tender_acceptance_rate_pct', sa.Double(), comment='% tenders accepted'),
        sa.Column('avg_tender_response_hrs', sa.Double(), comment='Avg hours to respond to tender'),
        sa.Column('tracking_compliance_pct', sa.Double(), comment='% shipments with tracking updates'),
        sa.Column('composite_score', sa.Double(), comment='Weighted composite 0-100'),
        sa.Column('score_components', postgresql.JSON(astext_type=sa.Text()),
                  comment='{"on_time": 30, "cost": 25, "quality": 20, "responsiveness": 25}'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_scorecard_carrier_period', 'carrier_scorecard', ['carrier_id', 'period_start'])
    op.create_index('idx_scorecard_tenant', 'carrier_scorecard', ['tenant_id', 'period_start'])

    # ---- load ----
    op.create_table(
        'load',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('load_number', sa.String(100), nullable=False),
        sa.Column('status', postgresql.ENUM(
            'PLANNING', 'OPTIMIZING', 'READY', 'TENDERED', 'ASSIGNED',
            'IN_TRANSIT', 'DELIVERED', 'CLOSED',
            name='load_status_enum', create_type=False), nullable=False, server_default='PLANNING'),
        sa.Column('origin_site_id', sa.Integer(), sa.ForeignKey('site.id'), nullable=False),
        sa.Column('destination_site_id', sa.Integer(), sa.ForeignKey('site.id'), nullable=False),
        sa.Column('mode', postgresql.ENUM(
            'FTL', 'LTL', 'PARCEL', 'FCL', 'LCL', 'BULK_OCEAN',
            'AIR_STD', 'AIR_EXPRESS', 'AIR_CHARTER',
            'RAIL_CARLOAD', 'RAIL_INTERMODAL', 'RAIL_UNIT',
            'INTERMODAL', 'DRAYAGE', 'LAST_MILE',
            name='transport_mode_enum', create_type=False), nullable=False),
        sa.Column('equipment_type', postgresql.ENUM(
            'DRY_VAN', 'REEFER', 'FLATBED', 'STEP_DECK', 'LOWBOY', 'TANKER',
            'CONTAINER_20', 'CONTAINER_40', 'CONTAINER_40HC', 'CONTAINER_45',
            'REEFER_CONTAINER', 'CHASSIS', 'RAILCAR_BOX', 'RAILCAR_HOPPER',
            'RAILCAR_TANK', 'SPRINTER_VAN', 'BOX_TRUCK',
            name='equipment_type_enum', create_type=False)),
        sa.Column('equipment_id', sa.Integer(), sa.ForeignKey('equipment.id')),
        sa.Column('carrier_id', sa.Integer(), sa.ForeignKey('carrier.id')),
        sa.Column('stops', postgresql.JSON(astext_type=sa.Text()),
                  comment='[{"site_id": 1, "type": "PICKUP", "sequence": 1}, ...]'),
        sa.Column('total_weight', sa.Double(), server_default='0'),
        sa.Column('total_volume', sa.Double(), server_default='0'),
        sa.Column('total_pallets', sa.Integer(), server_default='0'),
        sa.Column('weight_utilization_pct', sa.Double(), comment='% of max weight used'),
        sa.Column('volume_utilization_pct', sa.Double(), comment='% of max volume used'),
        sa.Column('linear_ft_used', sa.Double(), comment='Linear feet of trailer used'),
        sa.Column('planned_departure', sa.DateTime()),
        sa.Column('planned_arrival', sa.DateTime()),
        sa.Column('actual_departure', sa.DateTime()),
        sa.Column('actual_arrival', sa.DateTime()),
        sa.Column('total_cost', sa.Double()),
        sa.Column('cost_per_mile', sa.Double()),
        sa.Column('total_miles', sa.Double()),
        sa.Column('empty_miles', sa.Double(), server_default='0', comment='Deadhead miles'),
        sa.Column('optimization_score', sa.Double(), comment='Agent optimization quality 0-1'),
        sa.Column('optimization_metadata', postgresql.JSON(astext_type=sa.Text()),
                  comment='{"method": "TRM", "alternatives_considered": 5}'),
        sa.Column('source', sa.String(100)),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
        sa.UniqueConstraint('tenant_id', 'load_number', name='uq_load_tenant_number'),
    )
    op.create_index('idx_load_status', 'load', ['tenant_id', 'status'])
    op.create_index('idx_load_dates', 'load', ['tenant_id', 'planned_departure'])
    op.create_index('idx_load_carrier', 'load', ['carrier_id', 'status'])

    # ------------------------------------------------------------------
    # 3. Create tms_shipment table (TMS-domain shipments)
    # ------------------------------------------------------------------
    # NOTE: We use a separate `tms_shipment` table rather than ALTERing the
    # existing `shipment` table. The two represent different concepts:
    #   - sc_entities.Shipment / shipment: Material visibility (String PK,
    #     legacy SC schema)
    #   - tms_entities.Shipment / tms_shipment: Freight movement (Integer PK,
    #     full TMS lifecycle)
    # Keeping them separate avoids type conflicts and import ambiguity.
    op.create_table(
        'tms_shipment',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('shipment_number', sa.String(100), nullable=False, comment='Business reference number'),
        sa.Column('status', sa.String(30), nullable=False, server_default='DRAFT'),
        sa.Column('origin_site_id', sa.Integer(), sa.ForeignKey('site.id'), nullable=False),
        sa.Column('destination_site_id', sa.Integer(), sa.ForeignKey('site.id'), nullable=False),
        sa.Column('lane_id', sa.Integer(), sa.ForeignKey('transportation_lane.id')),
        sa.Column('commodity_id', sa.Integer(), sa.ForeignKey('commodity.id')),
        sa.Column('quantity', sa.Double()),
        sa.Column('weight', sa.Double()),
        sa.Column('weight_uom', sa.String(20), server_default='LBS'),
        sa.Column('volume', sa.Double()),
        sa.Column('volume_uom', sa.String(20)),
        sa.Column('pallet_count', sa.Integer()),
        sa.Column('piece_count', sa.Integer()),
        sa.Column('declared_value', sa.Double()),
        sa.Column('mode', sa.String(30)),
        sa.Column('required_equipment', sa.String(30)),
        sa.Column('is_hazmat', sa.Boolean(), server_default='false'),
        sa.Column('is_temperature_sensitive', sa.Boolean(), server_default='false'),
        sa.Column('temp_min', sa.Double()),
        sa.Column('temp_max', sa.Double()),
        sa.Column('requested_pickup_date', sa.DateTime()),
        sa.Column('requested_delivery_date', sa.DateTime()),
        sa.Column('earliest_pickup', sa.DateTime()),
        sa.Column('latest_pickup', sa.DateTime()),
        sa.Column('earliest_delivery', sa.DateTime()),
        sa.Column('latest_delivery', sa.DateTime()),
        sa.Column('actual_pickup_date', sa.DateTime()),
        sa.Column('actual_delivery_date', sa.DateTime()),
        sa.Column('load_id', sa.Integer()),
        sa.Column('carrier_id', sa.Integer(), sa.ForeignKey('carrier.id')),
        sa.Column('equipment_id', sa.Integer(), sa.ForeignKey('equipment.id')),
        sa.Column('estimated_cost', sa.Double()),
        sa.Column('actual_cost', sa.Double()),
        sa.Column('freight_charge', sa.Double()),
        sa.Column('accessorial_charges', postgresql.JSON(astext_type=sa.Text())),
        sa.Column('total_charge', sa.Double()),
        sa.Column('priority', sa.Integer()),
        sa.Column('service_level', sa.String(50)),
        sa.Column('special_instructions', sa.Text()),
        sa.Column('reference_numbers', postgresql.JSON(astext_type=sa.Text())),
        sa.Column('current_lat', sa.Double()),
        sa.Column('current_lon', sa.Double()),
        sa.Column('current_location', sa.String(255)),
        sa.Column('last_tracking_update', sa.DateTime()),
        sa.Column('estimated_arrival', sa.DateTime()),
        sa.Column('eta_confidence', postgresql.JSON(astext_type=sa.Text())),
        sa.Column('delivery_risk_score', sa.Double()),
        sa.Column('risk_level', sa.String(20)),
        sa.Column('p44_shipment_id', sa.String(200)),
        sa.Column('p44_tracking_url', sa.String(500)),
        sa.Column('external_identifiers', postgresql.JSON(astext_type=sa.Text())),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE')),
        sa.Column('source', sa.String(100)),
        sa.Column('source_event_id', sa.String(100)),
        sa.Column('source_update_dttm', sa.DateTime()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
    )
    op.create_index('idx_tms_shipment_tenant_status', 'tms_shipment', ['tenant_id', 'status'])
    op.create_index('idx_tms_shipment_dates', 'tms_shipment', ['tenant_id', 'requested_pickup_date', 'requested_delivery_date'])
    op.create_index('idx_tms_shipment_carrier', 'tms_shipment', ['carrier_id', 'status'])
    op.create_index('idx_tms_shipment_lane', 'tms_shipment', ['lane_id', 'status'])
    op.create_index('idx_tms_shipment_origin_dest', 'tms_shipment', ['origin_site_id', 'destination_site_id'])

    # ---- shipment_leg ----
    op.create_table(
        'shipment_leg',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('shipment_id', sa.Integer(), sa.ForeignKey('tms_shipment.id', ondelete='CASCADE'), nullable=False),
        sa.Column('leg_sequence', sa.Integer(), nullable=False, comment='1-based ordering'),
        sa.Column('from_site_id', sa.Integer(), sa.ForeignKey('site.id'), nullable=False),
        sa.Column('to_site_id', sa.Integer(), sa.ForeignKey('site.id'), nullable=False),
        sa.Column('mode', postgresql.ENUM(
            'FTL', 'LTL', 'PARCEL', 'FCL', 'LCL', 'BULK_OCEAN',
            'AIR_STD', 'AIR_EXPRESS', 'AIR_CHARTER',
            'RAIL_CARLOAD', 'RAIL_INTERMODAL', 'RAIL_UNIT',
            'INTERMODAL', 'DRAYAGE', 'LAST_MILE',
            name='transport_mode_enum', create_type=False), nullable=False),
        sa.Column('carrier_id', sa.Integer(), sa.ForeignKey('carrier.id')),
        sa.Column('equipment_id', sa.Integer(), sa.ForeignKey('equipment.id')),
        sa.Column('planned_departure', sa.DateTime()),
        sa.Column('planned_arrival', sa.DateTime()),
        sa.Column('actual_departure', sa.DateTime()),
        sa.Column('actual_arrival', sa.DateTime()),
        sa.Column('status', sa.String(20), server_default='PLANNED',
                  comment='PLANNED, IN_TRANSIT, COMPLETED, EXCEPTION'),
        sa.Column('current_lat', sa.Double()),
        sa.Column('current_lon', sa.Double()),
        sa.Column('last_tracking_update', sa.DateTime()),
        sa.Column('vessel_name', sa.String(200)),
        sa.Column('voyage_number', sa.String(100)),
        sa.Column('container_number', sa.String(50)),
        sa.Column('leg_cost', sa.Double()),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
    )
    op.create_index('idx_leg_shipment', 'shipment_leg', ['shipment_id', 'leg_sequence'])

    # ---- load_item ----
    op.create_table(
        'load_item',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('load_id', sa.Integer(), sa.ForeignKey('load.id', ondelete='CASCADE'), nullable=False),
        sa.Column('shipment_id', sa.Integer(), sa.ForeignKey('tms_shipment.id'), nullable=False),
        sa.Column('commodity_id', sa.Integer(), sa.ForeignKey('commodity.id')),
        sa.Column('quantity', sa.Double()),
        sa.Column('weight', sa.Double()),
        sa.Column('volume', sa.Double()),
        sa.Column('pallet_count', sa.Integer()),
        sa.Column('load_sequence', sa.Integer(), comment='Loading order (first on = last off)'),
        sa.Column('position', sa.String(50), comment='NOSE, CENTER, TAIL, UPPER, LOWER'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_load_item_load', 'load_item', ['load_id'])

    # ---- freight_rate ----
    op.create_table(
        'freight_rate',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('carrier_id', sa.Integer(), sa.ForeignKey('carrier.id', ondelete='CASCADE'), nullable=False),
        sa.Column('lane_id', sa.Integer(), sa.ForeignKey('transportation_lane.id'), nullable=False),
        sa.Column('mode', postgresql.ENUM(
            'FTL', 'LTL', 'PARCEL', 'FCL', 'LCL', 'BULK_OCEAN',
            'AIR_STD', 'AIR_EXPRESS', 'AIR_CHARTER',
            'RAIL_CARLOAD', 'RAIL_INTERMODAL', 'RAIL_UNIT',
            'INTERMODAL', 'DRAYAGE', 'LAST_MILE',
            name='transport_mode_enum', create_type=False), nullable=False),
        sa.Column('equipment_type', postgresql.ENUM(
            'DRY_VAN', 'REEFER', 'FLATBED', 'STEP_DECK', 'LOWBOY', 'TANKER',
            'CONTAINER_20', 'CONTAINER_40', 'CONTAINER_40HC', 'CONTAINER_45',
            'REEFER_CONTAINER', 'CHASSIS', 'RAILCAR_BOX', 'RAILCAR_HOPPER',
            'RAILCAR_TANK', 'SPRINTER_VAN', 'BOX_TRUCK',
            name='equipment_type_enum', create_type=False)),
        sa.Column('rate_type', postgresql.ENUM(
            'CONTRACT', 'SPOT', 'MINI_BID', 'TARIFF', 'BENCHMARK',
            name='rate_type_enum', create_type=False), nullable=False),
        sa.Column('rate_per_mile', sa.Double()),
        sa.Column('rate_flat', sa.Double(), comment='Flat rate for the lane'),
        sa.Column('rate_per_cwt', sa.Double(), comment='Rate per hundredweight (LTL)'),
        sa.Column('rate_per_unit', sa.Double(), comment='Rate per pallet/container'),
        sa.Column('min_charge', sa.Double()),
        sa.Column('fuel_surcharge_pct', sa.Double(), comment='Fuel surcharge as % of line haul'),
        sa.Column('fuel_surcharge_method', sa.String(50), comment='DOE_INDEX, FLAT, INCLUDED'),
        sa.Column('accessorial_schedule', postgresql.JSON(astext_type=sa.Text()),
                  comment='{"DETENTION": 75.0, "LIFTGATE": 150.0, "INSIDE_DELIVERY": 200.0}'),
        sa.Column('eff_start_date', sa.Date(), nullable=False),
        sa.Column('eff_end_date', sa.Date(), nullable=False),
        sa.Column('contract_number', sa.String(100)),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('min_volume_per_week', sa.Integer(), comment='Committed loads/week for this rate'),
        sa.Column('max_volume_per_week', sa.Integer()),
        sa.Column('market_rate_at_contract', sa.Double(), comment='Market rate when contract signed'),
        sa.Column('source', sa.String(100)),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
    )
    op.create_index('idx_rate_lookup', 'freight_rate', ['tenant_id', 'lane_id', 'mode', 'carrier_id'])
    op.create_index('idx_rate_validity', 'freight_rate', ['eff_start_date', 'eff_end_date', 'is_active'])

    # ---- freight_tender ----
    op.create_table(
        'freight_tender',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('shipment_id', sa.Integer(), sa.ForeignKey('tms_shipment.id', ondelete='CASCADE')),
        sa.Column('load_id', sa.Integer(), sa.ForeignKey('load.id', ondelete='CASCADE')),
        sa.Column('carrier_id', sa.Integer(), sa.ForeignKey('carrier.id'), nullable=False),
        sa.Column('rate_id', sa.Integer(), sa.ForeignKey('freight_rate.id')),
        sa.Column('tender_sequence', sa.Integer(), nullable=False, comment='Position in carrier waterfall'),
        sa.Column('status', postgresql.ENUM(
            'CREATED', 'SENT', 'ACCEPTED', 'DECLINED', 'COUNTER_OFFERED',
            'EXPIRED', 'CANCELLED',
            name='tender_status_enum', create_type=False), nullable=False, server_default='CREATED'),
        sa.Column('offered_rate', sa.Double(), nullable=False),
        sa.Column('counter_rate', sa.Double(), comment="Carrier's counter-offer rate"),
        sa.Column('final_rate', sa.Double(), comment='Agreed rate after negotiation'),
        sa.Column('tendered_at', sa.DateTime()),
        sa.Column('response_deadline', sa.DateTime()),
        sa.Column('responded_at', sa.DateTime()),
        sa.Column('decline_reason', sa.String(500)),
        sa.Column('agent_decision_id', sa.String(100), comment='FK to powell_decisions for traceability'),
        sa.Column('selection_rationale', postgresql.JSON(astext_type=sa.Text()),
                  comment='{"score": 0.87, "factors": {"cost": 0.3, "otd": 0.4, ...}}'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
    )
    op.create_index('idx_tender_shipment', 'freight_tender', ['shipment_id', 'tender_sequence'])
    op.create_index('idx_tender_carrier', 'freight_tender', ['carrier_id', 'status'])

    # ---- dock_door ----
    op.create_table(
        'dock_door',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('site.id', ondelete='CASCADE'), nullable=False),
        sa.Column('door_number', sa.String(20), nullable=False),
        sa.Column('door_type', sa.String(20), comment='INBOUND, OUTBOUND, BOTH'),
        sa.Column('equipment_compatible', postgresql.JSON(astext_type=sa.Text()),
                  comment='["DRY_VAN", "REEFER", "FLATBED"]'),
        sa.Column('has_leveler', sa.Boolean(), server_default='true'),
        sa.Column('has_restraint', sa.Boolean(), server_default='true'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('site_id', 'door_number', name='uq_dock_door_site_number'),
    )

    # ---- appointment ----
    op.create_table(
        'appointment',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('site.id', ondelete='CASCADE'), nullable=False),
        sa.Column('dock_door_id', sa.Integer(), sa.ForeignKey('dock_door.id')),
        sa.Column('shipment_id', sa.Integer(), sa.ForeignKey('tms_shipment.id')),
        sa.Column('load_id', sa.Integer(), sa.ForeignKey('load.id')),
        sa.Column('appointment_type', postgresql.ENUM(
            'PICKUP', 'DELIVERY', 'CROSS_DOCK', 'DROP_TRAILER',
            'LIVE_LOAD', 'LIVE_UNLOAD',
            name='appointment_type_enum', create_type=False), nullable=False),
        sa.Column('status', postgresql.ENUM(
            'REQUESTED', 'CONFIRMED', 'CHECKED_IN', 'AT_DOCK',
            'LOADING', 'UNLOADING', 'COMPLETED', 'NO_SHOW',
            'CANCELLED', 'RESCHEDULED',
            name='appointment_status_enum', create_type=False), nullable=False, server_default='REQUESTED'),
        sa.Column('scheduled_start', sa.DateTime(), nullable=False),
        sa.Column('scheduled_end', sa.DateTime(), nullable=False),
        sa.Column('actual_arrival', sa.DateTime()),
        sa.Column('actual_start', sa.DateTime()),
        sa.Column('actual_end', sa.DateTime()),
        sa.Column('actual_departure', sa.DateTime()),
        sa.Column('dwell_time_minutes', sa.Integer(), comment='Total time at facility'),
        sa.Column('dock_time_minutes', sa.Integer(), comment='Time at dock door'),
        sa.Column('wait_time_minutes', sa.Integer(), comment='Time waiting for dock assignment'),
        sa.Column('carrier_id', sa.Integer(), sa.ForeignKey('carrier.id')),
        sa.Column('driver_name', sa.String(200)),
        sa.Column('driver_phone', sa.String(50)),
        sa.Column('trailer_number', sa.String(100)),
        sa.Column('special_instructions', sa.Text()),
        sa.Column('reference_numbers', postgresql.JSON(astext_type=sa.Text())),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
    )
    op.create_index('idx_appointment_site_time', 'appointment', ['site_id', 'scheduled_start', 'scheduled_end'])
    op.create_index('idx_appointment_status', 'appointment', ['tenant_id', 'status', 'appointment_type'])
    op.create_index('idx_appointment_dock', 'appointment', ['dock_door_id', 'scheduled_start'])

    # ---- shipment_exception ----
    op.create_table(
        'shipment_exception',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('shipment_id', sa.Integer(), sa.ForeignKey('tms_shipment.id', ondelete='CASCADE'), nullable=False),
        sa.Column('leg_id', sa.Integer(), sa.ForeignKey('shipment_leg.id')),
        sa.Column('exception_type', postgresql.ENUM(
            'LATE_PICKUP', 'MISSED_PICKUP', 'LATE_DELIVERY', 'MISSED_DELIVERY',
            'ROUTE_DEVIATION', 'TEMPERATURE_EXCURSION', 'DAMAGE', 'SHORTAGE',
            'OVERAGE', 'REFUSED', 'ROLLED_CONTAINER', 'PORT_CONGESTION',
            'CUSTOMS_HOLD', 'WEATHER_DELAY', 'CARRIER_BREAKDOWN',
            'DETENTION', 'DEMURRAGE', 'ACCESSORIAL_DISPUTE',
            name='exception_type_enum', create_type=False), nullable=False),
        sa.Column('severity', postgresql.ENUM(
            'LOW', 'MEDIUM', 'HIGH', 'CRITICAL',
            name='exception_severity_enum', create_type=False), nullable=False, server_default='MEDIUM'),
        sa.Column('resolution_status', postgresql.ENUM(
            'DETECTED', 'INVESTIGATING', 'ACTION_TAKEN', 'RESOLVED',
            'ESCALATED', 'CLOSED',
            name='exception_resolution_status_enum', create_type=False), nullable=False, server_default='DETECTED'),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('detected_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('resolved_at', sa.DateTime()),
        sa.Column('estimated_delay_hrs', sa.Double()),
        sa.Column('estimated_cost_impact', sa.Double()),
        sa.Column('revenue_at_risk', sa.Double()),
        sa.Column('impact_assessment', postgresql.JSON(astext_type=sa.Text()),
                  comment='{"service": 0.8, "cost": 0.3, "risk": 0.6}'),
        sa.Column('detection_source', sa.String(50), comment='P44, CARRIER_EDI, AGENT, MANUAL, WEATHER_API'),
        sa.Column('detection_event_id', sa.String(200), comment='External event ID from detection source'),
        sa.Column('exception_lat', sa.Double()),
        sa.Column('exception_lon', sa.Double()),
        sa.Column('exception_location_desc', sa.String(500)),
        sa.Column('agent_decision_id', sa.String(100), comment='FK to powell_decisions for resolution traceability'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
    )
    op.create_index('idx_exception_shipment', 'shipment_exception', ['shipment_id'])
    op.create_index('idx_exception_status', 'shipment_exception', ['tenant_id', 'resolution_status', 'severity'])
    op.create_index('idx_exception_type', 'shipment_exception', ['tenant_id', 'exception_type', 'detected_at'])

    # ---- exception_resolution ----
    op.create_table(
        'exception_resolution',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('exception_id', sa.Integer(), sa.ForeignKey('shipment_exception.id', ondelete='CASCADE'), nullable=False),
        sa.Column('action_type', sa.String(50), nullable=False,
                  comment='REROUTE, REBOOK, EXPEDITE, CARRIER_CHANGE, RESCHEDULE, CLAIM, ACCEPT_DELAY'),
        sa.Column('action_description', sa.Text()),
        sa.Column('action_by', sa.String(20), nullable=False, comment='AGENT or USER'),
        sa.Column('aiio_status', sa.String(20), nullable=False, comment='ACTIONED, INFORMED, INSPECTED, OVERRIDDEN'),
        sa.Column('override_reason', sa.Text()),
        sa.Column('original_action', postgresql.JSON(astext_type=sa.Text()),
                  comment="Agent's original action before user override"),
        sa.Column('outcome', sa.String(50), comment='RESOLVED, PARTIALLY_RESOLVED, INEFFECTIVE, PENDING'),
        sa.Column('cost_of_resolution', sa.Double()),
        sa.Column('delay_mitigated_hrs', sa.Double()),
        sa.Column('resolved_by_user_id', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('agent_decision_id', sa.String(100)),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_resolution_exception', 'exception_resolution', ['exception_id'])

    # ---- bill_of_lading ----
    op.create_table(
        'bill_of_lading',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('shipment_id', sa.Integer(), sa.ForeignKey('tms_shipment.id', ondelete='CASCADE'), nullable=False),
        sa.Column('bol_number', sa.String(100), nullable=False),
        sa.Column('shipper_name', sa.String(200)),
        sa.Column('shipper_address', sa.Text()),
        sa.Column('consignee_name', sa.String(200)),
        sa.Column('consignee_address', sa.Text()),
        sa.Column('carrier_name', sa.String(200)),
        sa.Column('line_items', postgresql.JSON(astext_type=sa.Text()),
                  comment='[{"description": "Canned goods", "weight": 5000, "class": "70", "nmfc": "12345"}]'),
        sa.Column('total_weight', sa.Double()),
        sa.Column('total_pieces', sa.Integer()),
        sa.Column('freight_class', sa.String(20)),
        sa.Column('freight_terms', sa.String(20), comment='PREPAID, COLLECT, THIRD_PARTY'),
        sa.Column('special_instructions', sa.Text()),
        sa.Column('issued_date', sa.Date()),
        sa.Column('document_url', sa.String(500), comment='Link to scanned/generated BOL document'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('tenant_id', 'bol_number', name='uq_bol_tenant_number'),
    )

    # ---- proof_of_delivery ----
    op.create_table(
        'proof_of_delivery',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('shipment_id', sa.Integer(), sa.ForeignKey('tms_shipment.id', ondelete='CASCADE'), nullable=False),
        sa.Column('signed_by', sa.String(200)),
        sa.Column('signed_at', sa.DateTime()),
        sa.Column('delivery_date', sa.DateTime(), nullable=False),
        sa.Column('delivery_status', sa.String(20), comment='FULL, PARTIAL, REFUSED, DAMAGED'),
        sa.Column('pieces_received', sa.Integer()),
        sa.Column('pieces_expected', sa.Integer()),
        sa.Column('damage_noted', sa.Boolean(), server_default='false'),
        sa.Column('damage_description', sa.Text()),
        sa.Column('shortage_noted', sa.Boolean(), server_default='false'),
        sa.Column('shortage_description', sa.Text()),
        sa.Column('photo_urls', postgresql.JSON(astext_type=sa.Text()),
                  comment='["https://...pod_photo_1.jpg", ...]'),
        sa.Column('document_url', sa.String(500)),
        sa.Column('signature_url', sa.String(500)),
        sa.Column('notes', sa.Text()),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_pod_shipment', 'proof_of_delivery', ['shipment_id'])

    # ---- tracking_event ----
    op.create_table(
        'tracking_event',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('shipment_id', sa.Integer(), sa.ForeignKey('tms_shipment.id', ondelete='CASCADE'), nullable=False),
        sa.Column('leg_id', sa.Integer(), sa.ForeignKey('shipment_leg.id')),
        sa.Column('event_type', postgresql.ENUM(
            'PICKED_UP', 'DEPARTED', 'IN_TRANSIT', 'ARRIVAL_AT_STOP',
            'DEPARTED_STOP', 'OUT_FOR_DELIVERY', 'DELIVERED',
            'ARRIVED_AT_TERMINAL', 'DEPARTED_TERMINAL',
            'VESSEL_DEPARTED', 'VESSEL_ARRIVED', 'LOADED_ON_VESSEL',
            'DISCHARGED', 'GATE_IN', 'GATE_OUT', 'TRANSSHIPMENT',
            'CUSTOMS_CLEARED', 'CUSTOMS_HOLD',
            'RAIL_DEPARTED', 'RAIL_ARRIVED',
            'CREATED', 'UPDATED', 'CANCELLED',
            'APPOINTMENT_SET', 'UPDATED_DELIVERY_APPT',
            'DELAYED', 'EXCEPTION', 'RETURNED',
            'ETA_UPDATED',
            name='tracking_event_type_enum', create_type=False), nullable=False),
        sa.Column('event_timestamp', sa.DateTime(), nullable=False, comment='When the event occurred'),
        sa.Column('received_timestamp', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'),
                  comment='When we received the event'),
        sa.Column('p44_event_id', sa.String(200), comment='project44 eventId (UUID)'),
        sa.Column('p44_shipment_id', sa.String(200), comment='project44 masterShipmentId'),
        sa.Column('p44_shipment_leg_id', sa.String(200), comment='project44 shipmentLegId'),
        sa.Column('location_name', sa.String(200)),
        sa.Column('address_line_1', sa.String(255)),
        sa.Column('city', sa.String(100)),
        sa.Column('state', sa.String(100)),
        sa.Column('postal_code', sa.String(50)),
        sa.Column('country', sa.String(10)),
        sa.Column('latitude', sa.Double()),
        sa.Column('longitude', sa.Double()),
        sa.Column('status_code', sa.String(50), comment='Carrier-specific status code'),
        sa.Column('status_description', sa.String(500)),
        sa.Column('estimated_arrival', sa.DateTime(), comment='ETA at next stop'),
        sa.Column('estimated_departure', sa.DateTime()),
        sa.Column('eta_confidence', postgresql.JSON(astext_type=sa.Text()),
                  comment='{"p10": "...", "p50": "...", "p90": "..."} from conformal prediction'),
        sa.Column('stop_id', sa.Integer(), sa.ForeignKey('site.id')),
        sa.Column('stop_sequence', sa.Integer()),
        sa.Column('stop_type', sa.String(20), comment='PICKUP, DELIVERY, INTERMEDIATE'),
        sa.Column('vessel_name', sa.String(200)),
        sa.Column('voyage_number', sa.String(100)),
        sa.Column('vessel_imo', sa.String(20), comment='IMO vessel number'),
        sa.Column('port_locode', sa.String(10), comment='UN/LOCODE port code'),
        sa.Column('container_number', sa.String(50)),
        sa.Column('seal_number', sa.String(50)),
        sa.Column('equipment_identifier_type', sa.String(20),
                  comment='P44: CONTAINER_ID, RAIL_CAR_ID, TRAILER_ID'),
        sa.Column('equipment_identifier_value', sa.String(100)),
        sa.Column('temperature', sa.Double()),
        sa.Column('temperature_uom', sa.String(5), server_default='F', comment='F or C'),
        sa.Column('temperature_set_point', sa.Double()),
        sa.Column('exception_code', sa.String(50), comment='p44 exception code mapping'),
        sa.Column('exception_description', sa.String(500)),
        sa.Column('source', sa.String(50), nullable=False, comment='P44, CARRIER_EDI, CARRIER_API, MANUAL, AGENT'),
        sa.Column('raw_payload', postgresql.JSON(astext_type=sa.Text()), comment='Original p44/EDI payload for audit'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_tracking_event_shipment', 'tracking_event', ['shipment_id', 'event_timestamp'])
    op.create_index('idx_tracking_event_type', 'tracking_event', ['tenant_id', 'event_type', 'event_timestamp'])
    op.create_index('idx_tracking_event_p44', 'tracking_event', ['p44_event_id'])
    op.create_index('idx_tracking_event_container', 'tracking_event', ['container_number'])

    # ---- shipment_identifier ----
    op.create_table(
        'shipment_identifier',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('shipment_id', sa.Integer(), sa.ForeignKey('tms_shipment.id', ondelete='CASCADE'), nullable=False),
        sa.Column('identifier_type', sa.String(50), nullable=False,
                  comment='P44 types: BILL_OF_LADING, PURCHASE_ORDER, DELIVERY_NUMBER, etc.'),
        sa.Column('identifier_value', sa.String(200), nullable=False),
        sa.Column('is_primary', sa.Boolean(), server_default='false', comment='P44: primaryForType flag'),
        sa.Column('source', sa.String(50), comment='TMS, EDI, P44, MANUAL'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('shipment_id', 'identifier_type', 'identifier_value',
                            name='uq_shipment_ident_type_value'),
    )
    op.create_index('idx_shipment_ident_lookup', 'shipment_identifier', ['identifier_type', 'identifier_value'])
    op.create_index('idx_shipment_ident_shipment', 'shipment_identifier', ['shipment_id'])

    # ------------------------------------------------------------------
    # 4. Tables from transportation_config.py
    # ------------------------------------------------------------------

    # ---- facility_config ----
    op.create_table(
        'facility_config',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('site.id', ondelete='CASCADE'), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('facility_type', postgresql.ENUM(
            'SHIPPER', 'CONSIGNEE', 'TERMINAL', 'CROSS_DOCK', 'YARD',
            'PORT', 'RAIL_TERMINAL', 'AIRPORT', 'DEPOT', 'BORDER_CROSSING',
            name='facility_type_enum', create_type=False), nullable=False),
        sa.Column('total_dock_doors', sa.Integer(), server_default='0'),
        sa.Column('inbound_dock_doors', sa.Integer(), server_default='0'),
        sa.Column('outbound_dock_doors', sa.Integer(), server_default='0'),
        sa.Column('avg_load_time_minutes', sa.Integer(), server_default='60',
                  comment='Average loading time per trailer'),
        sa.Column('avg_unload_time_minutes', sa.Integer(), server_default='60',
                  comment='Average unloading time per trailer'),
        sa.Column('total_yard_spots', sa.Integer(), server_default='0',
                  comment='Total trailer/container staging spots'),
        sa.Column('reefer_yard_spots', sa.Integer(), server_default='0',
                  comment='Spots with reefer plug-ins'),
        sa.Column('hazmat_capable', sa.Boolean(), server_default='false'),
        sa.Column('max_daily_inbound_loads', sa.Integer(), comment='Max loads that can be received per day'),
        sa.Column('max_daily_outbound_loads', sa.Integer(), comment='Max loads that can be shipped per day'),
        sa.Column('avg_daily_volume', sa.Double(), comment='Average daily volume (weight or units)'),
        sa.Column('operating_hours', postgresql.JSON(astext_type=sa.Text()),
                  comment='{"mon": {"open": "06:00", "close": "22:00"}, ...}'),
        sa.Column('timezone', sa.String(50), server_default='America/Chicago'),
        sa.Column('requires_appointment', sa.Boolean(), server_default='true'),
        sa.Column('appointment_lead_time_hrs', sa.Integer(), server_default='24',
                  comment='Min hours before appointment'),
        sa.Column('default_appointment_duration_min', sa.Integer(), server_default='60'),
        sa.Column('capabilities', postgresql.JSON(astext_type=sa.Text()),
                  comment='["LIVE_LOAD", "DROP_TRAILER", "CROSS_DOCK", "REEFER", "HAZMAT"]'),
        sa.Column('equipment_compatible', postgresql.JSON(astext_type=sa.Text()),
                  comment='["DRY_VAN", "REEFER", "FLATBED", "CONTAINER_40"]'),
        sa.Column('shipping_contact_name', sa.String(200)),
        sa.Column('shipping_contact_email', sa.String(200)),
        sa.Column('shipping_contact_phone', sa.String(50)),
        sa.Column('receiving_contact_name', sa.String(200)),
        sa.Column('receiving_contact_email', sa.String(200)),
        sa.Column('receiving_contact_phone', sa.String(50)),
        sa.Column('geofence_radius_miles', sa.Double(), server_default='0.5'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
        sa.UniqueConstraint('site_id', 'config_id', name='uq_facility_config_site_config'),
    )
    op.create_index('idx_facility_config_tenant', 'facility_config', ['tenant_id', 'facility_type'])

    # ---- operating_schedule ----
    op.create_table(
        'operating_schedule',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('facility_config_id', sa.Integer(), sa.ForeignKey('facility_config.id', ondelete='CASCADE'), nullable=False),
        sa.Column('day_of_week', sa.Integer(), nullable=False, comment='0=Monday, 6=Sunday'),
        sa.Column('is_open', sa.Boolean(), server_default='true'),
        sa.Column('open_time', sa.String(5), comment='HH:MM format, e.g. 06:00'),
        sa.Column('close_time', sa.String(5), comment='HH:MM format, e.g. 22:00'),
        sa.Column('open_time_2', sa.String(5)),
        sa.Column('close_time_2', sa.String(5)),
        sa.Column('override_date', sa.Date(),
                  comment='If set, this record overrides the day_of_week for this specific date'),
        sa.Column('override_reason', sa.String(200)),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_op_schedule_facility', 'operating_schedule', ['facility_config_id', 'day_of_week'])

    # ---- yard_location ----
    op.create_table(
        'yard_location',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('facility_config_id', sa.Integer(), sa.ForeignKey('facility_config.id', ondelete='CASCADE'), nullable=False),
        sa.Column('spot_number', sa.String(20), nullable=False),
        sa.Column('zone', sa.String(50), comment='INBOUND, OUTBOUND, STAGING, REEFER, HAZMAT, OVERFLOW'),
        sa.Column('has_reefer_plug', sa.Boolean(), server_default='false'),
        sa.Column('is_hazmat_approved', sa.Boolean(), server_default='false'),
        sa.Column('max_equipment_length_ft', sa.Double(), server_default='53'),
        sa.Column('status', sa.String(20), server_default='EMPTY',
                  comment='EMPTY, OCCUPIED, RESERVED, MAINTENANCE'),
        sa.Column('current_equipment_id', sa.Integer(), sa.ForeignKey('equipment.id')),
        sa.Column('occupied_since', sa.DateTime()),
        sa.Column('expected_departure', sa.DateTime()),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
        sa.UniqueConstraint('facility_config_id', 'spot_number', name='uq_yard_spot_facility'),
    )
    op.create_index('idx_yard_status', 'yard_location', ['facility_config_id', 'status'])

    # ---- lane_profile ----
    op.create_table(
        'lane_profile',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('lane_id', sa.Integer(), sa.ForeignKey('transportation_lane.id', ondelete='CASCADE'), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('primary_mode', sa.String(20), nullable=False, comment='FTL, LTL, INTERMODAL, OCEAN, AIR, RAIL'),
        sa.Column('alternate_modes', postgresql.JSON(astext_type=sa.Text()),
                  comment='["LTL", "INTERMODAL"] — fallback modes'),
        sa.Column('direction', postgresql.ENUM(
            'OUTBOUND', 'INBOUND', 'INTER_FACILITY', 'RETURN',
            name='lane_direction_enum', create_type=False)),
        sa.Column('distance_miles', sa.Double()),
        sa.Column('drive_time_hours', sa.Double()),
        sa.Column('origin_region', sa.String(100), comment='Geographic region code for origin'),
        sa.Column('destination_region', sa.String(100), comment='Geographic region code for destination'),
        sa.Column('crosses_border', sa.Boolean(), server_default='false'),
        sa.Column('border_crossing_point', sa.String(200)),
        sa.Column('avg_transit_days', sa.Double()),
        sa.Column('p10_transit_days', sa.Double(), comment='10th percentile — best case'),
        sa.Column('p50_transit_days', sa.Double(), comment='Median transit time'),
        sa.Column('p90_transit_days', sa.Double(), comment='90th percentile — worst case'),
        sa.Column('transit_time_dist', postgresql.JSON(astext_type=sa.Text()),
                  comment='Stochastic: {"type": "lognormal", "mean": 3.2, "stddev": 0.5}'),
        sa.Column('avg_weekly_volume', sa.Integer(), comment='Average loads per week'),
        sa.Column('peak_weekly_volume', sa.Integer()),
        sa.Column('seasonality_pattern', postgresql.JSON(astext_type=sa.Text()),
                  comment='Monthly indices: [1.0, 0.9, 1.1, ...] for 12 months'),
        sa.Column('avg_cost_per_mile', sa.Double()),
        sa.Column('benchmark_rate', sa.Double(), comment='Market benchmark rate for this lane'),
        sa.Column('benchmark_source', sa.String(50), comment='DAT, SONAR, GREENSCREENS'),
        sa.Column('benchmark_date', sa.Date()),
        sa.Column('disruption_frequency', sa.Double(), comment='Disruptions per 100 shipments'),
        sa.Column('weather_risk_score', sa.Double(), comment='0-1 weather disruption risk'),
        sa.Column('congestion_risk_score', sa.Double(), comment='0-1 congestion/delay risk'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
        sa.UniqueConstraint('lane_id', 'config_id', name='uq_lane_profile_lane_config'),
    )
    op.create_index('idx_lane_profile_mode', 'lane_profile', ['tenant_id', 'primary_mode'])
    op.create_index('idx_lane_profile_volume', 'lane_profile', ['tenant_id', 'avg_weekly_volume'])

    # ---- carrier_contract ----
    op.create_table(
        'carrier_contract',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('carrier_id', sa.Integer(), sa.ForeignKey('carrier.id', ondelete='CASCADE'), nullable=False),
        sa.Column('contract_number', sa.String(100), nullable=False),
        sa.Column('description', sa.String(500)),
        sa.Column('status', postgresql.ENUM(
            'DRAFT', 'ACTIVE', 'EXPIRED', 'TERMINATED', 'RENEWED',
            name='contract_status_enum', create_type=False), nullable=False, server_default='DRAFT'),
        sa.Column('effective_date', sa.Date(), nullable=False),
        sa.Column('expiration_date', sa.Date(), nullable=False),
        sa.Column('signed_date', sa.Date()),
        sa.Column('notice_period_days', sa.Integer(), server_default='30',
                  comment='Days notice required for termination'),
        sa.Column('auto_renew', sa.Boolean(), server_default='false'),
        sa.Column('min_annual_volume', sa.Integer(), comment='Minimum loads per year'),
        sa.Column('max_annual_volume', sa.Integer()),
        sa.Column('volume_ytd', sa.Integer(), server_default='0',
                  comment='Year-to-date loads, updated periodically'),
        sa.Column('payment_terms_days', sa.Integer(), server_default='30', comment='Net 30, Net 45, etc.'),
        sa.Column('currency', sa.String(3), server_default='USD'),
        sa.Column('fuel_surcharge_method', sa.String(50), comment='DOE_INDEX, EIA_TABLE, FLAT_PCT, INCLUDED'),
        sa.Column('fuel_base_price', sa.Double(), comment='Base fuel price for surcharge calculation'),
        sa.Column('sla_on_time_pickup_pct', sa.Double(), comment='Committed on-time pickup %'),
        sa.Column('sla_on_time_delivery_pct', sa.Double(), comment='Committed on-time delivery %'),
        sa.Column('sla_damage_rate_pct', sa.Double(), comment='Max acceptable damage rate %'),
        sa.Column('sla_tracking_compliance_pct', sa.Double(), comment='Required tracking update %'),
        sa.Column('penalty_clauses', postgresql.JSON(astext_type=sa.Text()),
                  comment='[{"metric": "otd", "threshold": 95, "penalty_per_pct": 500}]'),
        sa.Column('lane_scope', sa.String(20), server_default='SPECIFIED',
                  comment='SPECIFIED, ALL_LANES, REGION'),
        sa.Column('covered_regions', postgresql.JSON(astext_type=sa.Text()),
                  comment='["US_SOUTHEAST", "US_MIDWEST"]'),
        sa.Column('covered_modes', postgresql.JSON(astext_type=sa.Text()), comment='["FTL", "LTL"]'),
        sa.Column('contract_document_url', sa.String(500)),
        sa.Column('notes', sa.Text()),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
        sa.UniqueConstraint('tenant_id', 'contract_number', name='uq_contract_tenant_number'),
    )
    op.create_index('idx_contract_carrier', 'carrier_contract', ['carrier_id', 'status'])
    op.create_index('idx_contract_dates', 'carrier_contract', ['effective_date', 'expiration_date'])

    # ------------------------------------------------------------------
    # 5. Tables from tms_planning.py
    # ------------------------------------------------------------------

    # ---- shipping_forecast ----
    op.create_table(
        'shipping_forecast',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_version', sa.String(20), nullable=False, server_default='live',
                  comment='live, tms_baseline, decision_action'),
        sa.Column('lane_id', sa.Integer(), sa.ForeignKey('transportation_lane.id')),
        sa.Column('origin_site_id', sa.Integer(), sa.ForeignKey('site.id')),
        sa.Column('destination_site_id', sa.Integer(), sa.ForeignKey('site.id')),
        sa.Column('mode', sa.String(20), comment='FTL, LTL, INTERMODAL, OCEAN, AIR'),
        sa.Column('commodity_id', sa.Integer(), sa.ForeignKey('commodity.id')),
        sa.Column('forecast_date', sa.Date(), nullable=False, comment='Start of the forecast period'),
        sa.Column('period_type', sa.String(10), server_default='WEEK', comment='DAY, WEEK, MONTH'),
        sa.Column('forecast_loads', sa.Double(), comment='Predicted number of loads'),
        sa.Column('forecast_weight', sa.Double(), comment='Predicted total weight'),
        sa.Column('forecast_volume', sa.Double(), comment='Predicted total volume (cuft)'),
        sa.Column('forecast_pallets', sa.Double(), comment='Predicted total pallets'),
        sa.Column('forecast_loads_p10', sa.Double(), comment='10th percentile — low scenario'),
        sa.Column('forecast_loads_p50', sa.Double(), comment='50th percentile — most likely'),
        sa.Column('forecast_loads_p90', sa.Double(), comment='90th percentile — high scenario'),
        sa.Column('forecast_method', postgresql.ENUM(
            'STATISTICAL', 'ML', 'CONSENSUS', 'EXTERNAL', 'CONFORMAL',
            name='forecast_method_enum', create_type=False)),
        sa.Column('model_id', sa.String(100), comment='ML model version that generated this'),
        sa.Column('mape', sa.Double(), comment='Mean Absolute Percentage Error for this forecast'),
        sa.Column('confidence_score', sa.Double(), comment='0-1 model confidence'),
        sa.Column('manual_adjustment', sa.Double(), server_default='0', comment='User override delta (loads)'),
        sa.Column('adjustment_reason', sa.String(500)),
        sa.Column('adjusted_by_user_id', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('source', sa.String(100), comment='AGENT, TMS, ERP, MANUAL'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
    )
    op.create_index('idx_forecast_lookup', 'shipping_forecast',
                    ['config_id', 'plan_version', 'forecast_date', 'lane_id'])
    op.create_index('idx_forecast_tenant_period', 'shipping_forecast',
                    ['tenant_id', 'forecast_date', 'period_type'])
    op.create_index('idx_forecast_lane_mode', 'shipping_forecast',
                    ['lane_id', 'mode', 'forecast_date'])

    # ---- capacity_target ----
    op.create_table(
        'capacity_target',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_version', sa.String(20), nullable=False, server_default='live'),
        sa.Column('lane_id', sa.Integer(), sa.ForeignKey('transportation_lane.id')),
        sa.Column('origin_site_id', sa.Integer(), sa.ForeignKey('site.id')),
        sa.Column('destination_site_id', sa.Integer(), sa.ForeignKey('site.id')),
        sa.Column('mode', sa.String(20)),
        sa.Column('carrier_id', sa.Integer(), sa.ForeignKey('carrier.id')),
        sa.Column('target_date', sa.Date(), nullable=False),
        sa.Column('period_type', sa.String(10), server_default='WEEK'),
        sa.Column('required_loads', sa.Double(), nullable=False, comment='Loads needed for this period'),
        sa.Column('committed_loads', sa.Double(), server_default='0', comment='Loads committed by carriers'),
        sa.Column('available_loads', sa.Double(), server_default='0', comment='Carrier capacity available'),
        sa.Column('gap_loads', sa.Double(), server_default='0', comment='Unmet capacity (required - committed)'),
        sa.Column('buffer_loads', sa.Double(), server_default='0', comment='Extra capacity buffer above forecast'),
        sa.Column('buffer_policy', sa.String(20), comment='FIXED, PCT_FORECAST, CONFORMAL'),
        sa.Column('buffer_pct', sa.Double(), comment='Buffer as % of forecast'),
        sa.Column('target_cost_per_load', sa.Double()),
        sa.Column('target_total_cost', sa.Double()),
        sa.Column('budget_limit', sa.Double()),
        sa.Column('required_loads_p10', sa.Double()),
        sa.Column('required_loads_p90', sa.Double()),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
    )
    op.create_index('idx_capacity_target_lookup', 'capacity_target',
                    ['config_id', 'plan_version', 'target_date', 'lane_id'])
    op.create_index('idx_capacity_target_gap', 'capacity_target', ['tenant_id', 'gap_loads'])

    # ---- transportation_plan ----
    op.create_table(
        'transportation_plan',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_version', sa.String(20), nullable=False, server_default='live'),
        sa.Column('plan_name', sa.String(200)),
        sa.Column('status', postgresql.ENUM(
            'DRAFT', 'OPTIMIZING', 'READY', 'APPROVED', 'EXECUTING',
            'COMPLETED', 'SUPERSEDED',
            name='plan_status_enum', create_type=False), nullable=False, server_default='DRAFT'),
        sa.Column('plan_start_date', sa.Date(), nullable=False),
        sa.Column('plan_end_date', sa.Date(), nullable=False),
        sa.Column('planning_horizon_days', sa.Integer()),
        sa.Column('total_planned_loads', sa.Integer(), server_default='0'),
        sa.Column('total_planned_shipments', sa.Integer(), server_default='0'),
        sa.Column('total_estimated_cost', sa.Double(), server_default='0'),
        sa.Column('total_estimated_miles', sa.Double(), server_default='0'),
        sa.Column('avg_cost_per_mile', sa.Double()),
        sa.Column('avg_utilization_pct', sa.Double(), comment='Average load utilization %'),
        sa.Column('carrier_count', sa.Integer(), comment='Number of distinct carriers assigned'),
        sa.Column('optimization_method', sa.String(50), comment='AGENT, MANUAL, HYBRID'),
        sa.Column('optimization_score', sa.Double(), comment='Overall plan quality 0-1'),
        sa.Column('optimization_duration_sec', sa.Double(), comment='Time to generate plan'),
        sa.Column('optimization_metadata', postgresql.JSON(astext_type=sa.Text())),
        sa.Column('cost_vs_baseline_pct', sa.Double(), comment='% cost change vs tms_baseline'),
        sa.Column('service_vs_baseline_pct', sa.Double(), comment='% OTD change vs tms_baseline'),
        sa.Column('approved_by_user_id', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('approved_at', sa.DateTime()),
        sa.Column('generated_by', sa.String(20), comment='AGENT, USER, SCHEDULED'),
        sa.Column('cascade_run_id', sa.String(100), comment='Planning cascade execution ID'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
    )
    op.create_index('idx_transport_plan_lookup', 'transportation_plan',
                    ['config_id', 'plan_version', 'status'])
    op.create_index('idx_transport_plan_dates', 'transportation_plan',
                    ['tenant_id', 'plan_start_date', 'plan_end_date'])

    # ---- transportation_plan_item ----
    op.create_table(
        'transportation_plan_item',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('plan_id', sa.Integer(), sa.ForeignKey('transportation_plan.id', ondelete='CASCADE'), nullable=False),
        sa.Column('origin_site_id', sa.Integer(), sa.ForeignKey('site.id'), nullable=False),
        sa.Column('destination_site_id', sa.Integer(), sa.ForeignKey('site.id'), nullable=False),
        sa.Column('lane_id', sa.Integer(), sa.ForeignKey('transportation_lane.id')),
        sa.Column('mode', sa.String(20), nullable=False),
        sa.Column('equipment_type', sa.String(30)),
        sa.Column('carrier_id', sa.Integer(), sa.ForeignKey('carrier.id')),
        sa.Column('rate_id', sa.Integer(), sa.ForeignKey('freight_rate.id')),
        sa.Column('status', postgresql.ENUM(
            'PLANNED', 'CARRIER_ASSIGNED', 'TENDERED', 'CONFIRMED',
            'IN_EXECUTION', 'COMPLETED', 'CANCELLED',
            name='plan_item_status_enum', create_type=False), nullable=False, server_default='PLANNED'),
        sa.Column('planned_pickup_date', sa.DateTime(), nullable=False),
        sa.Column('planned_delivery_date', sa.DateTime(), nullable=False),
        sa.Column('shipment_count', sa.Integer(), server_default='1'),
        sa.Column('total_weight', sa.Double()),
        sa.Column('total_volume', sa.Double()),
        sa.Column('total_pallets', sa.Integer()),
        sa.Column('utilization_pct', sa.Double()),
        sa.Column('estimated_cost', sa.Double()),
        sa.Column('estimated_cost_per_mile', sa.Double()),
        sa.Column('distance_miles', sa.Double()),
        sa.Column('stops', postgresql.JSON(astext_type=sa.Text()),
                  comment='[{"site_id": 1, "type": "PICKUP"}, {"site_id": 2, "type": "DELIVERY"}]'),
        sa.Column('is_multi_stop', sa.Boolean(), server_default='false'),
        sa.Column('load_id', sa.Integer(), sa.ForeignKey('load.id')),
        sa.Column('shipment_ids', postgresql.JSON(astext_type=sa.Text()),
                  comment='List of shipment IDs consolidated into this load'),
        sa.Column('agent_decision_id', sa.String(100), comment='FK to powell_decisions'),
        sa.Column('selection_rationale', postgresql.JSON(astext_type=sa.Text()),
                  comment='{"carrier_score": 0.92, "cost_rank": 1, ...}'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime()),
    )
    op.create_index('idx_plan_item_plan', 'transportation_plan_item', ['plan_id', 'status'])
    op.create_index('idx_plan_item_dates', 'transportation_plan_item',
                    ['planned_pickup_date', 'planned_delivery_date'])
    op.create_index('idx_plan_item_carrier', 'transportation_plan_item', ['carrier_id', 'status'])
    op.create_index('idx_plan_item_lane', 'transportation_plan_item', ['lane_id', 'mode'])


def downgrade():
    """Drop all TMS tables in reverse dependency order, then remove enum types."""

    # ------------------------------------------------------------------
    # Drop tables in reverse dependency order
    # ------------------------------------------------------------------

    # tms_planning children first
    op.drop_table('transportation_plan_item')
    op.drop_table('transportation_plan')
    op.drop_table('capacity_target')
    op.drop_table('shipping_forecast')

    # transportation_config children
    op.drop_table('carrier_contract')
    op.drop_table('lane_profile')
    op.drop_table('yard_location')
    op.drop_table('operating_schedule')
    op.drop_table('facility_config')

    # tms_entities — documents, identifiers, tracking
    op.drop_table('shipment_identifier')
    op.drop_table('tracking_event')
    op.drop_table('proof_of_delivery')
    op.drop_table('bill_of_lading')

    # Exceptions
    op.drop_table('exception_resolution')
    op.drop_table('shipment_exception')

    # Appointments & Dock
    op.drop_table('appointment')
    op.drop_table('dock_door')

    # Freight
    op.drop_table('freight_tender')
    op.drop_table('freight_rate')

    # Load items
    op.drop_table('load_item')
    op.drop_table('shipment_leg')

    # Load (before shipment FK columns removed)
    op.drop_table('load')

    # Carrier children
    op.drop_table('carrier_scorecard')
    op.drop_table('equipment')
    op.drop_table('carrier_lane')

    # Carrier
    op.drop_table('carrier')

    # Commodity
    op.drop_table('commodity')
    op.drop_table('commodity_hierarchy')

    # ------------------------------------------------------------------
    # Drop tms_shipment table
    # ------------------------------------------------------------------
    for idx_name in [
        'idx_tms_shipment_tenant_status', 'idx_tms_shipment_dates',
        'idx_tms_shipment_carrier', 'idx_tms_shipment_lane',
        'idx_tms_shipment_origin_dest',
    ]:
        try:
            op.drop_index(idx_name, table_name='tms_shipment')
        except Exception:
            pass
    op.drop_table('tms_shipment')

    # ------------------------------------------------------------------
    # Drop enum types
    # ------------------------------------------------------------------
    bind = op.get_bind()
    enum_names = [
        'plan_item_status_enum', 'plan_status_enum', 'forecast_method_enum',
        'lane_direction_enum', 'contract_status_enum', 'facility_type_enum',
        'tracking_event_type_enum', 'rate_type_enum',
        'appointment_status_enum', 'appointment_type_enum',
        'tender_status_enum', 'carrier_type_enum',
        'exception_resolution_status_enum', 'exception_severity_enum',
        'exception_type_enum', 'load_status_enum', 'shipment_status_enum',
        'equipment_type_enum', 'transport_mode_enum',
    ]
    for name in enum_names:
        sa.Enum(name=name).drop(bind, checkfirst=True)
