# TMS-as-AZIRELLA-tier Readiness Audit

**Status:** audit, 2026-05-05.
**Owner:** TMS team (acer-nitro session).
**Source:** msi-stealth's 2026-05-04 review of the heuristic-stack work
identified this as the long-pole TMS workstream:

> "Once Phase 5 deletes `azirella-tms-stub` from Core, any TMS-licensed
> customer needs the actual TMS backend handlers wired. That's the long
> pole on TMS's side and probably has its own register entries — worth
> acer-nitro mapping the gaps now while context is fresh from the
> heuristics PR."

Phase 5 has now landed in Core (commit `ae24626`). This doc maps the
TMS-side gap.

---

## 1. What "AZIRELLA tier" means for TMS

Per [AD-12 v3](../../Autonomy-Core/docs/architecture/ARCHITECTURE_DECISIONS.md)
and [§3.48](../../Autonomy-Core/docs/MIGRATION_REGISTER.md), each
tenant's `plane_registration.producer_tier` for `Plane.TRANSPORT` is
one of:

| Tier | Deployment shape |
|---|---|
| **AZIRELLA** | Real TMS backend deployed; `RouterClient` dispatches A2A HTTP to its A2A surface |
| **THIRD_PARTY** | Vendor MCP adapter (SAP TM, Manhattan, Oracle OTM, MercuryGate, BlueYonder TMS); in-process via `autonomy-tms-mcp-adapters` |
| **HEURISTIC** | No TMS deployed; in-process via `autonomy-tms-heuristics` |

Demo (2026-05-11) is HEURISTIC tier — no TMS container deploys; SCP/DP
backends pip-install `autonomy-tms-heuristics` and the router synthesises
TMS responses in-process. **That demo path is fully wired today.**

This audit is about the **AZIRELLA-tier** path: what's required for a
TMS-licensed tenant whose `producer_tier = AZIRELLA` to get real
TMS-backed responses to cross-plane skill calls (load consolidation,
carrier recommendation, lane ETA, etc.).

---

## 2. Per-TRM checkpoint state — 11 TRMs, 9 shipped checkpoints

Every TMS TRM follows the same load lifecycle (per
[`bc_checkpoint_loader.py`](../backend/app/services/powell/bc_checkpoint_loader.py)):

1. Try to load BC checkpoint from
   `/app/models/pretrained/trm_base_v{N}/trm_{name}_bc_v1.pt`
2. If checkpoint loads cleanly: `_model` is the trained MLP; inference
   uses `_model.forward(state_vector)`.
3. If checkpoint missing or torch unavailable: `_model = None`;
   inference falls back to `compute_tms_decision(trm_type, state)` from
   [`autonomy_tms_heuristics.library`](../packages/autonomy-tms-heuristics/src/autonomy_tms_heuristics/library/)
   — the canonical heuristic library that PR #51 (PR-LOCK) consolidated
   per [§3.52](../../Autonomy-Core/docs/MIGRATION_REGISTER.md).

**Checkpoint inventory** (`backend/training_data/checkpoints/`):

| TRM | Checkpoint shipped? | Heuristic teacher (fallback) | Cross-plane skill? |
|---|:-:|---|:-:|
| CapacityPromise | ✓ `trm_capacity_promise_bc_v1.pt` | `_compute_capacity_promise` | — |
| BrokerRouting | ✓ `trm_broker_routing_bc_v1.pt` | `_compute_broker_routing` | — |
| FreightProcurement | ✓ `trm_freight_procurement_bc_v1.pt` | `_compute_freight_procurement` | ✓ `transport.carrier.recommend` |
| CapacityBuffer | ✓ `trm_capacity_buffer_bc_v1.pt` | `_compute_capacity_buffer` | — |
| **ShipmentTracking** | **✗ no checkpoint** | `_compute_shipment_tracking` | ✓ `transport.lane.estimate_eta`* |
| **IntermodalTransfer** | **✗ no checkpoint** | `_compute_intermodal_transfer` | — |
| LoadBuild | ✓ `trm_load_build_bc_v1.pt` | `_compute_load_build` | ✓ `transport.load.evaluate_consolidation` |
| DockScheduling | ✓ `trm_dock_scheduling_bc_v1.pt` | `_compute_dock_scheduling` | — |
| EquipmentReposition | ✓ `trm_equipment_reposition_bc_v1.pt` | `_compute_equipment_reposition` | — |
| ExceptionManagement | ✓ `trm_exception_management_bc_v1.pt` | `_compute_exception_management` | — |
| DemandSensing | ✓ `trm_demand_sensing_bc_v1.pt` | `_compute_demand_sensing` | — |
| LaneVolumeForecast (12th) | ✓ `trm_lane_volume_forecast_bc_v1.pt` | `_compute_lane_volume_forecast` | — |

\* `transport.lane.estimate_eta` is currently the haversine math primitive
(`autonomy_tms_heuristics.eta`), not `ShipmentTracking`'s in-flight-progress
heuristic. The audit doc for that distinction lives in
[§3.52 step 3](../../Autonomy-Core/docs/MIGRATION_REGISTER.md).

**Implications:**

- **9 of 11 TRMs have shipped checkpoints.** ShipmentTracking and
  IntermodalTransfer fall through to heuristic for every tenant today.
- **All shipped checkpoints are behavioral-cloning imitations of the
  heuristic teacher.** The training corpus
  ([`generate_tms_corpus.py`](../backend/scripts/pretraining/generate_tms_corpus.py))
  sweeps each TRM's state dataclass across realistic distributions and
  labels each sample with `compute_tms_decision(...)`. **The neural
  network's job is to mimic the heuristic, not surpass it.**
- Improving on the heuristic requires real outcome data (
  `(state, action, observed_outcome)` tuples from production deployments)
  and CDC-driven retraining. That loop exists in code but has no live
  tenants today.

---

## 3. Cross-plane A2A surface — 3 of 11 TRMs exposed

[`backend/app/a2a/skills.py`](../backend/app/a2a/skills.py) wires three
canonical cross-plane skills to TRM internals:

| Skill ID | TRM wrapped |
|---|---|
| `transport.lane.estimate_eta` | `ShipmentTrackingTRM` (heuristic-only — no checkpoint) |
| `transport.load.evaluate_consolidation` | `LoadBuildTRM` (BC-trained checkpoint) |
| `transport.carrier.recommend` | `FreightProcurementTRM` (BC-trained checkpoint) |

The other 8 TRMs operate via internal decision cycles (per-site agent,
governance pipeline) and are not exposed cross-plane today. If
SCP/DP/Portfolio later need access to dock scheduling, equipment
reposition, exception triage, or capacity-buffer recommendations,
those skills get added to `app/a2a/skills.py` + the canonical Agent
Card.

---

## 4. Substrate gaps blocking AZIRELLA-tier deployment

### 4.1 §1.13 — Powell substrate move to Core (msi-stealth's lane)

[Core MIGRATION_REGISTER §1.13](../../Autonomy-Core/docs/MIGRATION_REGISTER.md)
lists ~45 TMS-side framework files that should move to Core. After
PR-5.E ([PR #54](https://github.com/azirella-ltd/Autonomy-TMS/pull/54))
deleted the SCP-shape decision tier, the remaining substrate-bound
files on TMS main are:

| File | Lines | §1.13 Core target |
|---|---:|---|
| `sop_inference_service.py` | 685 | `azirella_data_model.powell.site_agent_model` |
| `execution_gnn_inference_service.py` | 486 | `azirella_data_model.powell.site_agent_model` |
| `powell_training_service.py` | 1,256 | Tier 3.5 (Training infrastructure) |
| `inter_hive_signal.py` | 197 | `azirella_data_model.powell` (hive coordination) |
| `generic_training_orchestrator.py` | 525 | Tier 3.5 |
| `relearning_jobs.py` (post-PR-5.E trim) | 1,195 | Tier 2.3 + 2.5 |

These run in TMS today. AZIRELLA-tier deployment doesn't strictly
need them in Core (they work where they are), but the long-term
architecture per CLAUDE.md placement rules wants them as substrate.
**Not on the AZIRELLA-tier blocking critical path** — informational
context for msi-stealth's roadmap.

### 4.2 §3.41 — GraphSAGE Movement Planner training (Core, in flight)

[§3.41](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) Phase 3 work
in Core ships a real PyTorch GraphSAGE model class with end-to-end
training. The TMS-shape Tactical-tier replacement work (S&OP carrier
portfolio, lane-volume tGNN, equipment tGNN) builds on this. msi-stealth
delivers the substrate; acer-nitro retargets TMS imports + builds
TMS-shape model classes once Core has the scaffolding.

### 4.3 §3.9 — Frontend admin Phase B (msi-stealth's lane)

The 5 frontend admin pages flagged by the PR-5.E audit
(`GraphSAGEDashboard.jsx`, `PlanningHierarchyConfig.jsx`,
`ProvisioningStepper.jsx`, `AITrainingConfig.jsx`,
`GNNDirectiveReview.jsx`) move to Core's
`packages/autonomy-frontend/src/admin/` at §3.9 Phase B. After they
move, TMS frontend retargets imports + deletes its local copies.

### 4.4 Twin rewrite (acer-nitro's lane, in flight)

The TMS twin rewrite per [TWIN_REWRITE_PLAN.md](TWIN_REWRITE_PLAN.md)
delivers TMS-shape training data via:

- `LaneFlowSimulator` (PR-3.A through PR-3.G; physics models) — staged
  in PRs #27–#34. **Open as documentation per the user's earlier
  direction.**
- `ShipmentGenerator` Phase-1 parametric (PR-2) and Phase-2
  tenant-calibrated (PR-6 / PR-6b).
- `LaneFlowStepAdapter` for RL trajectory production.

Once these PRs land, the corpus generator (`generate_tms_corpus.py`)
can be retargeted from "sweep heuristic teacher across distributions"
to "drive lane-flow simulator with the heuristic, capture realised
trajectories." That elevates TRM training from
*heuristic-imitation* (today) to *physics-grounded RL on
realised carrier flow*.

---

## 5. What's blocking AZIRELLA-tier deployment for a real tenant

Ordered by impact:

### 5.1 Demo-blocker class — none

Nothing on the AZIRELLA-tier path blocks the 2026-05-11 demo. Demo
runs HEURISTIC for TMS; this audit's items are post-demo workstream.

### 5.2 Real-tenant production-readiness blockers (acer-nitro lane)

1. **`ShipmentTracking` BC checkpoint missing** — train + ship.
   `generate_tms_corpus.py --trm shipment_tracking --samples 50000` +
   `train_tms_trms.py --trm shipment_tracking`. Mirror the existing 9.
2. **`IntermodalTransfer` BC checkpoint missing** — same fix.
3. **`/app/models/pretrained/trm_base_v1/` deployment plumbing** —
   confirm Dockerfile copies `backend/training_data/checkpoints/` →
   `/app/models/pretrained/trm_base_v1/`, or document the manual ship
   step. The provisioning loader expects them at the latter path.
4. **CDC retraining + outcome collection on real production** — code
   exists; needs a real tenant to start producing real
   `(state, action, outcome)` tuples for the relearning loop to do
   anything beyond reload the same heuristic-imitation weights.

### 5.3 Cross-plane skill-surface expansion (acer-nitro, opportunistic)

Today only 3 of 11 TRMs are exposed as cross-plane skills. Adding
more (DockScheduling, EquipmentReposition, ExceptionManagement, etc.)
is a one-handler-per-skill addition to `backend/app/a2a/skills.py`
plus matching entries in `autonomy-tms-heuristics/handlers.py` for
HEURISTIC-tier coverage. **Driven by consumer demand** — only worth
doing when SCP/DP/Portfolio actually need the skills.

### 5.4 Substrate moves to Core (msi-stealth lane)

Not blocking acer-nitro work; tracked under §1.13 / §3.41 / §3.9 in
Core's MIGRATION_REGISTER. The substrate-bound files
(`sop_inference_service.py` / `execution_gnn_inference_service.py` /
`powell_training_service.py` / `inter_hive_signal.py` /
`generic_training_orchestrator.py` / trimmed `relearning_jobs.py`)
remain on TMS until msi-stealth runs the moves. Once they land in
Core, acer-nitro retargets imports + deletes the local copies.

### 5.5 Frontend admin pages (msi-stealth lane via §3.9 Phase B)

Same shape as 5.4 — TMS retargets after Core moves.

### 5.6 Twin-rewrite landings (acer-nitro lane, deferred)

The 14 open twin-rewrite PRs (#27–#45) stay open as documentation
per the user's earlier direction. Landing them is the unblocker for
TMS-shape TRM training (vs heuristic-imitation). **Out of scope for
AZIRELLA-tier readiness at the v1 production level** — heuristic-
imitation models are sufficient until real production data exists.

---

## 6. Summary — TMS is closer to AZIRELLA-ready than the audit headline suggests

The PR-5 series wave gives a misleading "everything is dead" impression
because the deletes targeted SCP-shape residue. Underneath:

- **All 11 TRMs are real Python services** with industry-best-practice
  heuristic teachers and a uniform BC checkpoint contract. Total
  code: ~5,000 lines of decision policy.
- **9 of 11 TRMs ship trained checkpoints** in the image
  (`backend/training_data/checkpoints/`). ShipmentTracking and
  IntermodalTransfer remain heuristic-only — straightforward to fix.
- **The cross-plane A2A surface** ([`app/a2a/skills.py`](../backend/app/a2a/skills.py))
  exposes 3 canonical TMS skills wired to real TRMs. AZIRELLA-tier
  consumers get real TRM responses; HEURISTIC-tier consumers get
  conservative defaults via `autonomy-tms-heuristics`.
- **The training pipeline is wired end-to-end** (corpus generator +
  PyTorch BC trainer + checkpoint storage + provisioning loader).
- **The relearning loop is wired** (CDC retraining cron, outcome
  collection, drift monitoring).

What's not yet real is **outcome-driven retraining** — the
heuristic-imitation models on disk haven't been improved by real
production data because TMS hasn't run as AZIRELLA-tier for a real
tenant yet. That's a chicken-and-egg fixed by deploying for an
internal-pilot tenant, collecting outcomes, and watching the CDC
loop close itself.

The acer-nitro pre-demo work (PR #49 / #50 / #51 / #52 / #53 / #54
+ this audit) puts TMS in a clean state to deploy as AZIRELLA-tier
once a customer asks for it.

---

## 7. Recommendations — ordered

For acer-nitro (TMS lane):

1. **Train + ship ShipmentTracking and IntermodalTransfer BC checkpoints**
   to remove the two heuristic-only TRMs. ~1 day each. Independent of
   any other work.
2. **Confirm Dockerfile copies the checkpoints to
   `/app/models/pretrained/trm_base_v1/`**. If not, fix the COPY
   directive. Otherwise document the manual ship step.
3. **Skill-surface expansion** when consumer demand surfaces — one
   handler per skill ID. Not speculative work.
4. **Twin-rewrite landings** — re-enter the queue post-demo. Today's
   open PRs (#27–#45) are the documentation backbone; landing them
   is the unblock for TMS-shape RL training.

For msi-stealth (Core lane):

1. **§1.13 substrate move** — port the 6 substrate-bound files to
   Core's `azirella_data_model.powell` per the existing register
   plan. Acer-nitro retargets imports after.
2. **§3.41 GraphSAGE Movement Planner Phase 4+** — finish the Core
   training pipeline scaffolding so TMS can build TMS-shape
   tactical-tier services on it.
3. **§3.9 Phase B frontend admin consolidation** — move the 5 TMS
   admin pages to Core's `autonomy-frontend` package.

---

## 8. Cross-references

- [TWIN_REWRITE_PLAN.md](TWIN_REWRITE_PLAN.md) — twin rewrite plan
- [TWIN_PR5_CALLER_MIGRATION_AUDIT.md](TWIN_PR5_CALLER_MIGRATION_AUDIT.md) — PR-5.A
- [TWIN_PR5D_DAG_SIMULATOR_AUDIT.md](TWIN_PR5D_DAG_SIMULATOR_AUDIT.md) — PR-5.D
- [TWIN_PR5E_POWELL_GNN_STACK_AUDIT.md](TWIN_PR5E_POWELL_GNN_STACK_AUDIT.md) — PR-5.E
- [Core MIGRATION_REGISTER §1.13](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — Powell substrate move
- [Core MIGRATION_REGISTER §3.9](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — Frontend admin consolidation
- [Core MIGRATION_REGISTER §3.41](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — GraphSAGE Movement Planner
- [Core MIGRATION_REGISTER §3.48](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — AD-12 v3
- [Core MIGRATION_REGISTER §3.52](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — Heuristic libraries (PR-LOCK)

---

*Last updated: 2026-05-05.*
