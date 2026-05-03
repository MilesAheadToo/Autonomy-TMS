# Lane-Volume Forecasting (TMS) — Architecture and Defaults

> **Scope:** this document covers TMS's **lane-volume** forecasting at the **lane × period × mode × equipment × service-class** grain (loads + tonnage + cube). It is **not** item-level demand planning — that lives in **Autonomy-DP** at product × site × period grain (units). See [the DP-side doc](../../Autonomy-DP/docs/DEMAND_PLANNING_PLATFORM.md) for the item-level plane.

---

## 1. Why TMS has its own forecast

DP's per-item forecast (units of product P at site S in period T) is the demand signal. TMS doesn't ship items — it ships **loads** down **lanes** in particular **modes** (FTL / LTL / Parcel / Intermodal / Ocean / Rail / Air) on particular **equipment** (Dry Van / Reefer / Flatbed / Tanker / Containers). Two facts about why a separate forecast is needed:

1. **The grain is different.** A TMS planner asks "how many FTL Dry Van loads will originate from this DC next Tuesday?" — DP's per-product P50 doesn't answer that; it has to be aggregated up to the lane grain *and* segmented by mode + equipment. Loss of resolution and gain of information happen at the same time (you lose product-mix detail; you gain lane-period structure).
2. **The signals are different.** Lane volumes respond to consolidation rules, equipment availability, carrier contracts, peak-season surge protocols, and shipper-specific service tiers — none of which the DP forecast captures. A lane forecast that just sums DP's item forecasts misses the actual operating constraints.

So TMS runs its own L1 lane-volume forecast (TMS-internal TRM) **and** consumes Core's L3 lane-volume substrate (`LaneVolumePlan`).

---

## 2. The pipeline

```
Historical shipments (per lane × period)
    ↓
[L1] LaneVolumeForecastTRM  (TMS-internal — Holt-Winters / LightGBM / Croston / TSB / AutoETS routing)
    ↓ per-lane × per-period loads/tonnage/cube + segmentation
[Aggregation] §3.36 segmentation rules — split aggregate by mode + equipment-within-FTL share
    ↓
[L3] TacticalForecastService  (writes LaneVolumePlan rows in Core)
    ↓
[L3] MovementPlannerService → TransportationPlan(unconstrained_reference)
    ↓
[L3] IntegratedBalancerService → TransportationPlan(constrained_live)
    ↓
[L1/L2] BrokerRoutingTRM, CapacityPromiseTRM, dispatch / tender execution
```

Cross-product: DP's **lifecycle reason codes** (`lifecycle_npi_introduction`, `lifecycle_eol_phaseout`, etc. — see [DP §3.40 Phase 2 work](../../Autonomy-Core/docs/MIGRATION_REGISTER.md)) propagate into TMS's L1 forecast as *adjustments* to the historical baseline. When DP shifts an item's forecast for an NPI launch, the lane volumes for the lanes serving that item shift up too — but TMS computes the lane-level shift, not DP.

---

## 3. The L1 — `LaneVolumeForecastTRM`

**Where:** `Autonomy-TMS/backend/app/services/powell/tms_heuristic_library/lane_volume_forecast.py` (in the TMS heuristic library — see [§3.34 / §3.35 plane-module invariant work](../../Autonomy-Core/docs/MIGRATION_REGISTER.md))

**Grain:** one decision per lane × period.

**Methods (auto-routed by `LaneVolumeForecastTRM` based on Syntetos-Boylan demand classification):**

| Method | When it fires | Output |
|---|---|---|
| Holt-Winters | Smooth, seasonal, predictable lanes | P10 / P50 / P90 loads |
| LightGBM | Lanes with rich feature history (rate cards, fuel, calendar) | P10 / P50 / P90 |
| Croston | Intermittent lanes (some-zero periods) | P50 only with confidence |
| TSB | Lumpy lanes (Croston-with-trend) | P50 only |
| AutoETS | Smooth without long history | P10 / P50 / P90 |
| DEFER | <12 weeks of history | No row written |

The TRM emits a `TMSHeuristicDecision` carrying the chosen method, segmentation parameters, and forecast quality metrics (MAPE, conformal coverage at 80%).

### §3.36 segmentation extension

TMS forecasts the *aggregate* lane volume first, then splits by industry-norm shares:

- **Mode split:** historical share of FTL / LTL / Parcel / Intermodal / Ocean / Rail / Air on this lane, applied EWMA-smoothed.
- **Equipment-within-FTL split:** Dry Van / Reefer / Flatbed / Tanker / Container_20 / Container_40, same EWMA shape.
- **Service class:** stays as an L4 planning constraint — not a forecast facet at L1.

Three segmentation cases:
- `no_segmentation` — single `mode='ALL'` row; rare, only when no history exists
- `single_mode_passthrough` — one row at the dominant mode; used when one mode is >95% historical share
- `ewma_share_history` — one row per mode plus one row per equipment-within-FTL; the production default

Bands split proportionally: `equipment_p50 = aggregate_p50 × FTL_share × DRY_VAN_share`. Verified end-to-end that equipment p50s sum back to FTL mode p50.

### Reference: §3.36 register entry

[Core MIGRATION_REGISTER §3.36](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — TMS lane-volume forecast segmentation extension per industry norms (e2open / Blue Yonder / Oracle OTM / MercuryGate / SAP TM).

---

## 4. The L3 — `LaneVolumePlan` canonical state (Core substrate)

**Where:** `azirella_data_model.transport_plan.LaneVolumePlan` (Core, since §3.37).

**Grain:** one row per `(tenant_id, config_id, scenario_id, lane_id, period_start, mode, equipment_type, service_class, plan_version)` tuple.

**Schema highlights:**

- Nullable `equipment_type` (NULL = mode-level row; non-NULL = equipment-within-FTL row).
- Nullable `service_class` (populated by L4 customer-tier policy when that ships).
- **Conformal bands on loads** (P10 / P50 / P90); weight + cube as P50-only per industry norm (capacity-sizing inputs, not commitment-grade).
- DB-level CHECK constraints: `loads_band_ordered` (P10 ≤ P50 ≤ P90), `loads_nonneg`, `period_days_pos`, `equipment_only_ftl` (`equipment_type IS NULL OR mode = 'FTL'`).
- `DEFAULT_PLAN_VERSION = "unconstrained_reference"` (from `TMS_DECISION_HIERARCHY.md` §4.1).

The `TacticalForecastService` (TMS-side) writes these rows by aggregating L1 outputs.

---

## 5. The L3 — `MovementPlannerService` + `IntegratedBalancerService`

**Where:** `Autonomy-TMS/backend/app/services/powell/movement_planner_service.py` and `integrated_balancer_service.py`.

**Movement Planner** reads `LaneVolumePlan` and produces `TransportationPlan(plan_version='unconstrained_reference')` — one item per round(loads_p50) load per period. As of §3.38 Phase 2A, it also assigns the cheapest carrier from Core's `RateCard` substrate (§3.29 Group C settlement). Phase 2B (§3.38 close) added LP-projection feasibility repair, `ChargeCalculator` integration for fuel + accessorials, geographic lane filters, and the GraphSAGE Phase 3 scaffold.

**Integrated Balancer** clones `unconstrained_reference` → `constrained_live` and applies LP-projection capacity feasibility repair (§3.38 Phase 2B) — items that exceed all carriers are escalated as `CANCELLED`, with `capacity_utilization_per_carrier` reported. As of §3.42, capacity can come from Core's `CarrierCapacityCommitment` substrate.

---

## 6. Where the lifecycle / shaping signals enter

**§3.40 Phase 3 (planned, not yet shipped):** TMS's `LaneVolumeForecastTRM` will read `LifecyclePhase` from the canonical product table and adjust lane volumes for NPI launches into a geo / EOL pulls out of a geo. The NPI ramp curve and EOL phase-out curve come from DP — TMS doesn't reinvent them; it just reacts to the demand signal at the lane grain.

The reaction filter is `LIFECYCLE_REASON_CODES` (Core's `azirella_data_model.forecast_adjustment` enum subset). Dashboards filter on `code.startswith('lifecycle_')` to show how much of a lane's volume change is lifecycle-driven vs planner-driven.

---

## 7. Default settings (TMS-side knobs)

| Knob | Default | Lives in | Purpose |
|---|---|---|---|
| Forecast method routing thresholds | Syntetos-Boylan ADI=1.32 / CV²=0.49 | `lane_volume_forecast.py` | Classifies lanes for method auto-routing |
| Holt-Winters seasonal periods | 52 (weekly) | same | Tune to monthly (12) for low-frequency lanes |
| Conformal coverage target | 0.80 | same | P10/P90 bands; raise to 0.90 for safety-critical |
| Forecast MAPE acceptance threshold | 0.30 | same | Lanes above this trigger DEFER |
| Min history for L1 fire | 12 weeks | same | Below this, no row written; lane goes through pure heuristic floor |
| Mode share EWMA half-life | 4 weeks | §3.36 segmentation logic | Smoother for stable lanes; raise to 8 weeks for volatile |
| Service-class L4 default | NULL | `LaneVolumePlan` constraint | Filled by L4 customer-tier policy when that ships |
| `DEFAULT_PLAN_VERSION` | `'unconstrained_reference'` | Core `LaneVolumePlan` | The L3 plan-of-record version name |
| L3 plan refresh cadence | Daily 5:30am (manual today) | `TacticalForecastService` | Phase 2 wires scheduled trigger |

For cross-plane heuristic defaults that intersect with the decision plane, see [Core HEURISTIC_DEFAULTS_REGISTRY](../../Autonomy-Core/docs/HEURISTIC_DEFAULTS_REGISTRY.md) per §3.33.

---

## 8. What this is NOT

- **Not item-level demand planning.** That's DP. If the question is "how many units of product X will sell at site Y," go to DP.
- **Not Movement Plan or Constrained Balanced Plan.** Those are downstream consumers of `LaneVolumePlan`, not the forecast itself.
- **Not carrier tendering or dispatch.** Those are L1/L2 execution layers consuming the constrained plan.
- **Not yard / gate / dock scheduling.** Those are operational concerns at the site / yard layer (§3.29 Group D substrate).
- **Not freight settlement / billing.** That's §3.29 Group C, downstream of execution.

---

## 9. References

- [Core MIGRATION_REGISTER §3.36](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — segmentation extension
- [Core MIGRATION_REGISTER §3.37](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — `LaneVolumePlan` substrate + `TacticalForecastService`
- [Core MIGRATION_REGISTER §3.38](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — L3 Movement Planner + Integrated Balancer (Phases 1 / 2A / 2B)
- [Core MIGRATION_REGISTER §3.39](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — `TransportationPlan` move from TMS to Core
- [Core MIGRATION_REGISTER §3.42](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — `CarrierCapacityCommitment` substrate
- [Core MIGRATION_REGISTER §3.40](../../Autonomy-Core/docs/MIGRATION_REGISTER.md) — DP-side lifecycle substrate; Phase 3 will land TMS-side lane-volume sensitivity
- [DP DEMAND_PLANNING_PLATFORM.md](../../Autonomy-DP/docs/DEMAND_PLANNING_PLATFORM.md) — item-level demand planning (the peer plane)
- [TMS_DECISION_HIERARCHY.md](TMS_DECISION_HIERARCHY.md) §4.1 — original L3 design that §3.37 implements
- [TACTICAL_PLANNING_REARCHITECTURE.md](TACTICAL_PLANNING_REARCHITECTURE.md) — the broader L3 re-architecture context
