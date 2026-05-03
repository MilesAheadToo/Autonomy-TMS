# PR-5 Caller Migration Audit

**Status:** audit, 2026-05-03.
**Owner:** TMS team (acer-nitro session).
**Companion:** [TWIN_REWRITE_PLAN.md](TWIN_REWRITE_PLAN.md) PR-5 row — re-scopes
the original "delete `dag_simpy_simulator.py`" line into a concrete migration
plan with three sub-tickets.

---

## 1. Why this audit exists

[TWIN_REWRITE_PLAN.md](TWIN_REWRITE_PLAN.md) PR-5 was originally a one-line
bullet:

> Delete `dag_simpy_simulator.py`. Drop the feature flag. Update
> [PHASE_A_TWIN_AUDIT.md](PHASE_A_TWIN_AUDIT.md) status.

That framing assumed the legacy simulator had no live consumers. It has three:

1. [`backend/app/services/deployment_pipeline_service.py`](../backend/app/services/deployment_pipeline_service.py) (535 lines) — exposed via the live REST endpoint `POST /api/deployment/pipelines`.
2. [`backend/scripts/training/train_food_dist_models.py`](../backend/scripts/training/train_food_dist_models.py) (388 lines) — CLI script for offline training runs.
3. [`backend/app/services/simulation_data_converter.py`](../backend/app/services/simulation_data_converter.py) (738 lines) — consumed by both of the above to convert simulator output into NPZ + TRM-record training artefacts.

Deleting the simulator without touching its callers breaks the deployment
endpoint and the CLI. This doc characterises each caller, flags the
architectural mismatch with the new TMS twin, and proposes a re-scoped PR-5
as three sub-tickets.

---

## 2. The architectural mismatch — same pattern across all three callers

All three files are **SCP-fork residue**. They were copied wholesale when TMS
was forked from SCP and never updated to the TMS plane shape. The smoking gun
is in [`simulation_data_converter.py:35`](../backend/app/services/simulation_data_converter.py#L35) — the `TRM_TYPE_MAP`:

```python
TRM_TYPE_MAP = {
    "atp": "atp_executor",
    "order": "po_creation",
    "rebalance": "inventory_rebalancing",
    "exception": "order_tracking",
    "transfer_order": "to_execution",
    "mo_execution": "mo_execution",
    "quality": "quality_disposition",
    "maintenance": "maintenance_scheduling",
    "subcontracting": "subcontracting",
    "forecast_adjustment": "forecast_adjustment",
    "safety_stock": "safety_stock",
}
```

These 11 TRM names are the **SCP plane's** decision shapes (PO creation,
safety stock, ATP, MO execution, MRP rebalancing, …). None of them is a TMS
TRM. The actual TMS TRMs ([`backend/app/services/powell/`](../backend/app/services/powell/)) are 11 entirely different agents:

| TMS TRM | Decision shape |
|---|---|
| CapacityPromiseTRM | accept / defer / reject capacity commit |
| BrokerRoutingTRM | broker selection at premium |
| FreightProcurementTRM | carrier waterfall ordering |
| CapacityBufferTRM | buffer sizing |
| ShipmentTrackingTRM | ETA recompute + escalation |
| IntermodalTransferTRM | mode economics |
| LoadBuildTRM | line-item consolidation |
| DockSchedulingTRM | appointment + detention |
| EquipmentRepositionTRM | empty-mile reposition |
| ExceptionManagementTRM | exception recovery |
| DemandSensingTRM | shipment-rate sensing |

**Zero overlap with `TRM_TYPE_MAP`.** So `SimulationDataConverter` produces
training records in a shape no TMS TRM consumes. The `deployment_pipeline_service`
and `train_food_dist_models` pipelines run end-to-end without errors, but
their output (NPZ + TRM records) feeds models that don't exist on the TMS
side.

The same pattern holds for the GNN feature vector ([`simulation_data_converter.py:50–67`](../backend/app/services/simulation_data_converter.py#L50-L67)):

```python
GNN_FEATURES = [
    "on_hand", "backlog", "demand", "fulfilled", "in_transit",
    "safety_stock", "target_inventory", "order_placed",
    "is_supply", "is_demand", "is_inventory", "is_manufacturer",
    "topo_position", "num_downstream",
]
```

These are inventory-physics features. The TMS lane-flow simulator's feature
shape ([`observations.py`](../backend/app/services/digital_twin/observations.py)) is
`(in_flight_loads, arrivals_this_period, carrier_capacity_remaining,
equipment_available, dock_queue_depth, on_time_pct_trailing,
cost_per_load_trailing, season_sin, season_cos, seasonal_regime)`. Different
plane.

---

## 3. Caller-by-caller findings

### 3.1 `deployment_pipeline_service.py`

**What it does.** A 7-step orchestrator that drives a "demo system build"
end-to-end:

| Step | Action | Uses |
|---|---|---|
| 1 | Seed Config | `SupplyChainConfig` ORM + `food_dist_config_generator` |
| 2 | Deterministic sim | `DAGSimulator` ([`dag_simulator.py`](../backend/app/services/dag_simulator.py)) — note: NOT `dag_simpy_simulator.py` |
| **3** | **Stochastic Monte Carlo** | **`DAGSimPySimulator.run_monte_carlo`** |
| 4 | Convert | `SimulationDataConverter` |
| 5 | Train models | `PowellTrainingService` (S&OP GraphSAGE + tGNN + TRMs) |
| 6 | Day-1 CSV export | `SAPCSVExporter` |
| 7 | Day-2 CSV export | `SAPCSVExporter` |

**Inbound triggers.** The live REST endpoint
[`POST /api/deployment/pipelines`](../backend/app/api/endpoints/deployment.py#L127)
calls `DeploymentPipelineService(db=db, pipeline_id=pipeline_id).run()`. The
endpoint also exposes pipeline list / detail / cancel / CSV download routes
([`endpoints/deployment.py:169–329`](../backend/app/api/endpoints/deployment.py#L169-L329)).

**Outputs consumed by what.** The Step-5 trained models land at
`backend/checkpoints/supply_chain_configs/{slug}/sop_graphsage_{config_id}.pt`
and similar — these are **SCP-shape** GraphSAGE / tGNN checkpoints, not the
TMS lane-flow GNNs. Steps 6–7's SAP CSV exports are user-facing demo
artefacts (downloadable from the pipeline's `/csvs/{pipeline_id}/{csv_type}`
endpoint).

**Architectural verdict.** This service builds the **SCP** Food Distribution
demo, not anything in the TMS plane. The `transportation_*` tables it
populates via Step 6/7 CSVs are arguably TMS-shape, but the upstream
`run_monte_carlo` + `SimulationDataConverter` pipeline is purely
inventory-physics. **It is not a TMS training pipeline.**

### 3.2 `train_food_dist_models.py`

**What it does.** A CLI version of the same 6-step pipeline in
`deployment_pipeline_service.py` (without the deployment-bookkeeping wrapper).
Skips the SAP CSV exports; ships only Steps 1–6.

**Inbound triggers.** None — pure CLI:
```
python -m scripts.training.train_food_dist_models \
    --config-name "Food Distribution" --periods 52 \
    --monte-carlo-runs 128 --epochs 50
```

**Outputs consumed by what.** Same NPZ + TRM-record artefacts, written to
`backend/training_jobs/{slug}/`. The CLI is a manual-run alternative to
hitting the deployment endpoint; same SCP-shape models result.

**Architectural verdict.** Identical to 3.1 — SCP fork residue. The script
likely hasn't been run in current TMS work; the new TMS twin rewrite
(`digital_twin/` subpackage + lane-flow simulator) supersedes its purpose
entirely.

### 3.3 `simulation_data_converter.py`

**What it does.** Converts `SimulationResult` → `(GNN tensors, TRM records)`
in SCP shape:

- GNN: `X[B, T, N, F=14]` with inventory features; `A[2, N, N]` adjacency
  from shipments; `Y[B, N, H]` ordering-decision targets.
- TRM: per-decision records keyed by SCP TRM type (`atp` / `order` /
  `rebalance` / `exception` / `transfer_order` / `mo_execution` / `quality`
  / `maintenance` / `subcontracting` / `forecast_adjustment` /
  `safety_stock`).

**Inbound triggers.** Called from 3.1 and 3.2 only. No standalone usage.

**Outputs consumed by what.**
- Step 5 of both pipelines (`PowellTrainingService.train_sop_graphsage` +
  `train_execution_tgnn`) — **SCP-shape models that don't run against TMS
  data shapes**.
- TRM behavioural-cloning loop in `train_food_dist_models.py` (lines 248–
  301) — trains a generic 3-layer MLP per TRM type. Saved to disk; no live
  caller of the resulting checkpoints in TMS that this audit could find.
- DB inserts to `powell_site_agent_decisions` for "CDC feedback loop" —
  these rows would only be useful if the TMS Powell agents read SCP-shape
  decisions, which they don't.

**Architectural verdict.** Pure SCP-shape converter. Even the
`powell_site_agent_decisions` inserts feed nothing on the TMS side.

---

## 4. Inbound trigger graph (final)

```
POST /api/deployment/pipelines       (REST endpoint, deployment.py:127)
   ↓
DeploymentPipelineService.run        (Steps 1–7)
   ↓ (Step 3)
DAGSimPySimulator.run_monte_carlo    ← SCP-shape inventory physics
   ↓ (Step 4)
SimulationDataConverter              ← SCP-shape NPZ + TRM records
   ↓ (Step 5)
PowellTrainingService                ← SCP-shape models

CLI: python -m scripts.training.train_food_dist_models
   ↓ (same Steps 1–6 as above, no SAP CSV export)
DAGSimPySimulator.run_monte_carlo
   ↓
SimulationDataConverter
   ↓
PowellTrainingService
```

Both paths terminate at `PowellTrainingService.train_*` — which trains models
that no TMS agent reads. **The end-to-end pipeline is a no-op for TMS.**

The pipeline's only customer-facing artefact is the **SAP CSV export** at
Steps 6–7 (deployment-pipeline path only). Those exports are produced by
`SAPCSVExporter` (separate file, not audited here) — the relevant question
is whether the CSV exporter depends on artefacts produced by Steps 2–5 or
whether it can run standalone against the seeded `SupplyChainConfig`.

---

## 5. Re-scoped PR-5 — three sub-tickets

The original "PR-5: delete `dag_simpy_simulator.py`" was a one-PR bullet.
Replace with the following sequence:

### 5.A — Audit + decision (this PR)

This audit doc. Outputs:

- Caller-by-caller findings (above).
- Inbound trigger graph (above).
- Recommendation (below).

No code changes.

### 5.B — Cut the SCP-shape pipeline from TMS — **shipped 2026-05-03**

Plan finalised 2026-05-03 after the open questions below were resolved.
Landed by acer-nitro the same day:

1. **Deleted** [`backend/app/services/deployment_pipeline_service.py`](../backend/app/services/deployment_pipeline_service.py) (535 lines).
2. **Deleted** [`backend/scripts/training/train_food_dist_models.py`](../backend/scripts/training/train_food_dist_models.py) (388 lines).
3. **Deleted** [`backend/app/services/simulation_data_converter.py`](../backend/app/services/simulation_data_converter.py) (738 lines).
4. **Slimmed** [`backend/app/api/endpoints/deployment.py`](../backend/app/api/endpoints/deployment.py) from 369 lines → 117 lines: dropped `POST /pipelines`, `GET /pipelines`, `GET /pipelines/{id}`, `GET /pipelines/{id}/steps/{step}`, `POST /pipelines/{id}/cancel`, and the four Pydantic models / two private helpers (`PipelineCreateRequest`, `PipelineResponse`, `PipelineListResponse`, `_pipeline_to_response`, `_run_pipeline_background`) that only those routes used. **Kept** `GET /csvs/{pipeline_id}` and `GET /csvs/{pipeline_id}/{csv_type}` so SAP CSV downloads still work standalone (per Q2). Module docstring updated to record the cut.
5. **Kept** [`backend/app/models/deployment_pipeline.py`](../backend/app/models/deployment_pipeline.py) and the `deployment_pipeline_run` table — the `/csvs/...` endpoints look up by `pipeline_id` so the table stays as the audit / lookup index. (If a future PR proves the CSV endpoints can be parameterised on `config_id` instead, the table can drop then.)
6. **Did NOT delete** `PowellTrainingService.train_sop_graphsage` / `.train_execution_tgnn`. Pre-flight grep showed live callers in `scripts/train_powell_models.py`, `scripts/prepare_powell_demo.py`, `services/provisioning_service.py`, and `services/powell/generic_training_orchestrator.py` — the audit's earlier "called only from the deletees" assumption was wrong. Out of 5.B scope; revisit if those callers also turn out to be SCP-fork residue.
7. **Did NOT delete** [`backend/app/services/dag_simulator.py`](../backend/app/services/dag_simulator.py) — out of 5.B scope. Flagged for a follow-up audit.

Test impact: zero. No tests reference the deletees or the orchestration
endpoint surface. The full `tests/services/digital_twin/` suite stays
green (1 pre-existing main-branch failure unrelated to PR-5.B).

**Open questions — resolved 2026-05-03:**

1. **Frontend integration of `POST /api/deployment/pipelines`?** **None.** Verified by grep across `Autonomy-TMS/frontend/src`, `Autonomy-Core`, `Autonomy-DP`, and TMS-side docs / Makefiles / compose files / shell scripts: zero references to `/api/deployment/pipelines`, `DeploymentPipeline*`, or `deployment_pipeline*` outside of this audit and the audited callers themselves. (SCP-readonly has equivalents — its own copies, not TMS's concern.) → **Safe to delete the orchestration routes.**
2. **SAP CSV exports useful standalone?** **Yes** — confirmed; salvage `SAPCSVExporter` and the read-only CSV endpoints. The `DeploymentPipelineRun` ORM + table stay (the CSV endpoints look up by `pipeline_id`), but the **write-side** routes (`POST /pipelines`, list / detail / cancel) go.
3. **Non-TMS demos consuming the SCP-shape NPZ artefacts?** **No.** → Delete the artefacts cleanly; no salvage required.

### 5.C — Delete `dag_simpy_simulator.py`

After 5.B lands, the simulator has zero callers in `backend/app/services` and
`backend/scripts`. Delete:

1. [`backend/app/services/dag_simpy_simulator.py`](../backend/app/services/dag_simpy_simulator.py) (1,252 lines).
2. Any tests under `backend/tests/` that import it.
3. Update [`PHASE_A_TWIN_AUDIT.md`](PHASE_A_TWIN_AUDIT.md) to note the file is gone.

This is the trivial cleanup the original PR-5 framing assumed. It's only
trivial *after* 5.B unblocks it.

---

## 6. Recommendation

**Option A — deprecate-and-delete (recommended).**

The end-to-end pipeline is an SCP-shape no-op for TMS. The TMS twin rewrite
(`digital_twin/` + 7 physics models + line-item shipment generator + observation
features) already supersedes the inventory-physics path with a TMS-shape
training surface. Keeping the old pipeline around adds:

- Maintenance burden (every Core pin bump risks breaking SCP-shape callers
  that produce no TMS value).
- Confusion for future contributors who see "deployment pipeline" and assume
  it's the live TMS deployment path.
- Drift risk — every time someone reads a `Step 3 stochastic Monte Carlo`
  reference and assumes it's TMS, the architectural placement rule from
  `CLAUDE.md` is silently undermined.

The migration is **not** about porting these files to use `LaneFlowSimulator`
— there's no demand on the consumer side for what they produce in a TMS
context. It's about ripping out an entire SCP-fork stack that never got
re-shaped after the fork.

**Option B — surgical replacement (rejected).**

Build a `lane_flow_data_converter` matching the 11 TMS TRMs and a
`tms_deployment_pipeline_service` driving `LaneFlowSimulator` rollouts. Keeps
the deployment endpoint stable. **Rejected because:** there is no
demonstrated TMS demand for "demo system build via REST endpoint". The
training surface for TMS RL is the `LaneFlowSimulator` rollout loop directly
(callable from any training driver), not a 7-step pipeline ORM-state-machine.
Building the replacement before the consumer demand is real is premature
abstraction.

**Option C — hybrid / freeze in place (rejected).**

Mark the files as legacy / not-TMS-shape without deleting. Defer until "we
have time." **Rejected because:** the audit's whole point is to convert
implicit dead code into either explicit dead code (delete) or actively
maintained code (rewrite). Letting it sit perpetuates the SCP-fork drift.

---

## 7. Status of `dag_simpy_simulator.py` itself

Only the three callers in §3 reach `dag_simpy_simulator.py` from TMS code.
After 5.B lands, deletion (5.C) is mechanical. No conditional logic to
unwind, no feature flags to drop, no plumbing to refactor — the file becomes
unreferenced.

---

## 8. Cross-references

- [TWIN_REWRITE_PLAN.md](TWIN_REWRITE_PLAN.md) — overall PR sequence; PR-5 row updates after this audit lands.
- [TMS_TWIN_PHYSICS_DESIGN.md](TMS_TWIN_PHYSICS_DESIGN.md) — TMS-shape physics model catalog (what replaces the inventory simulator).
- [PHASE_A_TWIN_AUDIT.md](PHASE_A_TWIN_AUDIT.md) — original `dag_simpy_simulator.py` audit (superseded by the rewrite plan).
- Autonomy-Core CLAUDE.md "Plane-module invariant" — placement rule the SCP-fork residue silently violates.

---

*Last updated: 2026-05-03.*
