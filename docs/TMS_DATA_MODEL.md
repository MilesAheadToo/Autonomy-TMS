# TMS Data Model Reference

**Last verified:** 2026-04-11
**Lives in the TMS repo by design** — this is the TMS-specific view of which entities come from the shared canonical schema and which are TMS-only extensions. The SCP repo has its own counterpart (`SCP_DATA_MODEL.md`).
**Sister docs (in the Autonomy-Core repo, which is the canonical home for shared architecture docs):**
- Data-model package: [`packages/data-model/`](https://github.com/azirella-ltd/Autonomy-Core/tree/main/packages/data-model)
- Frontend story: [`UI_CORE_SHAREABILITY_ANALYSIS.md`](https://github.com/azirella-ltd/Autonomy-Core/blob/main/docs/architecture/UI_CORE_SHAREABILITY_ANALYSIS.md)
- Component inventory: [`COMPONENT_INVENTORY.md`](https://github.com/azirella-ltd/Autonomy-Core/blob/main/COMPONENT_INVENTORY.md)

## Purpose

This is the **TMS-side reference** for which data model entities come from where:

1. Which entities come from **AWS SC DM** (the canonical foundation, used unchanged)
2. Which entities are **TMS-specific extensions** defined in `backend/app/models/tms_entities.py`, `tms_planning.py`, and `transportation_config.py`
3. How TMS entities **map to SCP entities** for cross-product MCP integration
4. How TMS entities **translate to/from EDI X12** for external carrier integration
5. Which entities are **fork residuals** that should be deleted (the SCP code carried over from the original fork)

This is documentation only — the actual canonical implementation lives in [`Autonomy-Core/packages/data-model/`](https://github.com/azirella-ltd/Autonomy-Core/tree/main/packages/data-model).

---

## 1. Foundation: AWS SC DM entities (used unchanged)

These are entities defined by the **AWS Supply Chain Data Model** that TMS uses without modification. They are master data shared across the entire Autonomy product family. After the Autonomy-Core bootstrap, they live in `Autonomy-Core/packages/data-model/src/autonomy_data_model/master/` and are imported by TMS, SCP, and any future product.

| Entity | What it represents | TMS usage |
|---|---|---|
| `Site` | A physical facility (origin, destination, terminal, plant, warehouse, port) | Every load has an origin and destination Site. Dock doors and yard locations belong to a Site. |
| `Product` | What's being moved or made (commodity, freight class, SKU, finished good) | Maps to TMS's `Commodity` extension. Products on a shipment determine freight class, dimensions, hazmat status. |
| `TradingPartner` | External party (carrier, broker, supplier, customer, consignee) | TMS extends with `Carrier` (a TradingPartner with `tpartner_type='carrier'`). All shippers and consignees are TradingPartners. |
| `TransportationLane` | Origin-destination pair, optionally with mode | The atomic unit of carrier procurement and rate negotiation. Every freight rate references a Lane. |
| `Inventory` | Quantity of product at site over time | Yard inventory (trailers, containers at a facility) reuses this entity. |
| `Forecast` | Demand prediction over time | Shipping volume forecast extends this. |
| `Tenant` | Customer organization | Standard. Every operational/learning tenant pair lives here. |
| `User` | Individual person within a tenant | Standard. Includes `decision_level` for the AIIO permission model. |
| `Role` | Capability bundle assigned to users | Standard. TMS roles (CAPACITY_PROMISE_ANALYST, etc.) are registered against this. |
| `Capability` | Atomic permission | Standard. TMS capabilities (`manage_capacity_promise_worklist`, etc.) are registered against this. |

**Note on the AWS SC DM "compliant" markers in the codebase:** The TMS frontend already has explicit comments calling out AWS SC DM compliance — see `frontend/src/services/supplyChainConfigService.js` lines 117 (`Sites CRUD (AWS SC DM: Site replaces Node)`), 144 (`Transportation Lanes CRUD (AWS SC DM standard)`), 218 (`Customers CRUD (AWS SC DM: TradingPartner with tpartner_type='customer')`). These markers should be preserved during refactoring.

---

## 2. TMS-specific extensions

These entities are defined in TMS only — they have no SCP analog. After the Autonomy-Core bootstrap, they live in `Autonomy-TMS/backend/app/models/tms/` and import their FK relationships from the shared `data-model` package.

### 2.1 Master data extensions (in `tms_entities.py`)

| Entity | What it is | Extends / references |
|---|---|---|
| `Commodity` | Freight class / commodity classification (NMFC, harmonized code) | Extends `Product` |
| `CommodityHierarchy` | Class → subclass → commodity hierarchy | Extends `Product` taxonomy |
| `Carrier` | Transportation provider with rates, lanes, capacity | Extends `TradingPartner` (`tpartner_type='carrier'`) |
| `CarrierLane` | Carrier's coverage of a specific lane | Many-to-many bridge: `Carrier` ↔ `TransportationLane` |
| `CarrierService` | Service level by carrier (expedited, economy, expedited-air, etc.) | Belongs to `Carrier` |
| `CarrierContract` | Contracted rate agreement with validity period | Belongs to `Carrier`, references `TransportationLane` |
| `CarrierScorecard` | Performance metrics over time (on-time %, damage rate, claim rate) | Belongs to `Carrier` |
| `Equipment` | Trailer, container, railcar, intermodal type | Standalone master |
| `FacilityConfig` | TMS-specific config for a Site (dock count, yard size, gates, hours) | Extends `Site` |
| `OperatingSchedule` | Weekly hours of operation for a facility | Belongs to `FacilityConfig` |
| `YardLocation` | Trailer parking spot at a facility | Belongs to `FacilityConfig` |
| `LaneProfile` | TMS-specific config for a Lane (mode mix, transit times, distance) | Extends `TransportationLane` |
| `DockDoor` | Individual loading dock at a facility | Belongs to `FacilityConfig` |

### 2.2 Operational entities (in `tms_entities.py`)

| Entity | What it is | Notes |
|---|---|---|
| `Shipment` | Unit of freight from origin to destination | The atomic movement unit. Has origin Site, destination Site, commodity, weight, dimensions, planned dates. |
| `ShipmentLeg` | Single mode segment of a multi-mode shipment | E.g., truck-rail-truck = 3 legs. Each leg has its own carrier and equipment. |
| `Load` | Physical grouping of shipments on equipment | A Load is what physically moves. One trailer = one Load. Multiple shipments can share a Load (LTL, consolidation). |
| `LoadItem` | Bridge between Load and Shipment | M:N bridge with packaging, sequence, position attributes. |
| `FreightRate` | Rate per lane/mode/carrier with validity period | Drives carrier selection and tendering. References `Carrier`, `TransportationLane`, `RateType`. |
| `FreightTender` | Carrier waterfall tender and response | TenderStatus enum: `PENDING`, `ACCEPTED`, `REJECTED`, `EXPIRED`. References `Shipment`, `Carrier`. |
| `Appointment` | Dock door scheduling (pickup/delivery windows) | References `DockDoor`, `Shipment` or `Load`. AppointmentStatus enum: `SCHEDULED`, `CONFIRMED`, `IN_PROGRESS`, `COMPLETED`, `MISSED`. |
| `BillOfLading` (BOL) | Legal shipping document | References `Shipment`. Required for interstate freight. |
| `ProofOfDelivery` (POD) | Confirmation record | References `Shipment`. Captured at delivery (signature, photo, timestamp). |
| `ShipmentException` | Delay, damage, refused, rolled, lost, etc. | ExceptionType + ExceptionSeverity enums. Drives the ExceptionManagementTRM workflow. |
| `ExceptionResolution` | How an exception was handled | Belongs to `ShipmentException`. ResolutionStatus enum. |
| `TrackingEvent` | In-transit visibility update | TrackingEventType enum (`pickup`, `arrival`, `departure`, `delivery`, `delay`, etc.). Sourced from project44 webhooks, EDI 214, or carrier APIs. |
| `ShipmentIdentifier` | External IDs (PRO number, BOL number, container ID) | Belongs to `Shipment`. Used for matching tracking events. |

### 2.3 Planning entities (in `tms_planning.py`)

These are the TMS analogs to SCP's planning entities. Same Powell decision-cycle, different domain content.

| Entity | What it is | SCP analog |
|---|---|---|
| `ShippingForecast` | Predicted shipping volume by lane/period | `DemandPlan` |
| `CapacityTarget` | Reserved carrier capacity by lane | `SafetyStock` (buffer) |
| `TransportationPlan` | Time-bounded execution plan (which loads move when, on what) | `MasterProductionSchedule` (MPS) |
| `TransportationPlanItem` | Individual load assignment within a plan | `MPSLineItem` |

### 2.4 Enums (in `tms_entities.py`, `tms_planning.py`, `transportation_config.py`)

| Enum | Values |
|---|---|
| `TransportMode` | TRUCK_FTL, TRUCK_LTL, PARCEL, OCEAN_FCL, OCEAN_LCL, BULK, AIR_STANDARD, AIR_EXPRESS, AIR_CHARTER, RAIL_CARLOAD, RAIL_INTERMODAL, RAIL_UNIT_TRAIN, INTERMODAL |
| `EquipmentType` | DRY_VAN, REEFER, FLATBED, STEPDECK, LOWBOY, CONTAINER_20, CONTAINER_40, CONTAINER_45, CONTAINER_53, RAILCAR, TANKER |
| `ShipmentStatus` | PENDING, TENDERED, ACCEPTED, IN_TRANSIT, OUT_FOR_DELIVERY, DELIVERED, EXCEPTION |
| `LoadStatus` | PLANNED, BUILT, TENDERED, ACCEPTED, IN_TRANSIT, DELIVERED, COMPLETED, EXCEPTION |
| `ExceptionType` | DELAY, DAMAGE, REFUSED, ROLLED, LOST, MISSED_PICKUP, MISSED_DELIVERY, ACCESSORIAL |
| `ExceptionSeverity` | LOW, MEDIUM, HIGH, CRITICAL |
| `ExceptionResolutionStatus` | OPEN, IN_PROGRESS, RESOLVED, ESCALATED |
| `CarrierType` | ASSET, BROKER, FORWARDER, OWNER_OPERATOR, INTERMODAL_MARKETING |
| `TenderStatus` | PENDING, ACCEPTED, REJECTED, EXPIRED, COUNTERED |
| `AppointmentType` | PICKUP, DELIVERY, RETURN |
| `AppointmentStatus` | SCHEDULED, CONFIRMED, IN_PROGRESS, COMPLETED, MISSED, CANCELLED |
| `RateType` | CONTRACT, SPOT, BENCHMARK, INDEX, ACCESSORIAL |
| `ForecastMethod` | NAIVE, MOVING_AVERAGE, EXPONENTIAL_SMOOTHING, ARIMA, LGBM, GNN, ENSEMBLE |
| `PlanStatus` | DRAFT, ACTIVE, ARCHIVED, BASELINE |
| `PlanItemStatus` | PROPOSED, ACCEPTED, OVERRIDDEN, EXECUTED, CANCELLED |
| `FacilityType` | SHIPPER, CONSIGNEE, TERMINAL, CROSS_DOCK, CARRIER_YARD, PORT |
| `ContractStatus` | DRAFT, ACTIVE, EXPIRED, TERMINATED |
| `LaneDirection` | INBOUND, OUTBOUND, INTERFACILITY |

---

## 3. Cross-product entity mapping (for MCP integration)

Some concepts exist in both SCP and TMS with similar but not identical semantics. When TMS calls SCP via MCP (or vice versa), both products need to agree on what's being exchanged. This table is the canonical mapping.

| Concept | SCP entity | TMS entity | MCP exchange | Notes |
|---|---|---|---|---|
| What moves | `Product` | `Product` (+ `Commodity` extension) | Direct | Shared via AWS SC DM. Identical type. |
| Where it moves | `Site` | `Site` (+ `FacilityConfig` extension) | Direct | Shared via AWS SC DM. TMS adds dock-level detail; SCP rarely needs it. |
| Origin-destination pair | `TransportationLane` | `TransportationLane` (+ `LaneProfile` extension) | Direct | Shared via AWS SC DM. |
| External party | `TradingPartner` (`tpartner_type='supplier'`) | `TradingPartner` (`tpartner_type='carrier'`) (+ `Carrier` extension) | Direct | Shared via AWS SC DM. Different `tpartner_type` values. |
| Tenant / user / role | `Tenant` / `User` / `Role` | Same | Direct | Shared via AWS SC DM. Identical. |
| Movement unit | `PurchaseOrder` (SCP) | `Shipment` (TMS) | Translate | Different. SCP uses PO to procure inbound material; TMS uses Shipment to move freight. MCP exchanges as serialized objects with explicit type tag. |
| Movement aggregation | `ManufacturingOrder` (SCP) | `Load` (TMS) | Translate | Different semantics. Both are "physical batch of work" but the underlying concepts differ. |
| Cross-mode transfer | `TransferOrder` (SCP) | `IntermodalTransfer` (TMS — currently lives in `tms_entities.py` as a future concept) | Translate | Both are "stuff moving between sites/modes." |
| Time-bounded plan | `MasterProductionSchedule` (SCP) | `TransportationPlan` (TMS) | Translate | Same Powell phase (BUILD), different domain content. |
| Capacity commitment | `ATPCommitment` (SCP) | `CapacityCommitment` (TMS — implied by `CapacityPromiseTRM` outputs, may not have its own table yet) | Translate | Architecturally analogous: both are "what we can commit to deliver." |
| Forecast | `DemandPlan` (SCP) | `ShippingForecast` (TMS) | Translate | Same Powell phase (SENSE), different units (units vs. shipments/weight). |

### MCP tools that depend on this mapping

**TMS calls SCP** for:
- `get_atp_constraints(product_id, site_id, date)` — `FreightProcurementTRM` calls before tendering to know what SCP expects to ship
- `get_demand_forecast(product_id, lane_id, date_range)` — `DemandSensingTRM` calls for shipping volume context
- `get_supply_plan(product_id, site_id, date)` — `LoadBuildTRM` calls to know what's expected to be ready for pickup

**SCP calls TMS** for:
- `get_carrier_capacity(lane_id, date_range)` — `POCreationTRM` calls before placing PO to know freight options
- `get_dock_availability(facility_id, time_window)` — Inventory rebalancing calls before scheduling transfers
- `get_active_exceptions(facility_id, severity_min)` — Demand sensing calls to know about disruptions affecting planned receipts

Each tool exchanges data using the **shared** entity types (Site, Product, TradingPartner, TransportationLane). Anything TMS-specific (Carrier extension, FreightRate, Tender) is **not** part of an MCP payload — it stays inside TMS.

---

## 4. EDI X12 ↔ canonical model translations

When TMS integrates with real commercial carriers, the wire format is **EDI X12** (or its modern API equivalent), not JSON. Translation happens at the integration boundary; internal storage uses the canonical TMS entities.

| EDI message | Direction | TMS internal representation |
|---|---|---|
| **204 Motor Carrier Load Tender** | Outbound (TMS → carrier) | `FreightTender` create with status `PENDING`. Maps `Shipment` + `Load` + planned pickup window to the 204 fields. |
| **990 Response to Load Tender** | Inbound (carrier → TMS) | `FreightTender` update — sets status to `ACCEPTED` or `REJECTED`. |
| **214 Transportation Carrier Shipment Status Message** | Inbound | `TrackingEvent` create. The 214 status code (e.g., `AF` = Carrier Departed Pickup) maps to `TrackingEventType`. |
| **210 Motor Carrier Freight Details and Invoice** | Inbound | Settlement record (entity TBD — currently planned, not yet implemented). |
| **211 Motor Carrier Bill of Lading** | Outbound | `BillOfLading` create. Required for interstate freight. |
| **856 Ship Notice / Manifest** (ASN) | Outbound (consignor → consignee) | `Shipment` + content list. Used to pre-notify the destination of incoming freight. |
| **858 Shipment Information** | Outbound | Used by some carriers as an alternative to 204. |
| **997 Functional Acknowledgment** | Both directions | Standard EDI ACK. Logged as `IntegrationEvent` (entity TBD). |

### Modern API equivalents

Most modern carrier integrations use REST APIs instead of raw EDI. The mapping is the same — the TMS internal representation is the same canonical model regardless of whether the wire format is EDI X12 or JSON. The translation layer lives in `backend/app/integrations/{carrier_name}/`.

**TMS already has a project44 integration** at `backend/app/integrations/project44/` that follows this pattern. project44 is the primary visibility provider; their REST API events get translated into `TrackingEvent` records.

---

## 5. Fork residual entities (delete during the Autonomy-Core bootstrap)

These entities currently exist in TMS as carry-over from the original SCP fork. They have **no TMS purpose** and should be deleted when the Autonomy-Core bootstrap happens.

### 5.1 In `backend/app/models/`

| File | Status | What to do |
|---|---|---|
| `aws_sc_planning.py.corrupted` | **Literally a corrupted file** from a prior attempt | **Delete.** No salvage value. |
| `sc_entities.py` | SCP entity definitions; sources for TMS's carried-over `Shipment` collision | **Delete after Autonomy-Core bootstrap** — TMS imports from `data-model` instead. |
| `sc_extensions.py` | SCP extensions to its entities | **Delete.** TMS doesn't need them. |
| `sc_planning.py` | SCP planning entities (MPS, MRP, etc.) | **Delete.** TMS uses `tms_planning.py` instead. |
| `aws_sc_planning.py` (if it exists, non-corrupted) | SCP planning service models | **Delete.** |

### 5.2 In `backend/app/services/`

| Directory | Status | What to do |
|---|---|---|
| `aws_sc_planning/` | SCP planning services (MRP, demand planning, ATP) | **Delete.** TMS uses `services/transportation_planning/` (to be created in Phase 4). |

### 5.3 Why these are still here

The original premise was "TMS is a fork of SCP that shares core infrastructure." That premise was abandoned on 2026-04-10 in favor of the sibling-products architecture. The fork residual files were left in place because backend Phase 4 work (TMS detachment from the SCP fork) was parked due to the model collision conflicts. The Autonomy-Core bootstrap is the right moment to delete them — once `data-model` is the canonical source of shared entities, the SCP-named files in TMS have no remaining purpose.

---

## 6. Decision-tracking entities (canonical, lives in `data-model`)

These are part of the AIIO governance model and live in shared `data-model` because both products implement AIIO identically.

| Entity | What it is |
|---|---|
| `Decision` | An agent decision (the canonical record). Fields: `id`, `decision_type`, `urgency`, `level`, `status`, `recommended_action`, `confidence`, `summary`, `effective_from`, `effective_to`, `tenant_id`, `created_at`. |
| `DecisionStatus` enum | `ACTIONED`, `INFORMED`, `INSPECTED`, `OVERRIDDEN`, `EXPIRED` |
| `DecisionUrgency` enum | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| `DecisionLevel` enum | `STRATEGIC`, `TACTICAL`, `EXECUTION` |
| `Override` | A user override with reason. References `Decision`. Fields: `decision_id`, `user_id`, `reason_code`, `reason_text`, `override_values`, `created_at`. |
| `GovernanceDecision` | Agent decision after passing through the governance pipeline. Records the AIIO mode (`AUTOMATE`, `INFORM`, `INSPECT`) the governance pipeline assigned. |
| `AuditLog` | Append-only record of every state change. References user, entity, before/after values. |

The `decision_type` field on `Decision` is a string that resolves against the `decisionTypeRegistry` on the frontend. SCP registers its 20 decision types; TMS registers its 11 decision types. The shared `Decision` table holds rows from both — they're distinguished by tenant_id (and in practice, by the deployed product since each product has its own database).

---

## 7. What lives where after the Autonomy-Core bootstrap

```
Autonomy-Core/
└── packages/data-model/src/autonomy_data_model/
    ├── master/
    │   ├── site.py            ← Site (AWS SC DM)
    │   ├── product.py         ← Product (AWS SC DM)
    │   ├── trading_partner.py ← TradingPartner (AWS SC DM)
    │   ├── lane.py            ← TransportationLane (AWS SC DM)
    │   ├── inventory.py       ← Inventory (AWS SC DM)
    │   └── forecast.py        ← Forecast (AWS SC DM)
    ├── governance/
    │   ├── decision.py        ← Decision, DecisionStatus, DecisionUrgency, DecisionLevel
    │   ├── override.py        ← Override
    │   ├── governance_decision.py
    │   └── audit_log.py
    ├── tenant/
    │   ├── tenant.py          ← Tenant
    │   ├── user.py            ← User
    │   ├── role.py            ← Role
    │   └── capability.py      ← Capability
    └── powell/
        ├── trm_base.py        ← TRM base class
        ├── hive_signal.py     ← HiveSignal base
        └── conformal.py       ← Conformal prediction framework

Autonomy-TMS/backend/app/models/tms/
    ├── master/
    │   ├── commodity.py       ← Commodity, CommodityHierarchy
    │   ├── carrier.py         ← Carrier, CarrierLane, CarrierService, CarrierContract, CarrierScorecard
    │   ├── equipment.py       ← Equipment
    │   ├── facility_config.py ← FacilityConfig, OperatingSchedule, YardLocation, DockDoor
    │   └── lane_profile.py    ← LaneProfile
    ├── operational/
    │   ├── shipment.py        ← Shipment, ShipmentLeg, ShipmentIdentifier
    │   ├── load.py            ← Load, LoadItem
    │   ├── freight_rate.py    ← FreightRate
    │   ├── tender.py          ← FreightTender
    │   ├── appointment.py     ← Appointment
    │   ├── bol.py             ← BillOfLading, ProofOfDelivery
    │   ├── exception.py       ← ShipmentException, ExceptionResolution
    │   └── tracking.py        ← TrackingEvent, TrackingEventType
    └── planning/
        ├── shipping_forecast.py ← ShippingForecast
        ├── capacity_target.py   ← CapacityTarget
        └── transportation_plan.py ← TransportationPlan, TransportationPlanItem
```

The split is clean: shared concepts in `data-model`, TMS-specific concepts in `Autonomy-TMS/backend/app/models/tms/` organized by purpose (master / operational / planning). Same pattern for `Autonomy-SCP/backend/app/models/scp/`.

---

## 8. Open questions for review

1. **`IntermodalTransfer` entity** — Currently the `IntermodalTransferTRM` exists but I'm not sure there's a dedicated table. Is this an existing entity I missed, or a planned one? **Action:** verify in the next backend session.
2. **`CapacityCommitment` entity** — Same question. The `CapacityPromiseTRM` outputs commitments; do they have their own table or are they implied by other state? **Action:** verify.
3. **Settlement / Invoice / 210 entity** — There's no explicit `FreightInvoice` table that I found. Carriers send EDI 210 invoices that need to be matched against shipments. **Action:** decide if this is in scope and add an entity if so.
4. **`IntegrationEvent` for 997 ACKs** — Generic integration event log. May exist as a project44 integration concern; should be canonical for all carriers. **Action:** verify and decide on canonical placement.
5. **Tenant-mode separation** — TMS_INDEPENDENCE_PLAN says every customer gets two tenants (`PRODUCTION` and `LEARNING`). The tenant model in `data-model` needs to support this. Confirm `Tenant` has a `mode` enum.
6. **AWS SC DM upgrades** — When AWS publishes a new version of SC DM, who reviews it and merges into `data-model`? **Recommendation:** an "AWS SC DM watch" item on the `Autonomy-Core` README.
7. **Pydantic schemas** — Should `data-model` ship pydantic schemas alongside SQLAlchemy entities, or just the entities? **Recommendation:** ship both. Apps can use the pydantic schemas directly for API serialization or wrap them.

---

**End of reference.** Verified against `tms_entities.py`, `tms_planning.py`, `transportation_config.py` on 2026-04-11.
