# TMS Twin Rewrite Plan

**Status:** active, started 2026-05-01.
**Owner:** TMS team (acer-nitro session).
**Supersedes:** [PHASE_A_TWIN_AUDIT.md](PHASE_A_TWIN_AUDIT.md) — its "extend
`dag_simpy_simulator.py` with `step` + action injection" roadmap is dropped
in favour of replacing the simulator entirely with two new files (lane-flow
simulator + shipment generator).
**Architecture source:** [Autonomy-Core/docs/architecture/TWIN_AND_ENVELOPES.md](../../Autonomy-Core/docs/architecture/TWIN_AND_ENVELOPES.md).
The cross-plane structure (generator/simulator split, three-phase implementation
ladder, canonical envelope contracts) is decided in Core; this doc is the
TMS-side execution plan only.

---

## 1. Why we're rewriting

[backend/app/services/dag_simpy_simulator.py](../backend/app/services/dag_simpy_simulator.py)
is a 1,252-line near-clone of SCP's inventory simulator. It models on-hand,
in-transit, backlog, safety stock, BOM explosion, and supplier reliability —
none of which the TMS plane decides over. TMS decides over carrier movement:
load assignment, dispatch timing, equipment balance, dock scheduling.

Per [TWIN_AND_ENVELOPES.md §3](../../Autonomy-Core/docs/architecture/TWIN_AND_ENVELOPES.md),
TMS's twin state node is `(lane × carrier × equipment × hour-or-day)`. Its
action is load assignment + carrier + dock + reposition. Its physics is HOS,
dock slots, carrier capacity, equipment flow. None of that is in the existing
simulator and bolting it on top of inventory physics is the wrong factoring.

## 2. The new shape

Per [TWIN_AND_ENVELOPES.md §4](../../Autonomy-Core/docs/architecture/TWIN_AND_ENVELOPES.md):

| Artefact | File | Owns |
|---|---|---|
| **Shipment generator** | `backend/app/services/digital_twin/shipment_generator.py` | Exogenous `TransferOrderEnvelope` arrival stream — line items per (origin, destination, product, bucket). |
| **Lane-flow simulator** | `backend/app/services/digital_twin/lane_flow_simulator.py` | Transition function over generator output: lane queues, dock dynamics, carrier capacity, equipment flow. |
| **Step adapter** | `backend/app/services/digital_twin/step_adapter.py` | `(observe, step, record)` façade producing RL trajectories — parallel to Core's `TwinStepAdapter`. |
| **Observation / Action / Reward** | `backend/app/services/digital_twin/observations.py` | TMS-shaped dataclasses. Will migrate to subclass Core's `TwinObservation` once Core slims it (Core PR per TWIN_AND_ENVELOPES.md §9 bullet 3 — not blocking). |

Schemas (the `TransferOrder` and `TransferOrderLineItem` shape) stay in Core
(`azirella-data-model`). The cross-plane envelope contract package
(`azirella-transfer-order-envelope-contract`) is also Core. This product repo
owns generator + simulator only.

## 3. Load Builder — where it gets material data

The Load Builder agent decides at line-item grain (SKU, weight, volume,
hazmat, due date). It needs to see what shipments arrived to be loaded — but
**not** how they got reordered upstream.

`shipment_generator.py` is exactly that source. It emits
`TransferOrderEnvelope` rows (line-item-grain) under the three-phase ladder:

- **Phase 1 (PR-2):** parametric stochastic stub — broad coverage, no upstream
  dependency. Lane mix, mode mix, weight/volume distributions, weekly cadence.
- **Phase 2 (PR-6):** parameters fitted to the tenant's `TransferOrderLineItem`
  history. Realistic for that tenant.
- **Phase 3 (later):** SCP's producer registers when SCP+TMS deployed jointly.
  TMS code is unchanged.

The lane-flow simulator never simulates inventory; it consumes whatever the
shipment generator emits.

## 4. PR sequence

| # | Scope | Behaviour change |
|---|---|---|
| **PR-1** | Scaffolding: subpackage `digital_twin/`, dataclasses, generator + simulator shells, Core pin bump. Phase-1 stub returns an empty envelope (zero rows) to wire the contract end-to-end. | None. `dag_simpy_simulator.py` still in place and unmodified. |
| **PR-2** | Phase-1 parametric `shipment_generator.py`: lane/mode mix, weight/volume distributions, weekly cadence, seasonal volume modulation. Adopts Core's `SeasonalEnvelope` ([MIGRATION_REGISTER §3.20](../../Autonomy-Core/docs/MIGRATION_REGISTER.md)) on the generator side. | New: shipment events flow. Verify Load Builder consumes them. |
| **PR-3** | Phase-1 `lane_flow_simulator.py`: lane queues, dock dynamics, carrier capacity, equipment flow. Action injection for load build / carrier / dispatch / reposition. Per-step reward (OTD%, cost-per-load, dock utilisation, equipment balance). **Staged into 3.A–3.G** per [TMS_TWIN_PHYSICS_DESIGN.md §4](TMS_TWIN_PHYSICS_DESIGN.md). | New: end-to-end RL training loop runs. Old simulator still callable behind a flag. |
| ↳ **PR-3.A** | **Carrier Acceptance** physics (§4.1) — feature-flagged on `LaneFlowSimulator` via `carrier_acceptance_model=` kwarg. Bootstrap-prior logistic with contract / spot / premium / tightness adjustments. Unblocks 4 TRMs: CapacityPromise, BrokerRouting, FreightProcurement, CapacityBuffer. **Shipped 2026-05-03.** | Default behaviour preserved (no model attached → all-tenders-accepted-within-capacity). Opt-in path adds `tender_rejected` events carrying `reason_code` + `p_accept`. |
| ↳ **PR-3.B** | **Lane Transit** physics (§4.2) — feature-flagged on `LaneFlowSimulator` via `lane_transit_model=` kwarg. Lognormal draw centred on the existing static `transit_buckets`, modulated by season (±10%) + scenario weather (0–25%). Conformal P10/P90 bands surfaced on `tender_accepted` outcome events. Unblocks 3 TRMs: ShipmentTracking (ETA bounds), CapacityPromise (deadline-feasibility), IntermodalTransfer (mode-economics). **Shipped 2026-05-03.** | Default behaviour preserved (no model attached → static `transit_buckets` for every load, legacy). |
| ↳ **PR-3.C** | **Dock Queue** dwell-time physics (§4.3) — feature-flagged on `LaneFlowSimulator` via `dock_queue_model=` kwarg. Stateless Gamma-distributed dwell per appointment type (live_load α=2 β=45, live_unload α=2 β=30, drop_hook α=3 β=15) plus detention math. `shipment_delivered` outcome events carry `dwell_minutes` + `detention_cost` for DockScheduling TRM training. **Shipped 2026-05-03.** Full per-facility queue-state machine deferred until DockScheduling RL needs it. | Default behaviour preserved. |
| ↳ **PR-3.D** | **Equipment Flow** physics (§4.4) — feature-flagged via `equipment_flow_model=` kwarg. Per-site availability tracker; initial-count formula = `floor(avg_daily_loads × 1.5)`. Auto-registers sites; mirrors the simulator's `equipment_available` counter into per-site balances on dispatch + arrival. Observation-only in Phase 1 — dispatch still gated on the legacy counter; full delegation lands when EquipmentReposition RL needs it. **Shipped 2026-05-03.** | Default behaviour preserved. |
| ↳ **PR-3.E** | **Spot-Rate Market** physics (§4.5) — feature-flagged via `spot_rate_model=` kwarg. AR(1) spot-rate-and-tightness recursion (α=0.85, β=0.7, κ=0.4) with seasonal drift (peak Q4, trough Q1) and shock injection from scenario disruptions. `tender_accepted` events carry `spot_rate`, `spot_tightness`, `spot_season_factor`, `spot_epsilon`, `spot_premium_vs_contract` for BrokerRouting / CapacityBuffer / IntermodalTransfer reward attribution. **Shipped 2026-05-03.** | Default behaviour preserved. Spot ↔ CarrierAcceptance plumbing (the model produces `tightness` that PR-3.A's CarrierAcceptance reads) deferred to a follow-up. |
| ↳ **PR-3.F** | **Exception Generator** physics (§4.6) — feature-flagged on `LaneFlowSimulator` via `exception_model=` kwarg. Per-load Bernoulli at dispatch (λ=0.05) + multinomial kind (delay/damage/miss/refused) + multinomial severity (recoverable_no_cost / recoverable_expedite / miss_sla_penalty) + recovery cost. Emits `shipment_exception` OutcomeEvents and decorates arrival events with the same metadata. Unblocks 2 TRMs: ExceptionManagement (entire decision space) and ShipmentTracking (exception triggers ETA recompute). **Shipped 2026-05-03.** | Default behaviour preserved (no model attached → no `shipment_exception` events emitted). PLAN_PRODUCTION mode never fires (twin invariant). |
| ↳ **PR-3.G** | **Intermodal Ramp** physics (§4.7) + **Tracking Event Generator** (§4.8) — bundled because both are standalone Phase-1 evaluators (single-TRM consumer each, no `LaneFlowSimulator` wiring). `IntermodalRampModel`: ramp acceptance under congestion, rail-transit lognormal × 1.5 truck × season modulation, four-component cost decomposition (drayage / ramp / rail / ramp / drayage). `TrackingEventModel`: per-shipment event-grid emission with carrier-tier-driven cadence (premium 4hr / budget 12hr), N(0, 30 min) jitter, 2 % drop rate. Unblocks 2 TRMs: IntermodalTransfer (mode economics) + ShipmentTracking (event-stream features). **Shipped 2026-05-03.** | Standalone — TRMs consume the models directly. No simulator-wiring change; default behaviour preserved. |
| **PR-4** | `SeasonalEnvelope` Phase-C adoption — sin/cos period features in `LaneFlowObservation`, regime classification when an envelope is registered, end-to-end stratification via Core's `stratified_start_dates`. **Shipped 2026-05-03.** | `LaneFlowObservation` gains `season_sin` / `season_cos` (always populated; `(0, 0)` when no calendar anchor) + optional `seasonal_regime` (populated when `seasonal_envelope=` kwarg is supplied to `LaneFlowSimulator`). Closes the §3.20 TMS-Phase-C deferral in `CONSUMER_ADOPTION_LOG`. |
| **PR-5** | **Re-scoped 2026-05-03** per [TWIN_PR5_CALLER_MIGRATION_AUDIT.md](TWIN_PR5_CALLER_MIGRATION_AUDIT.md). Three callers identified — all SCP-fork residue producing training records no TMS TRM consumes. Sub-tickets: **5.A** audit (this row's link), **5.B** delete `deployment_pipeline_service.py` + `train_food_dist_models.py` + `simulation_data_converter.py` + dependent endpoints, **5.C** delete `dag_simpy_simulator.py`. Pending user confirmation before 5.B lands. | None until 5.B. After 5.B + 5.C: SCP-shape pipeline gone; only TMS-shape `digital_twin/` remains. |
| ↳ **PR-5.D** | Audit doc for `dag_simulator.py` (the deterministic SCP simulator the parent audit flagged as out-of-scope). Per [TWIN_PR5D_DAG_SIMULATOR_AUDIT.md](TWIN_PR5D_DAG_SIMULATOR_AUDIT.md): ~1,400 of 1,671 lines are unreferenced post-PR-5.C; only `load_topology` (+ helpers) is live. Recommends extracting `topology_loader.py` and deleting the simulator class as sub-tickets 5.D-i / 5.D-ii / 5.D-iii after PR-5.C merges. **Audit shipped 2026-05-04; surgery deferred (and possibly subsumed by PR-5.E.6).** | None (doc-only). |
| ↳ **PR-5.E** | Audit Powell GNN inference / training stack — the SCP-shape **inference / serving** path PR-5.A undersold. Per [TWIN_PR5E_POWELL_GNN_STACK_AUDIT.md](TWIN_PR5E_POWELL_GNN_STACK_AUDIT.md): **15 files, 9,585 lines, zero TMS TRM consumers.** SCP-shape decision schema (RCCP, MPS, bullwhip, stockout) hard-coded into output dataclasses; the SCP→TMS step-label rebadge in `tms_provisioning_adapter.py` was never followed up by an underlying-service rewrite. Recommends staged delete (5.E.1 audit / 5.E.2 disable cron / 5.E.3 strip provisioning steps / 5.E.4 delete services / 5.E.5 frontend cleanup / 5.E.6 fold dag_simulator delete). **Audit shipped 2026-05-04; surgery pending two open questions to user.** | None (doc-only). |
| **PR-6** | Phase-2 calibration — `phase2_fitter.fit_phase2_shipment_generator(history)` walks tenant `TransferOrder` + `TransferOrderLineItem` history and returns a `Phase1ShipmentGenerator` configured with fitted `candidate_lanes` / `candidate_products` / `candidate_units` / `base_volumes` / `seasonal_envelopes` / `product_unit_overrides`. Stamps `tms:to_arrival_phase2:v0.1.0` on the producer signature so downstream consumers can route between Phase-1 and Phase-2 envelopes. Async `load_history_for_config` is the thin DB-side wrapper. **Shipped 2026-05-03.** | Tenant-realistic training. Lanes with ≥1.5 annual cycles get fitted seasonal envelopes; sparse channels fall through to defaults. |
| ↳ **PR-6b** | Provisioning wiring — sync companions (`load_history_for_config_sync`, `fit_phase2_for_config`, `load_phase2_generator_for_config`) plus best-effort call from `_step_training_corpus` (mirrors SCP's `seasonal_envelope_service.fit_seasonal_envelopes_for_config` pattern). Idempotent upsert via Core's `SeasonalEnvelopeRecord`. **Shipped 2026-05-04.** | Twin-driven simulation augmentation downstream of training-corpus generation now uses tenant-fitted shipment shape; fit failures log a warning and fall through to the bootstrap generator. |

Phase-3 producer coupling is not in TMS scope. When SCP ships its producer,
TMS swaps the registered provider with no code change.

## 5. Core dependencies adopted

PR-1 bumps the Core pin (`30549c4` → `08c6429`) and adds two new package
pins:

- `azirella-transfer-order-envelope-contract` — the `TransferOrderEnvelope`
  schema TMS produces under the producer signature `tms:to_arrival_stub:v0.1.0`.
- `azirella-demand-planning-contract` — transitive dep of the contract package
  (carries the shared `Tier` enum).

Optional adoption (per [Core CONSUMER_ADOPTION_LOG](../../Autonomy-Core/docs/CONSUMER_ADOPTION_LOG.md)
2026-05-01 entries): `DisruptionKind` extensions (`CARRIER_STRIKE`,
`DOCK_CONGESTION`, `WEATHER_EVENT`) and `ScenarioSampler.product_scopes=("tms",)`.
Wired in PR-3 alongside the simulator.

## 6. Cross-references

- [TWIN_AND_ENVELOPES.md](../../Autonomy-Core/docs/architecture/TWIN_AND_ENVELOPES.md) — architecture source of truth.
- [DIGITAL_TWIN.md](../../Autonomy-Core/docs/architecture/DIGITAL_TWIN.md) — twin invariants (training, not planning).
- [SEASONAL_ENVELOPE.md](../../Autonomy-Core/docs/architecture/SEASONAL_ENVELOPE.md) — substrate adopted in PR-4.
- [MIGRATION_REGISTER §2.2](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — twin placement decision.
- [MIGRATION_REGISTER §3.20](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — seasonal envelope (TMS Phase-C).
- [PHASE_A_TWIN_AUDIT.md](PHASE_A_TWIN_AUDIT.md) — superseded.
