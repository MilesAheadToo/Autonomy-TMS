# Phase A — TMS Digital Twin Audit + Implementation Roadmap

**Status:** Audit, 2026-04-27.
**Owner:** TMS team (acer-nitro session per TMS_TIER3_FIRST_PLAN §3).
**Critical path for:** L2 Phase 3b (PPO fine-tune of GATv2+GRU
Terminal Coordinator), L1 TRM RL fine-tuning beyond BC warmup.

---

## 1. Why this matters

Every "trained" agent we ship today is a *heuristic-imitator* (BC
warmup against the deterministic teacher, capped at the teacher's
quality). Real outperformance requires PPO fine-tuning against
trajectory rollouts; PPO needs an action-conditioned step+reward
loop. That's what the digital twin provides.

L2 Phase 3a (BC warmup) doesn't strictly need the twin — `hub_hour_
snapshot` rows from live ops + heuristic-teacher labels are enough.
But Phase 3b (PPO) is genuinely blocked. Same for any future RL
fine-tune of L1 TRMs beyond their initial BC checkpoint.

Without Phase A, the ceiling on TMS agent quality is the heuristic
teacher's quality. With Phase A, the ceiling is the twin's fidelity.

---

## 2. What exists today

### 2.1 `app/services/dag_simpy_simulator.py` (1252 lines)

Full SimPy stochastic simulator:

  * `DAGSimPySimulator.run_monte_carlo(num_runs, num_periods, seed)` —
    Monte Carlo over N runs × P periods. Used to generate warm-start
    training data for AI models.
  * `simulate(...)` — one full run end-to-end (no agent-in-loop).
  * Per-period processes: `weekly_process` / `_process_inventory_site
    _stochastic` / `_ship_downstream_stochastic` / `_place_order_
    upstream_stochastic` / `_track_orders_stochastic` / `_apply_
    disruptions`.
  * Stochastic samplers: `_sample_demand` (P10/P50/P90 from forecast),
    `_sample_lead_time` (triangular/normal per VendorLeadTime CV),
    `_apply_disruptions` (random capacity hits + supplier reliability
    fails).
  * KPI rollup at end of run: `_compute_kpis` / `_aggregate_kpis`.

**What's good:** the stochastic shape is rich, the DAG topology is
production-faithful, the seeded RNG gives determinism.

**What's missing for Phase 3b:**
  * No `step(state, action)` API — runs autonomously, doesn't admit
    agent-in-the-loop.
  * KPIs computed at end of run, not per-step. PPO reward signal
    needs per-step computation.
  * Product-flow oriented. Hub-hour state extraction (per-dock,
    per-lane, per-equipment view) needs to be built on top.

### 2.2 `app/services/simulation_execution_engine.py` (866 lines)

Round-by-round customer-order simulation:

  * `execute_round(scenario, round_number)` — one period step.
  * Orchestrates: receive_shipments → generate_customer_orders →
    fulfill_orders → evaluate_replenishment → calculate_metrics.
  * Async (FastAPI scenario-engine driver).

**What's good:** has a clean per-step structure. `execute_round`
already returns metrics, which is most of what PPO needs.

**What's missing:**
  * Action injection point. The replenishment / fulfilment decisions
    are heuristic-driven; PPO needs to inject agent actions instead.
  * Hub-hour resolution (currently round = period, not hour-aligned).
  * Reward function (KPIs → scalar reward).

### 2.3 `app/services/sc_execution/simulation_executor.py` (479 lines)

Lower-level simulation execution. Smaller surface; mostly a sub-
component of the engine above.

### 2.4 `app/services/scenario_evaluation_service.py`

Scenario branch evaluation — already supports running multiple
scenarios in parallel and comparing outcomes. Useful for the PPO
"rollout multiple episodes per update" pattern.

---

## 3. What Phase A must deliver

### 3.1 `TwinStepInterface` — agent-in-the-loop API

```python
class TwinStepInterface:
    def reset(self, scenario_seed: int, tenant_id: int, config_id: int) -> HubState:
        """Initialise twin to t=0, return initial hub-hour state."""

    def step(self, action: AgentAction) -> Tuple[HubState, float, bool, Dict]:
        """Apply agent action, advance one hour, return:
          (next_state, reward, done, info_dict)."""

    def hub_hour_state(self) -> HubState:
        """Snapshot current state as hub_hour_snapshot.node_features
        + edge_features shape — same as production extractor."""
```

Implementation: extend `DAGSimPySimulator` with a `step` mode that
yields control between simpy `env.timeout` steps. SimPy supports
this via process suspension.

### 3.2 Action injection points

`AgentAction` for the L2 Terminal Coordinator is an 11-dim
`urgency_multiplier` vector + HiveSignal directive set. Injection
points in the simulator:

  * `_ship_downstream_stochastic` — apply BrokerRouting urgency
    multiplier to tender decisions.
  * `_process_inventory_site_stochastic` — apply CapacityBuffer
    multiplier to safety-stock sizing.
  * `_track_orders_stochastic` — apply ShipmentTracking multiplier
    to escalation thresholds.
  * (Same per-TRM mapping as the live system; simulator becomes a
    drop-in replacement for the live state-builder.)

### 3.3 Per-hour reward function

```python
def compute_reward(state: HubState, action: AgentAction,
                   policy: PolicyParameters) -> float:
    """Sum of weighted axes:
      sla_miss        — 1 - (delivered_on_time / delivered_total)
      tender_reject   — declined_or_expired / total_tenders (1h)
      dock_utilization — abs(util - target_util)
      equipment_imbalance — abs(net_balance) / fleet_size

    Weights derive from policy.bsc_weight_*. Returns negative
    scalar (PPO maximises; reward < 0 by default since lower KPIs
    are better)."""
```

Co-locate with `HubHourSnapshotService` (Phase 2 already extracts
the same KPIs into `hub_summary` JSONB).

### 3.4 Deterministic replay

SimPy is deterministic given seeded RNGs, but the existing simulator
mixes np.random.Generator with python's random module. Audit + lock
all RNG sources to a single seeded `np.random.Generator` for
batch-replay reproducibility.

### 3.5 Hub-hour state extraction

The simulator works at site × product × period granularity. PPO
needs hub × hour granularity. Build a `hub_hour_projection(sim_state)`
function that:

  * Filters to terminal-style sites (CROSS_DOCK / DC / TERMINAL).
  * Aggregates product-level state to hub-level.
  * Computes the 7 node types from `hub_hour_snapshot.node_features`
    using the same logic as `HubHourSnapshotService._build_node_
    features` but reading sim state instead of canonical DB rows.
  * Sub-hour resolution: simulator already runs daily; per-hour needs
    interpolation OR reduce time-step to 1h (more compute, but
    matches production).

---

## 4. Implementation roadmap

| # | Item | Owner | Wks | Depends |
|---|---|---|---|---|
| 1 | Audit doc — this doc | TMS | 0.5 | — |
| 2 | RNG audit + lock to single seeded Generator | TMS | 0.5 | 1 |
| 3 | Extract `compute_reward` from `_compute_kpis`; co-locate with snapshot service | TMS | 0.5 | 2 |
| 4 | Reduce simulator time-step to 1h (or interpolate) | TMS | 1 | 2 |
| 5 | Build `hub_hour_projection(sim_state)` reusing snapshot extractor | TMS | 1 | 4 |
| 6 | Build `TwinStepInterface` with `reset` + `step` + `hub_hour_state` | TMS | 1 | 4, 5 |
| 7 | Action injection at all per-TRM integration points | TMS | 1.5 | 6 |
| 8 | Validation: BC checkpoint policy run through twin should reproduce live behaviour ±5% | TMS | 0.5 | 7 |
| 9 | Twin-generated hub_hour_snapshot rows with `source="twin"` | TMS | 0.5 | 8 |
| 10 | Phase 3b BC warmup against twin-generated corpus | TMS | 1.5 | 9 |
| 11 | Phase 3b PPO loop on top of TwinStepInterface | TMS | 2 | 10 |

**Total**: ~10 weeks. Critical path through items 2-7 (6 weeks of
twin work), then 3 weeks for the agent training itself.

---

## 5. What this session ships toward Phase A

This audit doc itself is item 1 of the roadmap. The hub_hour_snapshot
table + `HubHourSnapshotService` shipped earlier (commit
[70a63e6](https://github.com/azirella-ltd/Autonomy-TMS/commit/70a63e6))
are the production-side foundation that items 5 + 9 reuse — the twin
extractor writes to the same table with `source="twin"`, so the
schema is shared across live and twin data sources.

Items 2-11 are out of scope for this session — they need focused
multi-week work. The audit is the deliverable that unblocks future
sessions: anyone picking up Phase A starts with a clear gap analysis,
roadmap, and effort estimate instead of having to re-read the 2,597
lines of existing simulator code.

---

## 6. Adjacent dependencies

  * `policy_parameters` (shipped) — twin reward function reads
    `bsc_weight_*` from active policy.
  * `hub_hour_snapshot` (shipped) — twin emits to the same table.
  * `terminal_health_signal` (shipped) — Phase-1 KPI mirror; twin
    emits these alongside snapshots.
  * Conformal calibration (shipped) — twin training data feeds the
    same auto-calibration pipeline as live data.

---

## 7. Cross-references

  * [TMS_TIER3_FIRST_PLAN.md](TMS_TIER3_FIRST_PLAN.md) §3 Phase A —
    the original 2-3 week estimate (this audit revises to ~10 weeks
    when including Phase 3b training on top, ~6 weeks for twin alone).
  * [L2_PHASE3_GRAPHCONSTRUCTION.md](L2_PHASE3_GRAPHCONSTRUCTION.md) §2.1
    — twin requirement detail.
  * [L2_TERMINAL_COORDINATOR_DESIGN.md](L2_TERMINAL_COORDINATOR_DESIGN.md) §3.4 —
    reward function design.
