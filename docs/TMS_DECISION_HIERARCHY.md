# TMS Decision Hierarchy — Strategic / Tactical / Operational / Execution

**Status:** Draft v1, 2026-04-23. Canonical enumeration of transport-plane
decisions by planning layer. Grounded in transport-domain concerns, not
SCP analogs.
**Supersedes:** ad-hoc references across `TACTICAL_PLANNING_REARCHITECTURE.md`,
`TMS_TIER3_FIRST_PLAN.md`, and `internal/AGENT_HIERARCHY_DIAGRAMS.md`.
**Sister docs:** Autonomy-Core `AGENT_ARCHITECTURE.md` (cross-product
treatment); SCP `docs/internal/architecture/PLANNING_HIERARCHY_AND_MRP.md`
(SCP-side hierarchy).

---

## 1. Why this doc exists

The TMS backend inherited a 5-layer agent architecture from SCP
(L4 S&OP → L3 Tactical tGNNs → L2 Site Coordinator → L1 11 TRMs →
Cross-Authority Protocol). Today only L1 is actually built for TMS
(the 11 canonical TMS TRMs shipped 2026-04-21 through 2026-04-23).
L2-L4 exist as SCP-forked code still carrying SCP-domain semantics
(inventory rebalancing, MRP, MPS, etc.), pending MIGRATION_REGISTER
item 1.13.

This doc enumerates the decisions TMS *should* make at each layer —
**from the transport domain**, not by analogy — so that future L2/L3/L4
builds can be scoped against a real target rather than a rewrite of
the SCP code.

Three things every layer entry includes:

- **What decision** (one line)
- **Trigger + cadence** (when does the agent fire)
- **Inputs & outputs** (data contract)

And three things we explicitly *don't* put here:

- SKU-level decisions (inventory sizing, demand forecasting per product,
  safety stock policy, quality disposition, subcontracting). These are
  SCP / QMS / supply-planning concerns, not transport.
- Plant-floor decisions (MO execution, production sequencing, RCCP
  adjustment, maintenance scheduling for production equipment). These
  are SCP / MES / CMMS.
- Customer-order tracking (PO exception detection on outbound orders).
  TMS tracks **shipments** and **loads**, not customer sales orders
  (SCP's concern).

---

## 2. Overview — decision density per layer

| Layer | Horizon | Cadence | Agent shape (target) | # distinct decisions (rough) |
|---|---|---|---|---|
| **L4 Strategic** | 12-18 mo | Monthly (+ trigger-based re-run on major disruption) | S&OP GraphSAGE (CFA) — ~500K params | ~12 |
| **L3 Tactical** | 26-52 wk | Weekly (batch) + daily (re-solve on drift) | Three named planners: Demand Potential (analytical/LightGBM), Unconstrained Movement (GraphSAGE), Constrained Balanced (GraphSAGE + LP feasibility repair) | ~10 |
| **L2 Operational** | 1-4 wk | Hourly, always-on | Terminal Coordinator (GATv2 + GRU per hub/yard) — ~25K params | ~6 |
| **L1 Execution** | Current shift | Real-time / <10ms | 11 TRMs (MLPs, BC + RL trained) | 11 (one per TRM) |
| **Cross-Authority (AAP)** | Ad hoc | Event-driven | Protocol, not an agent | 3-5 boundary decisions |
| **Capacity Gap Analyzer** | 13-52 wk rolling | Daily advisory | Advisory-only agent (no re-plan authority) | 4 gap categories → envelope proposals |

---

## 3. L4 Strategic — policy envelope (12-18 month horizon)

Strategic decisions shape the **network envelope** within which L3 plans
and L1 executes. They're slow, infrequent, and usually
human-in-the-loop — the agent's job is to **propose** (with reasoning
+ scenario analysis) and **inform**, not to autonomously change
structural commitments.

Output of L4 is the **policy parameter vector θ** consumed by L3.

### 3.1 Network design

| Decision | Trigger | Inputs | Outputs |
|---|---|---|---|
| **Open / close terminals** | Annual network review; major customer add/drop; M&A | Geographic demand centroid shift, inbound/outbound volume trends, rent / dock-slot cost per region, service-level gap at current network | Proposal: site to open/close + expected capacity impact; written to `network_change_proposals` for exec review |
| **Add / drop lanes** | Quarterly; persistent capacity gap from L3 Capacity Gap Analyzer | Lane volume trend (13-26 wk), carrier availability on lane, profitability per lane, customer SLA commitments | Proposal: lane pair to add/retire + projected utilization |
| **Hub-and-spoke vs direct-ship topology** | Annual | Shipment count per O-D pair, LTL vs FTL mix, dwell-time distributions | Topology recommendation per region |

### 3.2 Carrier & fleet composition

| Decision | Trigger | Inputs | Outputs |
|---|---|---|---|
| **Carrier-contract portfolio** | Contract renewal cycle; OTRI trend; spot-market tightness | Carrier OTD history, tender-reject rate, rate-card benchmarks, commit vs spot cost delta | Portfolio: per-carrier commit volume per lane per period |
| **Asset-based vs 3PL mix** | Annual fleet planning | Utilization per fleet type, per-mile cost curves, driver-retention rate, CapEx schedule | Fleet-size target per asset type per region |
| **Equipment-pool composition** | Annual + shock events | Equipment-type demand by lane, repositioning cost history, commodity mix (dry van vs reefer vs flatbed), seasonality | Target equipment count per type per pool |

### 3.3 Service-level strategy

| Decision | Trigger | Inputs | Outputs |
|---|---|---|---|
| **Customer service-level tiers** | Annual or customer-add | Customer revenue, strategic value, competitor pricing, tier cost-to-serve | Tier assignments (Platinum P99 / Gold P95 / Silver P90 / Economy P80) with SLA commitments per customer |
| **Mode-mix strategy** | Annual | Mode cost per lane, CO2 per mode, customer mode tolerance, rail ramp availability | Target % mix per mode per corridor |

### 3.4 BSC weights + sustainability

| Decision | Trigger | Inputs | Outputs |
|---|---|---|---|
| **BSC weight rebalancing** | Quarterly | Actual KPI outcomes vs targets (Financial / Customer / Internal / Learning), exec direction | Weight vector written to `bsc_config`; consumed by L3 constrained balancer |
| **CO2-per-load-mile target** | Annual regulatory / strategic | Current emissions, mode mix, customer-sustainability requirements, carbon-pricing forecasts | Target ceiling fed into L3 as a constraint |

**L4 canonical decision types** (write into `agent_decisions`):
`NETWORK_DESIGN`, `CARRIER_CONTRACT_PORTFOLIO`, `FLEET_COMPOSITION`, `SERVICE_LEVEL_TIER`, `MODE_MIX_STRATEGY`, `BSC_WEIGHTS`, `SUSTAINABILITY_TARGET`.

---

## 4. L3 Tactical — the three-plan pipeline (26-52 week horizon)

Per `TACTICAL_PLANNING_REARCHITECTURE.md`, tactical is a **pipeline**
of three named plans, not four agents negotiating. Each produces a
distinct `plan_version` in `transportation_plan`.

### 4.1 Demand Potential (unconstrained demand)

| Aspect | |
|---|---|
| **Decision** | What *could* we ship, per lane × mode × service-class × period, if carrier capacity were unlimited? |
| **Agent** | Analytical: LightGBM + Trigg tracking signal (not a GNN — the signal is clean enough for gradient-boosting; consistent with DemandSensingTRM's short-horizon signal at L1) |
| **Trigger** | Scheduled daily at 05:00; re-run on `forecast_exception_rule` trigger |
| **Inputs** | Historical shipment volumes by lane × mode, customer tender forecasts, seasonal index, order pipeline (trailing 24h × 7d), external signals (Slack / email / market intel routed through edge ingestion), customer-SLA tier |
| **Outputs** | `shipping_forecast` rows with `plan_version='unconstrained_reference'`, P10/P50/P90 conformal bands, forecast_mape |

### 4.2 Unconstrained Movement Plan (ideal movement)

| Aspect | |
|---|---|
| **Decision** | What's the *ideal* load-build + mode split + routing, given the Demand Potential, if carrier capacity / HOS / dock / equipment were all unlimited? |
| **Agent** | Movement Planner GraphSAGE (analog of SCP's supply_planning_tgnn but with transport semantics: nodes = lanes + hubs, edges = mode alternatives, features = rate-card + distance + transit time) |
| **Trigger** | Scheduled daily at 05:30, after Demand Potential publishes |
| **Inputs** | Demand Potential output, rate-card (no commit-volume constraint), mode availability catalog, lane distance + transit-time distributions |
| **Outputs** | `transportation_plan` rows with `plan_version='unconstrained_reference'` — ideal load-build decisions per lane-period without capacity enforcement |

### 4.3 Constrained Balanced Plan (committed movement)

| Aspect | |
|---|---|
| **Decision** | What *will* we do, given the real constraint envelope (carrier capacity commitments, HOS, dock slots, equipment pool) and BSC trade-off? |
| **Agent** | Integrated Balancer — GraphSAGE with feasibility-repair via LP projection on hard-constraint violations (HOS, dock, contract minimums) |
| **Trigger** | Scheduled daily at 06:00; event-triggered re-solve when tender-reject rate > 15% (L2 escalation) or when L4 θ updates |
| **Inputs** | Unconstrained Movement Plan (from 4.2), Demand Potential (from 4.1), carrier contracts with commit volumes, equipment pool state, HOS calendar per carrier, dock appointment capacity, BSC weights from L4 θ, service-level tiers |
| **Outputs** | `transportation_plan` rows with `plan_version='constrained_live'` — committed load-build + carrier assignment + mode + routing + dock slots. This is the plan-of-record that L1 TRMs consume. |

### 4.4 Capacity Gap Analyzer (TMS's RCCP analog)

| Aspect | |
|---|---|
| **Decision** | Where are the persistent bottlenecks, and what envelope changes should L4 consider? |
| **Agent** | Advisory-only (no re-plan authority). Reads all three plans + historical outcomes. |
| **Trigger** | Scheduled daily; proposals aggregated weekly for exec review |
| **Inputs** | All three plan versions, historical tender-reject rate, equipment-imbalance history, dock-congestion history, mode-optimal-vs-available gap |
| **Outputs** | `strategic_proposal` rows — carrier-contract renegotiation suggestions, new lane RFPs, dock-expansion proposals, fleet-size adjustments, mode-mix rebalance proposals |

### 4.5 Other L3 decisions

| Decision | Trigger | Inputs | Outputs |
|---|---|---|---|
| **Carrier tender-acceptance commitments** | Weekly | 13-wk rolling lane volume forecast, carrier-scorecard OTRI, contract terms | Committed weekly tender volume per carrier per lane |
| **Dock appointment capacity planning** | Weekly per site | Arrival forecast by hour + day-of-week, dock-door count, labor schedule | Appointment-slot inventory per site per shift |
| **Fleet deployment plan** | Weekly | Lane demand forecast, equipment balance snapshot, HOS + home-base constraints | Target trailer/container count per terminal per week |
| **Peak-season capacity reservation** | Seasonal (40-60 days ahead) | Historical peak patterns, customer commit forecasts, carrier peak-capacity offers | Reserved capacity per carrier per lane per peak period |
| **Seasonal surge routing playbook** | Pre-peak | Historical surge data, contingency carriers, alternative lane options | Tiered fallback playbook; feeds into L2 during surge |

**L3 canonical decision types**:
`SHIPPING_FORECAST` (L3 Demand Potential layer — distinct from DemandSensingTRM at L1 which is *signal* not *plan*), `UNCONSTRAINED_MOVEMENT_PLAN`, `CONSTRAINED_MOVEMENT_PLAN`, `CAPACITY_GAP_PROPOSAL`, `TENDER_COMMITMENT`, `DOCK_CAPACITY_PLAN`, `FLEET_DEPLOYMENT_PLAN`.

---

## 5. L2 Operational — terminal coordinator (hourly, 1-4 week horizon)

The operational layer is the **hub-local** coordinator: at each
transportation terminal (DC, cross-dock, rail ramp, ocean terminal)
it sits above the 11 TRMs and modulates their real-time urgency based
on local signals that neither L1 (too narrow) nor L3 (too
infrequent) can see.

Analog to SCP's site tGNN, but scoped to transport-plane semantics:
the "site" in L2 for TMS is a transportation hub, not a manufacturing
plant.

### 5.1 Terminal Coordinator decisions

| Decision | Trigger | Inputs | Outputs |
|---|---|---|---|
| **Cross-TRM urgency modulation** | Hourly + on HiveSignal | TRM decisions last hour, dock queue depth, carrier on-property count, in-transit exception count, last-hour tender-reject rate at this hub | Urgency multipliers pushed to the 11 TRMs via `HiveSignalBus` |
| **Carrier-waterfall depth tuning** | Hourly, per lane | Lane-specific reject history in this shift, carrier show-rate in last 7d at this hub, spot-rate premium today | Per-lane waterfall depth override for L1 FreightProcurementTRM |
| **Active-shipment batching within shift** | Shift boundary + every 2h | Unassigned shipments, dock slot inventory, available carriers on-property, SLA-clock-remaining | Shipment-to-load batching decisions passed to L1 LoadBuildTRM |
| **Dock re-sequencing** | On appointment slip (≥15 min late) | Current appointment schedule, late-arrival ETA, downstream shipment pickups dependent on the slot | Re-sequenced dock schedule, cascading updates to L1 DockSchedulingTRM |
| **Equipment-pool management at yard** | Hourly | Yard inventory by equipment-type, incoming drop-offs, outgoing demand next 4h, repositioning cost to neighbor yards | Yard-level equipment placement decisions; feeds L1 EquipmentRepositionTRM |
| **Cross-site signal aggregation** | Continuous | HiveSignals from every TRM at this hub | Site-health signal emitted upward to L3 + neighbor-hub awareness |

### 5.2 Upward feedback to L3

L2 aggregates hub-level data that L3 can't see in real-time:

- **Terminal health signal** — composite of dock utilization, tender-reject rate, exception backlog. If red, L3 re-solves the Constrained Balanced Plan for that hub.
- **Override pattern detection** — if human overrides at this hub cluster around a specific TRM, L2 surfaces the pattern for L3 / L4 policy adjustment.
- **Emerging bottlenecks** — dock congestion drift, equipment imbalance trend. Feeds the Capacity Gap Analyzer.

**L2 canonical decision types**:
`TERMINAL_URGENCY_MODULATION`, `CARRIER_WATERFALL_TUNE`, `BATCH_SHIPMENTS`, `DOCK_RESEQUENCE`, `YARD_EQUIPMENT_PLACEMENT`, `TERMINAL_HEALTH_SIGNAL`.

---

## 6. L1 Execution — the 11 TRMs (<10ms, current-shift horizon)

Already built. See [TMS_TRM_HEURISTIC_REFERENCE.md](TMS_TRM_HEURISTIC_REFERENCE.md)
for the canonical spec.

| Phase | TRM | Canonical decision type |
|---|---|---|
| SENSE | CapacityPromise | `CAPACITY_PROMISE` |
| SENSE | ShipmentTracking | `SHIPMENT_TRACKING` |
| SENSE | DemandSensing | `DEMAND_SENSING` |
| ASSESS | ExceptionManagement | `EXCEPTION_MANAGEMENT` |
| ASSESS | CapacityBuffer | `CAPACITY_BUFFER` |
| ACQUIRE | FreightProcurement | `FREIGHT_PROCUREMENT` |
| ACQUIRE | BrokerRouting | `BROKER_ROUTING` |
| PROTECT | DockScheduling | `DOCK_SCHEDULING` |
| BUILD | LoadBuild | `LOAD_BUILD` |
| BUILD | IntermodalTransfer | `INTERMODAL_TRANSFER` |
| REFLECT | EquipmentReposition | `EQUIPMENT_REPOSITION` |

L1 reads the `constrained_live` plan from L3 as its plan-of-record.
Any TRM decision that *violates* the plan is either (a) acceptable
drift that the plan tolerates, or (b) an exception that triggers
an L3 re-solve.

---

## 7. Cross-Authority Protocol (AAP) — event-driven

Not an agent. A **protocol** for boundary decisions that sit at the
intersection of TMS and another plane (Supply, Portfolio, WMS).
Analog across every plane.

### 7.1 TMS × Supply (SCP) boundary decisions

| Boundary decision | Initiating plane | Authorization flow |
|---|---|---|
| **Accept a supply-side shipment commitment** (SCP promises a ship-from-DC load to TMS by date D) | SCP | SCP creates `DeploymentRequirement`; TMS ACKs with `DispatchCommitment`; both sign off → L3 both sides lock it into `constrained_live`. |
| **Push back on a supply commitment** (TMS can't deliver by D given HOS + dock) | TMS | TMS emits `ServiceWindowPromise` with feasible D'; SCP re-plans or escalates to L4 exception. |
| **Coordinate mode-shift impact** (TMS intermodal reduces transit by 2d; does SCP re-plan downstream inventory position?) | TMS | TMS emits mode-shift advisory; SCP L3 reads; AAP negotiates if downstream impact exceeds threshold. |

### 7.2 TMS × Portfolio boundary (future)

| Boundary decision | Flow |
|---|---|
| **NPI / EOL launch-support capacity** (Portfolio launches new SKU → TMS must pre-position capacity) | Portfolio → TMS via AAP → L4 Strategic TMS re-evaluates carrier-contract portfolio |

### 7.3 TMS × WMS boundary (future)

| Boundary decision | Flow |
|---|---|
| **Dock-door labour alignment** (WMS schedules receiving labour; TMS schedules appointments) | L2 coordination via AAP; if WMS has no labour for a slot, TMS re-sequences |

**AAP canonical decision types**:
`DEPLOYMENT_REQUIREMENT`, `DISPATCH_COMMITMENT`, `SERVICE_WINDOW_PROMISE`, `BOUNDARY_ESCALATION`.

Reference: Autonomy-Core `docs/CROSS_PLANE_COLLABORATION_ARCHITECTURE.md`
(AD-11, locked 2026-04-22).

---

## 8. Vertical data flow

### 8.1 Top-down (directives / context)

```
L4  →  θ (policy parameters): BSC weights, service-level tiers, mode-mix
       targets, CO2 ceiling, fleet composition, contract portfolio
   ↓
L3  →  constrained_live plan: load-build + carrier assignment + mode +
       routing + dock slots per lane per period (within θ's envelope)
   ↓
L2  →  urgency vectors + waterfall-depth overrides + batching decisions
       (per hub, per hour)
   ↓
L1  →  individual execution decisions on specific shipments / loads /
       forecasts / capacity targets
```

### 8.2 Bottom-up (signals / outcomes)

```
L1  →  per-decision outcomes + HiveSignals (tender rejects, at-risk
       shipments, buffer stress, equipment surplus/deficit)
   ↑
L2  →  terminal health signal + override-pattern detection +
       emerging-bottleneck alerts
   ↑
L3  →  plan-performance metrics (OTD achievement, cost-per-load,
       tender-acceptance rate) + re-plan triggers when drift > threshold
   ↑
L4  →  reads rolling KPIs against θ; triggers strategic proposals when
       persistent gaps
```

---

## 9. What's built vs. what's design

### Built (2026-04-23)
- ✓ L1: all 11 canonical TMS TRMs (heuristic-backed, BC-checkpoint-loader
  stubbed pending Sprint 2)

### Design proposal only
- ◐ L3: three-plan pipeline documented in `TACTICAL_PLANNING_REARCHITECTURE.md`
  + `TMS_TIER3_FIRST_PLAN.md` (Path C, "GraphSAGE-first, skip LP")
- ◐ AAP: SCP × TMS boundary contracts locked in AD-11
  (`CROSS_PLANE_COLLABORATION_ARCHITECTURE.md`)

### Not started
- ✗ L4 Strategic: no TMS-specific S&OP agent; policy-parameter schema not
  defined; network-change-proposal pipeline not built
- ✗ L2 Operational: Terminal Coordinator doesn't exist; SCP's site_tgnn is
  SCP-domain and not yet rescoped
- ✗ Capacity Gap Analyzer: `rccp_service.py` references are SCP-era
- ✗ Plan-of-record discipline: L1 TRMs currently read per-agent ad-hoc
  decisions, not `constrained_live`. Phase 0 of `TMS_TIER3_FIRST_PLAN.md`
  flipped the default label to `'constrained_live'` but nothing produces
  real constrained plans yet.

### Scoped for deletion
- `inventory_rebalancing_trm`, `safety_stock_trm`, `mo_execution_trm`,
  `to_execution_trm`, `order_tracking_trm`, `forecast_baseline_trm`,
  `forecast_adjustment_trm`, `demand_adjustment_trm`, `supply_adjustment_trm`,
  `quality_disposition_trm`, `rccp_adjustment_trm`, `subcontracting_trm`,
  `maintenance_scheduling_trm`, `inventory_buffer_trm`, `inventory_adjustment_trm`
  (TRM service files deleted 2026-04-23 first-pass;
  tracked for fuller substrate extraction in `TMS_MIGRATION_1_13_PUNCH_LIST.md`)

---

## 10. Decision-to-agent mapping summary

| Decision class | Layer | Cadence | Agent(s) |
|---|---|---|---|
| Can we promise this shipment? | L1 | Real-time | CapacityPromiseTRM |
| Is this load at-risk? | L1 | Hourly sweep | ShipmentTrackingTRM |
| Adjust shipping-volume forecast (short-horizon signal)? | L1 | On signal | DemandSensingTRM |
| Route this shipment exception | L1 | On exception | ExceptionManagementTRM |
| Resize lane capacity buffer | L1 | Daily sweep | CapacityBufferTRM |
| Pick carrier (waterfall) | L1 | Per shipment | FreightProcurementTRM |
| Broker vs asset | L1 | Per shipment | BrokerRoutingTRM |
| Dock-door appointment triage | L1 | Per appointment | DockSchedulingTRM |
| Consolidate shipments → load | L1 | Per batch | LoadBuildTRM |
| Truck↔rail mode-shift GO/NO-GO | L1 | Per shipment | IntermodalTransferTRM |
| Reposition empty trailer/container | L1 | Daily sweep | EquipmentRepositionTRM |
| Modulate TRM urgency at this hub | L2 | Hourly | Terminal Coordinator |
| Tune carrier waterfall depth on this lane | L2 | Hourly | Terminal Coordinator |
| Batch shipments within shift | L2 | 2-hourly | Terminal Coordinator |
| Re-sequence docks when appointment slips | L2 | Event | Terminal Coordinator |
| Manage yard equipment pool | L2 | Hourly | Terminal Coordinator |
| Demand Potential (unconstrained lane × mode × period forecast) | L3 | Daily 05:00 | Analytical (LightGBM + Trigg) |
| Unconstrained Movement Plan (ideal consolidation + mode split) | L3 | Daily 05:30 | Movement Planner GraphSAGE |
| Constrained Balanced Plan (committed plan-of-record) | L3 | Daily 06:00 + event re-solve | Integrated Balancer (GraphSAGE + LP repair) |
| Identify persistent capacity gaps | L3 | Daily advisory | Capacity Gap Analyzer (advisory only) |
| Set weekly carrier tender commitments | L3 | Weekly | Tender-Commitment Planner |
| Dock appointment capacity planning | L3 | Weekly per site | Dock Capacity Planner |
| Fleet deployment plan | L3 | Weekly | Fleet Deployment Planner |
| Peak-season capacity reservation | L3 | Seasonal | Surge Planner |
| Open / close a terminal | L4 | Annual / event | S&OP GraphSAGE (proposal) |
| Add / drop a lane | L4 | Quarterly | S&OP GraphSAGE (proposal) |
| Carrier-contract portfolio rebalance | L4 | Contract cycle | S&OP GraphSAGE (proposal) |
| Fleet-composition change | L4 | Annual | S&OP GraphSAGE (proposal) |
| Customer service-level tier assignment | L4 | Annual / customer-add | S&OP GraphSAGE (proposal) |
| Mode-mix strategy | L4 | Annual | S&OP GraphSAGE (proposal) |
| BSC weights rebalance | L4 | Quarterly | S&OP GraphSAGE (proposal) |
| CO2-per-load-mile target | L4 | Annual / regulatory | S&OP GraphSAGE (proposal) |
| Accept supply-side shipment commitment | AAP | Event | Protocol (not an agent) |
| Push back on supply commitment | AAP | Event | Protocol |
| Coordinate mode-shift downstream impact | AAP | Event | Protocol |

---

## 11. Next steps

1. **Scope L4 schema** — define `policy_parameters` table columns
   (BSC weights, service-level tiers per customer, mode-mix targets, CO2
   ceiling, fleet composition). Wire into TMS tenant provisioning.
2. **Scope L2 Terminal Coordinator** — decide agent architecture
   (GATv2 + GRU, like SCP's site_tgnn, but with TMS features). Define
   state contract (what the Terminal Coordinator reads + writes).
3. **Start L3 Phase A** — TMS digital twin
   (`TMS_TIER3_FIRST_PLAN.md` §3). L3 is blocked on the twin.
4. **Execute 1.13 punch list** — extract SCP-fork residue so L2/L3/L4
   have a clean substrate to build on.

The right **next build** is L2 Terminal Coordinator if we want
the 11 TRMs to actually coordinate (today they run independently
with no hub-local urgency modulation). But L2 requires the twin for
training data, so practically: L3 Phase A (twin) + 1.13 extraction in
parallel, then L2 + L3 builds on top.

---

## 12. Cross-references

- [TMS_TRM_HEURISTIC_REFERENCE.md](TMS_TRM_HEURISTIC_REFERENCE.md) —
  L1 canonical spec
- [TACTICAL_PLANNING_REARCHITECTURE.md](TACTICAL_PLANNING_REARCHITECTURE.md) —
  L3 three-plan pattern, data-readiness audit
- [TMS_TIER3_FIRST_PLAN.md](TMS_TIER3_FIRST_PLAN.md) — L3 "Path C" sequencing
- [TMS_MIGRATION_1_13_PUNCH_LIST.md](TMS_MIGRATION_1_13_PUNCH_LIST.md) —
  SCP-fork substrate extraction
- Autonomy-Core `AGENT_ARCHITECTURE.md` — cross-product platform treatment
- Autonomy-Core `CROSS_PLANE_COLLABORATION_ARCHITECTURE.md` (AD-11) —
  cross-authority protocol
- SCP `docs/internal/architecture/PLANNING_HIERARCHY_AND_MRP.md` —
  SCP-side hierarchy (analogous but different domain)
