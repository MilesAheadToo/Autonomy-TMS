# PR-5.E Audit — Powell GNN Inference / Training Stack

**Status:** audit, 2026-05-04.
**Owner:** TMS team (acer-nitro session).
**Companion:** [TWIN_PR5_CALLER_MIGRATION_AUDIT.md](TWIN_PR5_CALLER_MIGRATION_AUDIT.md) (parent),
[TWIN_PR5D_DAG_SIMULATOR_AUDIT.md](TWIN_PR5D_DAG_SIMULATOR_AUDIT.md) (sibling). Closes the
gap PR-5.A undersold — the SCP-shape **inference / serving** path
that runs alongside the SCP-shape *training-data* pipeline that
PR-5.B deleted.

**Resolves:** Trevor's question from 2026-05-04 — *"why do we need
[these files]? We're on TMS here, not SCP. Some of this sounds very
much like TBG baggage."*

---

## 1. The headline finding

**Zero of the 11 TMS TRMs consume any output from the Powell GNN
inference stack.** Verified by grepping every TRM file for any of
`TGNN`, `SOPInference`, `ExecutionGNN`, `GNNOrchestration`,
`tactical_hive`, or import paths into the Powell GNN modules:

| TMS TRM | Consumes Powell GNN? |
|---|---|
| CapacityPromiseTRM | No |
| BrokerRoutingTRM | No |
| FreightProcurementTRM | No |
| CapacityBufferTRM | No |
| ShipmentTrackingTRM | No |
| IntermodalTransferTRM | No |
| LoadBuildTRM | No |
| DockSchedulingTRM | No |
| EquipmentRepositionTRM | No |
| ExceptionManagementTRM | No |
| DemandSensingTRM | No |

The Powell GNN stack runs in TMS — provisioning steps train it,
APScheduler triggers daily inference, a REST endpoint exposes
on-demand cycles — but **its outputs are never read by anything in
the TMS plane.** ~9,500 lines + provisioning steps + cron + frontend
admin pages are running for an audience of zero.

---

## 2. The stack — 15 files, 9,585 lines

### 2.1 Inference services (4 SCP-shape, 3 substrate-ish)

The four "tactical" tGNNs are unambiguously SCP-shape — their own
docstrings say so. Each loads a trained checkpoint and emits decisions
per site:

| Service | Lines | What it produces |
|---|---:|---|
| `supply_planning_tgnn_service.py` | 449 | "supply exception probability", "order quantity recommendations", "allocation priority", "lead time risk", "pipeline coverage days" |
| `demand_planning_tgnn_service.py` | 472 | "demand forecasts", "demand volatility", **"bullwhip coefficient predictions"** |
| `inventory_optimization_tgnn_service.py` | 476 | "buffer adjustment signals", "rebalancing urgency", "stockout probability", "days of stock estimates" |
| `capacity_rccp_tgnn_service.py` | 598 | "planned utilization targets", "capacity buffer recommendations", **"RCCP validation against MPS"** |

**RCCP** = Rough-Cut Capacity Planning. **MPS** = Master Production
Schedule. **Bullwhip** is straight Beer Game terminology. None of
these decision shapes maps onto the TMS plane (carrier acceptance,
lane utilisation, dock queue depth, equipment balance, load
consolidation savings, on-time delivery).

The three more substrate-ish services share the same SCP-shape
output schema:

| Service | Lines | Shape |
|---|---:|---|
| `sop_inference_service.py` | 685 | S&OP GraphSAGE — structural graph features (criticality, bottleneck risk, concentration risk, resilience) plus 64-dim embeddings. Plane-agnostic *in principle*; consumed only by the SCP-shape stack today. |
| `execution_gnn_inference_service.py` | 486 | "demand forecasts (short-term, daily)", "exception probability predictions", "order recommendations" — SCP-shape decision schema even if the GNN itself could in theory be retrained on TMS data. |
| `gnn_orchestration_service.py` | 520 | Glues SOP + Execution + Tactical Hive. Plane-agnostic shape but only orchestrates SCP-shape today. |

### 2.2 Coordination + auxiliary (4 files)

| Service | Lines | Purpose |
|---|---:|---|
| `tactical_hive_coordinator.py` | 625 | Coordinates the 3 tactical tGNNs. Architecture diagram in its docstring names `ForecastBaselineTRM` and `ForecastAdjustmentTRM` (SCP TRM names) feeding the supply-side tGNNs — **no TMS TRM appears anywhere.** |
| `joint_inventory_capacity_service.py` | 166 | Joint optimization of inventory + capacity outputs from the two SCP-shape tGNNs. |
| `inter_hive_signal.py` | 197 | Signal-bus types for hive coordination. |
| `relearning_jobs.py` | 1,214 | APScheduler entries that trigger the daily GNN cycle in TMS. |

### 2.3 Training (3 files)

| Service | Lines | Purpose |
|---|---:|---|
| `tactical_tgnn_training_service.py` | 616 | Trains the 3 tactical tGNNs (supply / demand / inventory / capacity). |
| `powell_training_service.py` | 1,256 | Trains S&OP GraphSAGE + Execution tGNN + TRMs. **Imports `Site` and `TransportationLane` from canonical entities** — references TMS-canonical types but the *decisions* it trains for are SCP-shape. |
| `generic_training_orchestrator.py` | 525 | Orchestration shell over PowellTrainingService. |

### 2.4 Top-level orchestrator (1 file)

| Service | Lines | Purpose |
|---|---:|---|
| `agent_orchestrator_service.py` | 1,300 | Powell agent orchestrator: trigger detection, mode determination (AUTOMATE / INFORM), CDC integration. **Zero inbound callers in the TMS backend** — self-orphaned. Architecture diagram in its docstring describes "S&OP GraphSAGE → Execution tGNN → Narrow TRMs" — the SCP planning cascade, not the TMS plane. |

**Stack total: 9,585 lines across 15 files.**

---

## 3. The runtime footprint

The stack isn't just dormant code — TMS pays compute cost for it
every provisioning run and every day:

### 3.1 Provisioning steps that drive it

[`provisioning_service.py`](../backend/app/services/provisioning_service.py)
includes (paraphrased; full list at `_step_*` definitions):

| Step | Action |
|---|---|
| `_step_sop_graphsage` | Trains S&OP GraphSAGE (SCP-shape) |
| `_step_demand_tgnn` | Trains DemandPlanningTGNN |
| `_step_supply_tgnn` | Trains SupplyPlanningTGNN |
| `_step_inventory_tgnn` | Trains InventoryOptimizationTGNN |
| `_step_capacity_tgnn` | Trains CapacityRCCPTGNN — **"RCCP validation against MPS"** |
| `_step_trm_training` | Trains the SCP-shape TRMs |
| `_step_rl_training` | RL fine-tune (uses dag_simulator's training corpus) |
| `_step_supply_plan` | (TMS-shape via `tms_provisioning_adapter.adapt_transportation_plan`) |
| `_step_rccp_validation` | (TMS-shape via `tms_provisioning_adapter.adapt_capacity_validation`) |
| `_step_site_tgnn` | Site-level tGNN |
| `_step_*_bg` | Background variants of the above |

The `tms_provisioning_adapter.py` claims these steps "map to TMS
equivalents" via display-label rewrites
([`tms_provisioning_adapter.py:52–72`](../backend/app/services/tms_provisioning_adapter.py#L52)):

```
sop_graphsage  → "S&OP GraphSAGE (Carrier Portfolio)"
demand_tgnn    → "Demand tGNN (Lane Volume)"
inventory_tgnn → "Equipment tGNN (Yard/Fleet)"
capacity_tgnn  → "Capacity tGNN"
```

That mapping is **labels only** — the actual implementations
underneath are the SCP-shape services in §2.1. The TMS rebadge never
landed.

### 3.2 Daily cron

`relearning_jobs.py` (1,214 lines) registers an APScheduler job that
calls `GNNOrchestrationService.run_full_cycle()` daily, which runs:

1. `SOPInferenceService.analyze_network()` (cached weekly/monthly)
2. `ExecutionGNNInferenceService.infer()`
3. Broadcasts the results as `tGNNSiteDirectives` over the
   "intra-hive signal buses".

Nothing in the TMS plane consumes those directives — the
hive subscribers are SCP-shape SiteAgents that don't exist as TMS
TRMs.

### 3.3 REST endpoint

[`endpoints/site_agent.py`](../backend/app/api/endpoints/site_agent.py)
exposes `POST /site-agent/gnn/run-cycle` which kicks the
orchestration manually. Same residue downstream.

### 3.4 Frontend admin pages (5 files)

| Page | Purpose |
|---|---|
| [`pages/admin/GraphSAGEDashboard.jsx`](../frontend/src/pages/admin/GraphSAGEDashboard.jsx) | S&OP GraphSAGE training / inference UI |
| [`pages/admin/PlanningHierarchyConfig.jsx`](../frontend/src/pages/admin/PlanningHierarchyConfig.jsx) | Configures the hierarchy the SCP-shape stack trains against |
| [`components/supply-chain-config/ProvisioningStepper.jsx`](../frontend/src/components/supply-chain-config/ProvisioningStepper.jsx) | The provisioning-pipeline UI lists every `_step_*` row, including the SCP-shape ones |
| [`components/admin/AITrainingConfig.jsx`](../frontend/src/components/admin/AITrainingConfig.jsx) | Knobs for the SCP-shape training |
| [`components/admin/GNNDirectiveReview.jsx`](../frontend/src/components/admin/GNNDirectiveReview.jsx) | Reviews the GNN-broadcast directives nothing reads |

Per Core's [`MIGRATION_REGISTER §3.9`](../../Autonomy-Core/docs/MIGRATION_REGISTER.md),
admin UI moves to Core (`packages/autonomy-frontend/src/admin/`) at
Phase B. So these pages land on msi-stealth's plate eventually.
Whether they're worth migrating depends on whether the underlying
backend stays.

---

## 4. Why this happened

Same root cause as everything PR-5 has been cutting: TMS forked from
SCP, inherited the entire SCP twin substrate, and the rewrite plan
focused on the **digital twin / training-data path**
(`dag_simpy_simulator` → `LaneFlowSimulator`). The **inference /
serving path** (Powell GNNs → SiteAgents) was never on the rewrite
plan because it sat in a different folder (`app/services/powell/`
vs `app/services/`) and didn't show up in the audit's import
walk from `SimulationDataConverter`.

The `tms_provisioning_adapter.py` mapping (SCP step name → TMS
display label) was the team's intent to repurpose the substrate
without rewriting it. That repurposing requires the underlying
GNNs to retrain on TMS-shape data with TMS-shape outputs — which
they don't, because:

1. The training corpus came from `dag_simulator` /
   `dag_simpy_simulator` (deleted PR-5.B/5.C) which produced
   inventory-shape labels, not carrier-flow labels.
2. The output schemas (per §2.1) have inventory-shape fields hard-coded
   into the dataclasses (`buffer_adjustment_signal`,
   `stockout_probability`, `rebalancing_urgency`,
   `bullwhip_coefficient`). Retraining the network doesn't change the
   field names.

---

## 5. Three forward-options

### 5.A — Delete the SCP-shape Powell stack

Cut all 15 files (~9,585 lines) plus the 11 SCP-shape provisioning
steps plus the daily cron plus the REST endpoint plus the 5 frontend
admin pages.

What survives in TMS-plane:
- The TMS twin rewrite (`digital_twin/` subpackage — PR-1 through PR-6b)
- The 11 TMS TRMs in `app/services/powell/{capacity_promise,broker_routing,...}_trm.py`
- The plane-agnostic Powell substrate (BeliefState, PolicyParameters, etc.)
- The provisioning steps that *don't* call SCP-shape inference
  (`warm_start`, `cfa_optimization`, `lgbm_forecast`, `conformal`,
  `scenario_bootstrap`, `briefing`, `decision_seed`, plus the two
  `tms_provisioning_adapter`-routed ones).

**Pros:** removes ~10,000 lines of running-but-unread code; aligns
with `CLAUDE.md`'s "convert implicit dead code to explicit dead code"
rule; eliminates the daily compute cost.

**Cons:** if there's a future intent to do TMS-shape strategic /
tactical GNN inference (carrier-portfolio S&OP, lane-volume tGNN,
equipment tGNN), this delete trashes the substrate that work would
build on. The plane-agnostic GraphSAGE / tGNN scaffolding (training
loop, checkpoint storage, signal bus) might be salvageable for a
TMS-shape rewrite.

### 5.B — Replace the SCP-shape Powell stack with TMS-shape

Build TMS-shape equivalents:
- TMS S&OP GraphSAGE — carrier-portfolio + lane-strategy embeddings.
- TMS Demand tGNN — lane-volume forecasts (Strategic / Tactical /
  Execution per `project_tms_forecast_architecture` memory).
- TMS Capacity tGNN — carrier-capacity + dock-throughput estimates.
- TMS Equipment tGNN — yard / fleet equipment flow.
- New tactical-hive coordinator that feeds the 11 TMS TRMs.

**Pros:** preserves the multi-tier Powell architecture (Strategic /
Tactical / Execution) for TMS plane.

**Cons:** essentially the work of (5.A delete) plus rewriting all
~9,500 lines as TMS-shape services. Multi-month project. Out of
scope for the cleanup phase.

### 5.C — Park (disable, don't delete)

- Drop the SCP-shape steps from `STEPS` constant (steps become
  no-ops, the provisioning UI loses the rows).
- Remove the daily APScheduler entry from `relearning_jobs.py`.
- Remove the `POST /site-agent/gnn/run-cycle` route.
- Leave the 9,500 lines of code in place.

**Pros:** zero compute cost, smallest blast radius. Code reachable
for forensic / future-replace use.

**Cons:** doesn't actually remove the residue. Future contributors
will keep tripping over it. Reverses the spirit of the PR-5 cleanup.

---

## 6. Recommendation — staged delete (5.A) over multiple sub-tickets

Before any code lands, the user has to confirm two things:

1. **Is there a near-term plan to do TMS-shape strategic / tactical
   inference (5.B)?** If yes, defer the delete and start the replace
   work instead (with the parametric Phase-1 stubs serving until the
   real models train). If no, proceed with delete.
2. **Are the frontend admin pages used by anyone today?** They're
   live in `Autonomy-TMS/frontend/src/`. If a real user clicks
   `GraphSAGEDashboard.jsx` and expects it to work, the delete
   should pair with a frontend cleanup.

Assuming "no" to both, stage the delete as:

| | Sub-ticket | Scope |
|---|---|---|
| **5.E.1** | This audit doc (no code change). |
| **5.E.2** | Disable the daily `relearning_jobs.py` entry + the `POST /site-agent/gnn/run-cycle` route. Park-without-delete first; verifies nothing user-facing breaks. |
| **5.E.3** | Remove the SCP-shape provisioning steps from the `STEPS` registry + the corresponding `_step_*` methods. Provisioning UI shrinks; new tenants stop training the SCP-shape models. |
| **5.E.4** | Delete the 15 service files (~9,585 lines). Remove `tactical_hive_coordinator` orchestration. |
| **5.E.5** | Frontend admin-page cleanup (probably handled at Phase B of the consolidation, not here). |
| **5.E.6** | After 5.E.4 lands, `dag_simulator.py` becomes 100 % residue (its only post-PR-5 caller — `sop_inference_service.py` — is gone). Delete it cleanly without the `topology_loader.py` extraction PR-5.D recommended. |

Stage 5.E.2 is the real "is this safe" check. If a week passes with
the cron disabled and nothing complains, the rest of the stages are
mechanical.

---

## 7. Open question for the user

Two answers needed before any code change:

1. Is there a near-term plan to build TMS-shape strategic / tactical
   GNN inference, or is the Powell GNN tier conceptually replaced by
   the per-TRM lane-flow training pipeline (PR-3.A through PR-3.G)?
2. Is the `GraphSAGEDashboard` / `AITrainingConfig` /
   `GNNDirectiveReview` frontend used by anyone today, or is it dead
   along with the backend?

Until those answers land, this audit doc is the deliverable; the
code change waits.

---

## 8. Cross-references

- [TWIN_PR5_CALLER_MIGRATION_AUDIT.md](TWIN_PR5_CALLER_MIGRATION_AUDIT.md) — parent (training-data path).
- [TWIN_PR5D_DAG_SIMULATOR_AUDIT.md](TWIN_PR5D_DAG_SIMULATOR_AUDIT.md) — sibling (deterministic-simulator audit). 5.E.6 takes its delete to completion.
- [TWIN_REWRITE_PLAN.md](TWIN_REWRITE_PLAN.md) — overall PR sequence.
- [`Autonomy-Core/docs/MIGRATION_REGISTER.md §3.9`](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — frontend admin migrates to Core at Phase B; relevant to 5.E.5.
- [`tms_provisioning_adapter.py`](../backend/app/services/tms_provisioning_adapter.py) — the file that documents the SCP→TMS step-label rebadge that never landed in the underlying services.

---

*Last updated: 2026-05-04.*
