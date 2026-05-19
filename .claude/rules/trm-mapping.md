# TRM mapping — SC Planning → TMS

The Powell framework and agent architecture are shared with SCP. The 10
TRM agent slots map to transportation equivalents.

Shipping-volume sensing / lane-volume forecasting moved to DP-Ship per
§3.79 Substep 3 (2026-05-19); the canonical lane volume forecast is now
produced upstream by DP-Ship's `LoadVolumeSensingTRM` and consumed by
TMS L4 (movement plan + balancer) via the shared `lane_volume_plan`
table. TMS no longer owns a SENSE-phase volume-forecast TRM.

| SC TRM | TMS TRM | Phase | Function |
|---|---|---|---|
| ATPExecutorTRM | **CapacityPromiseTRM** | SENSE | Available capacity to promise on lane / carrier |
| OrderTrackingTRM | **ShipmentTrackingTRM** | SENSE | In-transit visibility, ETA prediction, exceptions |
| InventoryBufferTRM | **CapacityBufferTRM** | ASSESS | Reserve carrier capacity, surge planning |
| QualityDispositionTRM | **ExceptionManagementTRM** | ASSESS | Delay, damage, refusal, rolled-container resolution |
| POCreationTRM | **FreightProcurementTRM** | ACQUIRE | Carrier waterfall tendering, rate optimisation |
| SubcontractingTRM | **BrokerRoutingTRM** | ACQUIRE | Broker vs asset-carrier decision, overflow routing |
| MaintenanceSchedulingTRM | **DockSchedulingTRM** | PROTECT | Appointment scheduling, dock-door optimisation |
| MOExecutionTRM | **LoadBuildTRM** | BUILD | Load consolidation, optimisation, sequencing |
| TOExecutionTRM | **IntermodalTransferTRM** | BUILD | Cross-mode transfers, drayage coordination |
| InventoryRebalancingTRM | **EquipmentRepositionTRM** | REFLECT | Empty container / trailer repositioning |

## Implementation status

Capability declarations, hive signals (50+ TMS-specific signal types),
site capability mapping (6 facility types), and heuristic library (10
dispatch-side TRMs with deterministic fallback rules) are complete.
Files:

- [services/powell/tms_agent_capabilities.py](../../services/powell/tms_agent_capabilities.py) — 10 TRM declarations with signal reads / emits
- [services/powell/tms_hive_signals.py](../../services/powell/tms_hive_signals.py) — 50+ TMS signal types (carrier, tracking, dock, load, equipment, intermodal, network)
- [services/powell/tms_site_capabilities.py](../../services/powell/tms_site_capabilities.py) — facility type → active TRM mapping (shipper, terminal, cross_dock, consignee, carrier_yard, port)
- [services/powell/tms_heuristic_library/](../../services/powell/tms_heuristic_library/) — 10 state dataclasses + dispatch with industry-standard rules

## TMS-specific GNN layers

- **S&OP GraphSAGE** — network-wide lane optimisation, carrier portfolio balance, mode mix
- **Execution tGNN** — daily load assignments, carrier allocations, priority routing
- **Site tGNN** — intra-facility cross-TRM coordination (dock, yard, staging)

## TMS-specific agent scenarios

- **Freight Tender Scenario** — carrier bidding simulation (shipper vs carrier agents)
- **Network Disruption Scenario** — port strike, weather event, capacity crunch response
- **Mode Selection Scenario** — intermodal vs direct routing optimisation
