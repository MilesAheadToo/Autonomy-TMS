# TMS Powell GNN Rewrite Plan

**Status:** design doc, 2026-05-11.
**Owner:** TMS team (acer-nitro session).
**Companion (Core-side, to draft):** `Autonomy-Core/docs/architecture/POWELL_GNN_SUBSTRATE.md` — the plane-agnostic substrate contracts this TMS-side design depends on. Owned by msi-stealth; this doc enumerates the substrate interfaces the rewrite expects.
**Supersedes:** the "Park or replace" question in [TWIN_PR5E_POWELL_GNN_STACK_AUDIT.md §5](TWIN_PR5E_POWELL_GNN_STACK_AUDIT.md). Decision recorded 2026-05-10: build TMS-shape replacement (Option 5.B) — substrate stays, SCP-shape heads get rewritten.

---

## 1. Why this rewrite — the SCP-fork problem

[TWIN_PR5E_POWELL_GNN_STACK_AUDIT.md](TWIN_PR5E_POWELL_GNN_STACK_AUDIT.md) found **15 files, 9,585 lines, zero TMS TRM consumers** in the Powell GNN inference / training stack. Every output schema (RCCP capacity, MPS validation, bullwhip coefficient, stockout probability, days-of-supply) is SCP-shape. The provisioning steps train them on SCP-shape data; APScheduler reruns them daily; nothing reads the results.

The audit gave the user a choice: delete (5.A) or rewrite (5.B). User chose 5.B on the basis that the multi-tier Powell architecture (Strategic / Tactical / Execution per CLAUDE.md) is the foundation the TMS plane should keep. The substrate (training loop, checkpoint storage, signal bus, GraphSAGE / tGNN scaffolding) is generic — only the *decision heads* and the *training data they expect* are plane-specific.

This doc plans the **TMS-side head rewrite**. The substrate extraction is the Core-side companion workstream.

---

## 2. Architectural decision — Option C (per market analysis)

Per [market analysis 2026-05-11](#) (vendor scan: o9, Blue Yonder Luminate, Manhattan Active, Kinaxis Maestro, SAP IBP, Oracle SCM Cloud, OMP, e2open):

> Every major SC platform rebuilt in the last 5 years has converged on **plane-agnostic substrate + plane-specific decision modules**. Gartner's 2025 "composable + data fabric" theme blesses this shape. Mid-market buying behaviour is best-of-breed dominant (~70 % hybrid stacks), which forbids a tightly-coupled monolith.

Concrete placement (per [CLAUDE.md](../../Autonomy-Core/CLAUDE.md) two-rule test):

| Layer | Substrate (Core owns) | Plane-specific head (TMS owns) |
|---|---|---|
| **L4 Strategic** (weekly S&OP, GraphSAGE) | Training loop, checkpoint storage, node/edge protocols, scenario sampler hooks | Carrier-portfolio S&OP head: lane allocation, dedicated vs broker split, contract renegotiation triggers |
| **L3 Tactical** (daily tGNN) | tGNN scaffolding (temporal convolutions, attention, prediction heads with configurable output shape) | Per-domain TMS-shape heads (see §4) |
| **L2 Operational** (hourly Site Coordinator) | GATv2+GRU architecture, hive signal bus types | Site-coordinator wiring to the 11 TMS TRMs |
| **L1 TRMs** | TRMTrainer, parquet corpus format, BC + RL hooks | Already TMS-owned (11 TRMs shipped) |
| **AAP** (cross-authority) | Negotiation protocol primitives | Plane-specific authority boundaries |

The commercial shape Option C unlocks (per market analysis): **substrate fee (Core platform) + per-plane decision-module fee**. Mirrors Blue Yonder Luminate (Luminate Platform priced separately from each module).

---

## 3. SCP-fork residue — inventory of what needs replacing

Per the PR-5.E audit, 15 files / 9,585 lines:

### 3.1 SCP-shape tactical heads (4 files, 1,995 lines) — REPLACE

| File | Lines | Current output | TMS replacement output |
|---|---:|---|---|
| `supply_planning_tgnn_service.py` | 449 | "supply exception probability", "lead time risk" | — (no TMS analogue) |
| `demand_planning_tgnn_service.py` | 472 | "demand forecasts", "bullwhip coefficient" | **Lane Volume Forecast tGNN** — lane-grain volume p50/p10/p90 per Strategic/Tactical/Execution tier |
| `inventory_optimization_tgnn_service.py` | 476 | "buffer adjustment signals", "stockout probability" | — (Capacity Buffer TRM covers the per-lane analogue; no GNN needed) |
| `capacity_rccp_tgnn_service.py` | 598 | "RCCP validation against MPS" | **Carrier Capacity tGNN** — per-lane carrier-capacity forecast with confidence; feeds CapacityPromise TRM |

**Result:** 2 of the 4 tactical heads become TMS-shape; the other 2 are deleted (no TMS analogue).

### 3.2 Plane-agnostic-ish heads (3 files, 1,691 lines) — RETARGET

| File | Lines | Disposition |
|---|---:|---|
| `sop_inference_service.py` | 685 | Retarget to carrier-portfolio S&OP (lane mix, dedicated vs broker split, equipment fleet sizing). Substrate (GraphSAGE) stays; output schema rewritten. |
| `execution_gnn_inference_service.py` | 486 | Retarget to **per-site dispatch readiness** — short-horizon predictions of tender acceptance, dock congestion, exception risk that feed L2 site coordinator. Replaces the SCP-shape "demand forecasts (short-term, daily)". |
| `gnn_orchestration_service.py` | 520 | Plane-agnostic; minimal rewrite to wire new TMS heads. |

### 3.3 Coordination + scheduling (4 files, 2,202 lines) — RETARGET

| File | Lines | Disposition |
|---|---:|---|
| `tactical_hive_coordinator.py` | 625 | Architecture diagram retargets SCP TRM names → TMS TRM names. Substrate (signal bus, coordination protocol) stays. |
| `joint_inventory_capacity_service.py` | 166 | Delete (no TMS joint-optimization analogue) or retarget to **joint carrier-capacity + equipment-balance optimization**. |
| `inter_hive_signal.py` | 197 | Plane-agnostic; promotes to Core substrate (`packages/powell-core`). |
| `relearning_jobs.py` | 1,214 | Retarget APScheduler jobs to new TMS heads + correct triggers (lane-volume drift, carrier-OTP drop, etc.). |

### 3.4 Training (3 files, 2,397 lines) — RETARGET

| File | Lines | Disposition |
|---|---:|---|
| `tactical_tgnn_training_service.py` | 616 | Retarget training loops to the 2 new TMS-shape heads (Lane Volume Forecast, Carrier Capacity). |
| `powell_training_service.py` | 1,256 | Substrate piece (training loop, checkpoint storage) promotes to Core; TMS-specific training-config registration stays here. |
| `generic_training_orchestrator.py` | 525 | Substrate; promotes to Core. |

### 3.5 Top-level orchestrator (1 file, 1,300 lines) — RETARGET

| File | Lines | Disposition |
|---|---:|---|
| `agent_orchestrator_service.py` | 1,300 | Architecture diagram refers to "S&OP GraphSAGE → Execution tGNN → Narrow TRMs" — the SCP planning cascade. Retarget to TMS cascade: carrier-portfolio S&OP → dispatch-readiness tGNN → 11 TMS TRMs. |

---

## 4. TMS-shape head designs

### 4.1 L4 Strategic — Carrier-Portfolio S&OP GraphSAGE

**Inputs (graph):**
- Nodes: lanes, sites, carriers, equipment archetypes
- Edges: contracted-lane assignments, alternate carriers, alternate ramps, equipment-flow routes
- Node features: lane volume P50, carrier capacity contracted, equipment fleet size, dock throughput, contract rate vs DAT benchmark
- Edge features: contract duration remaining, OTP percentage, recent-tender accept rate

**Outputs:**
- Per-lane: recommended carrier mix (% dedicated, % broker, % spot)
- Per-carrier: recommended contract action (renew, renegotiate, replace)
- Per-equipment-archetype: recommended fleet size delta
- Network-wide: aggregate carrier-cost forecast, peak-season capacity gap

**Cadence:** weekly Monday 06:00 UTC. Refresh on contract milestones (quarterly renegotiations).

**Substrate dependencies:** GraphSAGE node-classification + edge-regression heads; scenario sampler for "what-if contract loss" simulation.

### 4.2 L3 Tactical — Lane Volume Forecast tGNN

**Inputs:**
- Time-series: historical lane volume (Strategic monthly / Tactical weekly / Execution daily)
- Features: seasonal index, day-of-week pattern, customer-promo flags, weather forecast, lane reliability trend

**Outputs:**
- Per-lane × tier: P10 / P50 / P90 volume forecast for next H buckets
- Confidence band per forecast (conformal coverage)
- Trend signal (surge / drop / stable)

**Cadence:** daily 05:00 UTC. Refresh on demand-sensing signal arrival.

**Consumes:** outputs feed CapacityPromise TRM, CapacityBuffer TRM, FreightProcurement TRM tender-attempt distribution.

### 4.3 L3 Tactical — Carrier Capacity tGNN

**Inputs:**
- Per-(lane, carrier): contracted capacity, recent tender accept rate, OTP trailing 30/90/365 days
- Carrier scorecard: lane coverage, equipment specialisation, hazmat/temp certs
- Market signals: DAT spot vs contract, regional reject-rate index

**Outputs:**
- Per-(lane, carrier): probability of accepting next tender at contract rate
- Per-lane: aggregate available capacity over next 7 days (conformal P10/P90)
- Per-carrier: capacity-health score (composite OTP, accept rate, market position)

**Cadence:** daily 05:30 UTC.

**Consumes:** outputs feed CapacityPromise (carrier-availability gate), FreightProcurement (waterfall priority), BrokerRouting (escalation threshold).

### 4.4 L2 Operational — TMS Site Coordinator

**Inputs:** outputs from the 4 TMS-shape heads (4.1–4.3) + per-site live state (in-flight loads, dock queue, equipment counts, exception count).

**Architecture:** GATv2 + GRU per CLAUDE.md.

**Outputs:** urgency modulation signal for each of the 11 TMS TRMs at each site. AIIO mode hints (INFORM / INSPECT escalation triggers).

**Cadence:** hourly.

### 4.5 L1 TRMs — already shipped

All 11 TMS TRMs exist in `backend/app/services/powell/`. They have native reward weights (PR #62), curriculum-tuned synthetic state (PR #63), Phase-2 secondary teachers where applicable (PR #64), a live backtest scaffold (PR #65), and a twin-driven state sampler for CapacityPromise (PR #66). The GNN tier above feeds them context features at training and inference time.

---

## 5. Sequence — recommended rewrite path

Six staged PRs. Each is independently mergeable.

| Stage | Scope | Effort | Depends on |
|---|---|---|---|
| **GNN-1** | Disable SCP-shape APScheduler cron entries; verify nothing user-facing breaks (parallel to PR-5.E.2 in the audit) | 1 day | — |
| **GNN-2** | Strip SCP-shape provisioning steps; new tenants stop training SCP-shape models | 2 days | GNN-1 soaked 1 week |
| **GNN-3** | Build TMS-shape **Lane Volume Forecast tGNN** + training loop + first checkpoint per tenant | 2 weeks | Substrate extraction (Core-side) at least started |
| **GNN-4** | Build TMS-shape **Carrier Capacity tGNN** + training loop + first checkpoint | 2 weeks | GNN-3 pattern proven |
| **GNN-5** | Build TMS-shape **Carrier-Portfolio S&OP GraphSAGE** + weekly cadence | 3 weeks | GNN-3 + GNN-4 producing usable inputs |
| **GNN-6** | Build TMS **Site Coordinator** (L2) + tactical hive coordinator retarget; wire L4/L3 outputs into the 11 TRMs | 2 weeks | GNN-3 + GNN-4 + GNN-5 |

**Total elapsed:** ~3 months on TMS side, assuming substrate extraction lands in parallel on Core (msi-stealth's plate).

After GNN-6 lands, the residue from §3 is fully replaced. PR-5.E.4 (delete the 15 SCP-shape files) can then ship cleanly.

---

## 6. Core-side substrate contracts the rewrite needs

For the Core team to scope `POWELL_GNN_SUBSTRATE.md`, this rewrite expects the substrate to expose:

### 6.1 Training infrastructure
- A `GraphSAGEScaffolding` class — Core owns the architecture, node/edge protocols, message-passing layers. Plane code subclasses with a custom output head and a custom training-data adapter.
- A `tGNNScaffolding` class — Core owns the temporal convolution + attention + horizon-prediction trunk. Plane code provides output-head shape (regression / classification / quantile bands).
- A `CheckpointStorage` protocol — tenant-scoped path layout (`/{tenant_id}/{config_id}/gnn/{model_type}_{version}.pt`), atomic write/load, version pinning, rollback.
- A `TrainingLoop` class — handles data loading, batching, loss + optimiser, validation, early stopping, metrics emission.

### 6.2 Signal bus
- `HiveSignal` types (already partially extracted to Core per [MIGRATION_REGISTER §1.13](../../Autonomy-Core/docs/MIGRATION_REGISTER.md)).
- A `SignalBus` protocol — pub/sub between Powell tiers; plane code subscribes to specific topics.

### 6.3 Plane registry hooks
- A `PlaneRegistry` where TMS registers its set of {TRMs, GNN heads, site coordinator config}.
- A `SchedulerHook` API for plane-specific cadences (TMS daily 05:00 / 05:30 / weekly Monday 06:00).

### 6.4 Scenario sampler
- A `ScenarioSampler` Core class for what-if simulation (contract loss, carrier strike, weather closure) that GraphSAGE training and S&OP what-if both consume. Currently in Core's `ml.scenarios` per CLAUDE.md package layout — needs an interface that GNN heads can subscribe to.

### 6.5 What stays in TMS
- All output dataclasses (the SCP-shape ones get deleted; TMS-shape replace per §4)
- Training-data adapters that produce the right input tensors for each TMS-shape head
- Provisioning-step registrations
- Frontend admin pages (Phase B migration to Core per [MIGRATION_REGISTER §3.9](../../Autonomy-Core/docs/MIGRATION_REGISTER.md))

---

## 7. Frontend — deferred to Phase B substrate migration

Per the audit §3.4, 5 frontend admin pages currently UI the SCP-shape stack:

- `GraphSAGEDashboard.jsx` — retargets to carrier-portfolio S&OP view
- `PlanningHierarchyConfig.jsx` — retargets to lane / carrier hierarchy
- `ProvisioningStepper.jsx` — needs SCP-shape step rows pruned, TMS-shape steps added
- `AITrainingConfig.jsx` — knobs for the new TMS-shape heads
- `GNNDirectiveReview.jsx` — review surface for new TMS-shape directives

Per [MIGRATION_REGISTER §3.9](../../Autonomy-Core/docs/MIGRATION_REGISTER.md), admin frontend migrates to Core's `packages/autonomy-frontend/src/admin/` at Phase B. The rewrite above doesn't touch the frontend until that migration; today's pages keep working with the old backend until GNN-2 strips the steps that drive them.

---

## 8. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Substrate extraction (Core-side) takes longer than 3 months | Stage TMS heads as monolith-friendly first (keep `powell_training_service.py` plane-local); refactor against the substrate once it lands |
| TMS-shape training data unavailable until ERP extracts ship | Bootstrap from the twin (PR #66) for early training; switch to real data when extracts arrive |
| Disabling SCP-shape cron breaks something user-facing | GNN-1 is the park-not-delete stage; soak 1 week before GNN-2 |
| TMS L4 outputs require contract / fleet data the system doesn't yet store | Audit data-model gaps before GNN-5; extend masters via Alembic if needed |
| New heads' agreement with planner choice is low (vs SCP's 99.6 % on PO Creation) | Backtest scaffold (PR #65) is the gate — don't ship a head until it clears a per-TRM target on a real extract |

---

## 9. Open questions

1. **TMS-shape contract master** — do we have a `carrier_contract` ORM with rate, validity, OTP commitments, equipment specialisation? If not, GNN-5 (S&OP) needs that master first; flag as Alembic-blocker.
2. **DAT / FreightWaves licensing** — the L4 head reads market signals. Real-data integration requires a feed subscription. Bootstrap-prior version trains on synthetic until the subscription closes.
3. **Frontend ownership during transition** — admin pages live in TMS today, migrate to Core at Phase B. Question: do we freeze the current pages and ship new ones in Core, or evolve the current pages then move them?

---

## 10. Cross-references

- [TWIN_PR5E_POWELL_GNN_STACK_AUDIT.md](TWIN_PR5E_POWELL_GNN_STACK_AUDIT.md) — the audit that established the residue inventory and the build-vs-delete choice.
- [TWIN_REWRITE_PLAN.md](TWIN_REWRITE_PLAN.md) PR-5.E row — surgery-deferred status from 2026-05-10.
- [TMS_TRM_TRAINING_DATA_SPECIFICATION.md](TMS_TRM_TRAINING_DATA_SPECIFICATION.md) — what the L1 TRMs consume and how.
- [`Autonomy-Core/CLAUDE.md`](../../Autonomy-Core/CLAUDE.md) — Powell framework (Strategic / Tactical / Operational / Execution); placement rules.
- [`Autonomy-Core/docs/MIGRATION_REGISTER.md`](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) §1.13 (TMS Powell-substrate extraction), §3.9 (admin UI Phase B).
- *(TBD)* `Autonomy-Core/docs/architecture/POWELL_GNN_SUBSTRATE.md` — Core-side substrate-contract doc this design depends on. Assigned to msi-stealth.

---

*Last updated: 2026-05-11.*
