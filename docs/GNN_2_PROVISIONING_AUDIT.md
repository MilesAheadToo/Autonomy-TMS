# GNN-2 Provisioning Audit — strip SCP-shape steps from the runner

**Status:** audit (no code change), 2026-05-11.
**Owner:** TMS team (acer-nitro session).
**Companion:** [TMS_POWELL_GNN_REWRITE.md](TMS_POWELL_GNN_REWRITE.md) — GNN-2 stage of the rewrite sequence.

---

## 1. What this audit is

Per `TMS_POWELL_GNN_REWRITE.md §5`, **GNN-2** strips the SCP-shape provisioning steps so new tenants stop training SCP-shape models. GNN-1 closed the inputs (cron + REST trigger); GNN-2 closes the provisioning side. After GNN-1 + GNN-2, no path from a new tenant's onboarding flows to the SCP-shape GraphSAGE / tGNN training services.

The Core `ConfigProvisioningStatus.STEPS` list has **19 entries**. This audit walks each step, identifies the handler in [`backend/app/services/provisioning_service.py`](../backend/app/services/provisioning_service.py), and lands a disposition for the GNN-2 PR that follows this audit.

---

## 2. Provisioning step inventory + disposition

| # | Step key | Handler present | Shape | Disposition | Why |
|---|---|---|---|---|---|
| 1 | `warm_start` | ✅ `_step_warm_start` | plane-agnostic | **KEEP** | Historical data prep, no SCP-shape decision schema. |
| 2 | `market_intelligence` | ❌ no handler | (Core-added 2026-05-11) | **KEEP-CORE** | New Core step; SCP added a handler for it. TMS no-ops via the runner's missing-handler fallback until acer-nitro picks it up. |
| 3 | `training_corpus` | ✅ `_step_training_corpus` | TMS-shape | **KEEP** | The TMS twin's training-corpus generator (PRs #62-#69). |
| 4 | `sop_graphsage` | ✅ `_step_sop_graphsage` + `_bg` variant | **SCP-shape** | **STRIP** (GNN-2 PR) | Calls `GenericTrainingOrchestrator.train_sop_graphsage()` → `GNNOrchestrationService.run_full_cycle()` → `SOPInferenceService.analyze_network()` which produces SCP-shape network embeddings (criticality / bottleneck risk / resilience). No TMS consumer of the directives broadcast by the cycle. |
| 5 | `cfa_optimization` | ✅ `_step_cfa_optimization` | TMS-shape | **KEEP** | Policy-parameter DE optimisation over scenarios; plane-agnostic by design. |
| 6 | `lgbm_forecast` | ✅ `_step_lgbm_forecast` | plane-agnostic | **KEEP** | LightGBM forecast layer; TMS uses it for lane-volume context per project memory. |
| 7 | `demand_features` | ❓ no top-level handler — likely uses `_step_lgbm_forecast` follow-on | TMS-shape | **KEEP** | Feature engineering; no SCP-shape decision schema. |
| 8 | `supply_tgnn` | ❌ deleted by PR-5.E (2026-05-05) | SCP-shape | **STAYS NO-OP** | Handler removed; runner's missing-handler fallback returns a placeholder. STEPS list still in Core; whether to drop the key is §1.17's plate. |
| 9 | `inventory_tgnn` | ❌ deleted by PR-5.E | SCP-shape | **STAYS NO-OP** | As above. |
| 10 | `capacity_tgnn` | ❌ deleted by PR-5.E | SCP-shape | **STAYS NO-OP** | As above. |
| 11 | `trm_load_pretrained` | ✅ `_step_trm_load_pretrained` + `_bg` | TMS-shape | **KEEP** | Loads TMS TRM BC checkpoints. PR #62-#66 land the TRM-side foundation. |
| 12 | `backtest_evaluation` | ✅ `_step_backtest_evaluation` | plane-agnostic | **KEEP** | Plane-agnostic backtest harness; PR #65's live-backtest scaffold is the TMS-shape consumer. |
| 13 | `supply_plan` | ✅ `_step_supply_plan` | naming SCP-shape, **data TMS-shape** | **KEEP-RENAME-FOLLOWUP** | Writes to `supply_plan` table. Six TMS-side consumers read from it (decision_chart, pegging_gantt, resource_heatmap, planning_cascade/sop_service, confidence_funnel, assistant_tools). The table is in active TMS use as plan-of-record despite the SCP-vestigial name. Rename to `transportation_plan_*` or split into a TMS-shape table is a separate workstream — DO NOT strip the handler here. |
| 14 | `rccp_validation` | ✅ `_step_rccp_validation` | naming SCP-shape, **referenced by TMS decision-stream + planning skills** | **KEEP-RENAME-FOLLOWUP** | `decision_stream_service.py` and `inventory_planning` skill reference `rccp_adjustment` as a decision class. "RCCP" terminology is SCP; the validation outputs are consumed by TMS lifecycle paths. Rename + rescope to "carrier capacity validation" is a separate workstream — DO NOT strip the handler here. |
| 15 | `decision_seed` | ✅ `_step_decision_seed` | TMS-shape | **KEEP** | Seeds the Decision Stream with initial TMS-shape decisions; depends on `backtest_evaluation`. |
| 16 | `site_tgnn` | ✅ `_step_site_tgnn` + `_bg` | **TMS-shape** | **KEEP** | Wraps `MultiTRMCoordinationOracle` over the **11 TMS TRMs** (CapacityPromise, BrokerRouting, …) and trains the per-site coordinator. This is the L2 (Operational) layer of the Powell hierarchy — exactly the GNN-6 target. The SCP-vestigial naming (`site_tgnn`) is misleading; the implementation is TMS-shape. GNN-6 retargets the trunk to `tGNNScaffolding` from `azirella-powell-core`. |
| 17 | `conformal` | ✅ `_step_conformal` | plane-agnostic | **KEEP** | Tenant-scoped CDT calibration. |
| 18 | `scenario_bootstrap` | ✅ `_step_scenario_bootstrap` + `_bg` | plane-agnostic | **KEEP** | Scenario skill warm-start. |
| 19 | `briefing` | ✅ `_step_briefing` | plane-agnostic | **KEEP** | Executive briefing generation. |

**Net GNN-2 deletions identified:** **2 handlers** — `_step_sop_graphsage` + `_step_sop_graphsage_bg` (lines 837–848 + 2620–2630 of `provisioning_service.py`).

---

## 3. What stays for §1.17 (plane-licensing) to clean up

The four `*_tgnn` keys still in Core's `STEPS` list (`supply_tgnn`, `inventory_tgnn`, `capacity_tgnn`, plus future `demand_tgnn` if it returns) have **no TMS handler** but the keys remain. The runner's missing-handler fallback emits a placeholder result that flows into the provisioning UI as a successful no-op row. This is wasteful but not wrong.

The proper fix is `MIGRATION_REGISTER §1.17 Phase 1`: add `required_planes` annotation to each `STEPS` entry in Core; the step runner skips entries whose plane set isn't licensed for the current tenant. That's msi-stealth's plate; once it lands, acer-nitro's Phase 3 work threads `@requires_plane(Plane.TMS)` decorators through the handlers that remain after this GNN-2 strip.

---

## 4. GNN-2 PR scope (next step after this audit)

Single-file change: `backend/app/services/provisioning_service.py`.

1. Delete `_step_sop_graphsage` (lines 837–848).
2. Delete `_step_sop_graphsage_bg` (lines 2620–2630).
3. Add a brief comment in their place, mirroring the PR-5.E pattern at line 1443:

   > "`_step_sop_graphsage` and its `_bg` variant deleted in GNN-2 (2026-05-XX) — they trained SCP-shape S&OP via `GNNOrchestrationService` whose directives no TMS code consumes. The runner returns a placeholder when no handler exists for a step key (see `_step_runner` ~line 558). TMS-shape replacement lands in **GNN-5** (Carrier-Portfolio S&OP GraphSAGE)."

4. Optionally: audit `train_sop_graphsage` / `train_execution_tgnn` callers in `GenericTrainingOrchestrator` — once `_step_sop_graphsage` is gone, the orchestrator method has only `train_all()` as a caller. `train_all()` could either be retargeted (drop the `train_sop_graphsage` call) or left until GNN-5 retargets the S&OP path entirely. **Recommendation: defer the orchestrator audit to GNN-5** — the method has no consumers after the step is gone but harmless to leave.

**Out of scope for GNN-2:**

- `supply_plan` rename (active TMS table; cross-cutting rename is its own workstream)
- `rccp_validation` rename (active TMS decision-stream class; same)
- Adding `required_planes` to `STEPS` (§1.17 Phase 1, Core's plate)
- `site_tgnn` retarget to `azirella-powell-core` (GNN-6)
- Sequential per-step removal of the SCP-shape no-op keys (`supply_tgnn`, `inventory_tgnn`, `capacity_tgnn`) — §1.17's gating supersedes the need to delete them outright

---

## 5. Soak guidance

The rewrite plan said "GNN-2 lands after GNN-1 has soaked 1 week" — meaning we should observe the disabled cron + 410 endpoint produce no user complaints before stripping the provisioning side.

**GNN-1 status:** Daily APScheduler entry disabled 2026-05-05 (6 days as of 2026-05-11); manual REST endpoint disabled 2026-05-11 (today). The cron has soaked >6 days; the endpoint hasn't.

**Recommendation:** Wait at least 1 day post-merge of PR #70 (the 410 endpoint disable) before landing GNN-2. The endpoint isn't expected to be hit, but a 1-day buffer catches any forgotten dev / demo workflow that touched it.

---

## 6. Cross-references

- [TMS_POWELL_GNN_REWRITE.md](TMS_POWELL_GNN_REWRITE.md) — overall rewrite sequence + Option-C decision.
- [TWIN_PR5E_POWELL_GNN_STACK_AUDIT.md](TWIN_PR5E_POWELL_GNN_STACK_AUDIT.md) — original audit; inventory of files already deleted by PR #58.
- [`Autonomy-Core/docs/MIGRATION_REGISTER.md §1.17`](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — plane-licensing awareness, supersedes the manual `required_planes` annotation work this audit notes.
- [`Autonomy-Core/docs/architecture/POWELL_GNN_SUBSTRATE.md`](../../Autonomy-Core/docs/architecture/POWELL_GNN_SUBSTRATE.md) — substrate contracts the GNN-3 / GNN-4 / GNN-5 / GNN-6 work depends on (now shipped).

---

*Last updated: 2026-05-11.*
