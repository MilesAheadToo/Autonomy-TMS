# TMS TRM Training Data Specification

**Version**: 1.0
**Date**: 2026-04-18
**Status**: Specification — paired with `SCP/TRM_TRAINING_DATA_SPECIFICATION.md`

---

## Overview

Each TMS TRM (Tactical Response Model) is pre-trained on synthetic
transportation states labeled by deterministic heuristic teachers. The
neural model learns the *family* of reasonable decisions; at inference
time it selects from the learned policy and falls back to the
deterministic engine when confidence is below threshold. This mirrors
the SCP TRM framework — the scaffolding is shared (Core's TRMTrainer,
behavioral cloning loss, CrossEntropy + value head, parquet corpus
format) — but the state/action/reward semantics are transportation
specific.

**Domain difference vs SCP:** SCP TRMs reason about internal inventory,
lot sizing, safety stock, MRP netting. TMS TRMs reason about *external
carrier networks*: waterfall tendering, carrier acceptance probability,
dock utilization, intermodal mode shift, equipment repositioning. Where
SCP state features come from MRP tables, TMS state features come from
lane/load/shipment/carrier/dock records plus live signals (ETA feeds,
market rates, spot premiums).

## Corpus Pipeline

```
Network Generator → Transport State Sampler → Single-Teacher Labeler → Parquet Corpus
       ↓                      ↓                          ↓                        ↓
  Lane topology       Load archetypes             1 deterministic            (state, action,
  Carrier pool        Capacity scenarios          teacher per TRM            reward) tuples
  Dock network        Exception scenarios         (no multi-teacher          × 50K per TRM
  Equipment fleet     Seasonal patterns            consensus — unlike SCP)
```

**Key difference from SCP:** TMS uses a **single deterministic teacher
per state by default** (one call into `compute_tms_decision()` in
`library/dispatch.py`). SCP uses 8 teachers per state to form a
consensus. TMS's single-teacher default is appropriate because
transportation heuristics are more discrete (waterfall, threshold-
based) and have less legitimate disagreement across methods than
inventory policies do.

**Phase-2 multi-teacher mode** (CLI: `--secondary-teachers`) adds
secondary labelers where two industry-recognised perspectives
legitimately diverge:
- **DAT-pure FreightProcurement** — picks the cheapest carrier inside
  the DAT band; ignores reliability scoring. The "cost-only buyer".
- **Regulatory-first ExceptionManagement** — escalates on FDA
  cold-chain / DOT 49 CFR hazmat / DOT post-accident reporting /
  FMCSA HOS triggers regardless of priority-score math. The
  "compliance officer".

Disagreement between primary and a secondary is emitted as
`consensus_action` / `disagreement` / per-teacher action+reasoning
columns on the corpus row. Empirically ~5–10 % of rows disagree under
phase 3 disruption. The seven other TRMs have no registered
secondary and stay single-teacher.

Scripts (shipped):
- [`backend/scripts/pretraining/generate_tms_corpus.py`](../backend/scripts/pretraining/generate_tms_corpus.py) — per-TRM corpus (`--trm <name>` or `--all`)
- [`backend/scripts/pretraining/generate_all_tms_trms.py`](../backend/scripts/pretraining/generate_all_tms_trms.py) — thin wrapper that invokes the per-TRM script with `--all`. Enumerates 12 entries (11 execution TRMs + `lane_volume_forecast`).

---

## TRM Catalog

TMS has **11 execution TRMs + 1 forecast orchestrator**. The execution
TRMs map to the six action codes defined in `TMSHeuristicDecision`:
`ACCEPT(0), REJECT(1), DEFER(2), ESCALATE(3), MODIFY(4), RETENDER(5),
REROUTE(6), CONSOLIDATE(7), SPLIT(8), REPOSITION(9), HOLD(10)`. Each
TRM uses a subset of these actions.

---

## 1. Capacity Promise (14 features → 3 actions)

Evaluates whether a requested shipment on a specific lane and date can
be promised given lane capacity, carrier availability, and priority.

### State: `CapacityPromiseState`

| Field | Type | Meaning |
|-------|------|---------|
| shipment_id | int | — |
| lane_id | int | — |
| requested_date | datetime | — |
| requested_loads | int | Number of loads needing capacity |
| mode | str | FTL, LTL, INTERMODAL, PARCEL |
| priority | int (1–5) | 1 = critical, 5 = low |
| committed_capacity | int | Already tendered loads on lane |
| total_capacity | int | Contracted lane capacity |
| buffer_capacity | int | Reserve pool |
| forecast_loads | int | Expected volume |
| booked_loads | int | Committed bookings |
| primary_carrier_available | bool | — |
| backup_carriers_count | int | Available tier-2/3 carriers |
| spot_rate_premium_pct | float (0–1) | Spot vs contract markup |

### Action Space

| Action | Idx | Trigger |
|--------|-----|---------|
| ACCEPT | 0 | Capacity exists or P1/P2 override |
| REJECT | 1 | No capacity, no backups, spot expensive |
| DEFER | 2 | Spot available but premium ≥ 20% |

### Teacher Logic (`dispatch.py:99–150`)

```
if priority in (1, 2):             → ACCEPT (consume buffer)
elif available_capacity >= requested_loads:  → ACCEPT
elif backup_carriers_count > 0 and spot_premium < 0.20: → ACCEPT
elif spot_premium >= 0.20:         → DEFER
else:                              → REJECT
```

---

## 2. Shipment Tracking (17 features → 3 actions)

Detects in-transit exceptions: silence, late delivery, temperature
excursion, slow progress.

### State: `ShipmentTrackingState`

| Field | Type | Meaning |
|-------|------|---------|
| shipment_id | int | — |
| shipment_status | str | IN_TRANSIT, DELIVERED, HELD |
| planned_pickup / actual_pickup | datetime | — |
| planned_delivery / current_eta | datetime | — |
| eta_p10 / eta_p90 | datetime | Conformal bounds |
| current_lat / current_lon | float | — |
| last_update_hours_ago | float | Silence detector |
| total_miles / miles_remaining | float | — |
| pct_complete | float (0–1) | — |
| carrier_otp_pct | float | On-time percentage |
| carrier_reliability_score | float | — |
| active_exceptions_count | int | Prior unresolved |
| is_temperature_sensitive | bool | — |
| current_temp / temp_min / temp_max | float | — |

### Action Space

| Action | Idx |
|--------|-----|
| ACCEPT | 0 |
| ESCALATE | 3 |
| MODIFY | 4 |

### Teacher Logic (`dispatch.py:157–215`)

```
if status == IN_TRANSIT and last_update_hours_ago > 4:  → ESCALATE (silence)
elif is_late():                                         → ESCALATE
elif is_temperature_sensitive and (temp < min or > max): → ESCALATE
elif pct_complete < 0.50 and time_used_pct > 0.50:       → MODIFY (behind schedule)
else:                                                   → ACCEPT
```

---

## 3. Demand Sensing (14 features → 2 actions)

Adjusts lane-level volume forecasts based on recent actuals, rolling
averages, external signals, and seasonal patterns.

### State: `DemandSensingState`

| Field | Type | Meaning |
|-------|------|---------|
| lane_id | int | — |
| period_start | date | — |
| period_days | int | Default 7 |
| forecast_loads | float | Current forecast |
| forecast_method | str | CONFORMAL, LGBM, NAIVE |
| forecast_mape | float | Rolling MAPE |
| actual_loads_current / actual_loads_prior | float | — |
| week_over_week_change_pct | float | — |
| rolling_4wk_avg | float | — |
| signal_type | str | VOLUME_SURGE, SEASONAL_SHIFT |
| signal_magnitude / signal_confidence | float | — |
| seasonal_index | float | Default 1.0 |
| is_peak_season | bool | — |
| day_of_week_pattern | List[float] | 7-element weights |

### Action Space

| Action | Idx |
|--------|-----|
| ACCEPT | 0 |
| MODIFY | 4 |

### Teacher Logic (`dispatch.py:222–263`)

```
bias = (forecast - actual) / forecast
if |bias| > 0.15:                        → MODIFY, qty = -bias × forecast × 0.5
elif |wow_change| > 0.20:                → MODIFY, qty = change × forecast × 0.3
elif is_peak_season and mape > 0.20:     → MODIFY, qty = forecast × 0.10 (buffer)
else:                                    → ACCEPT
```

---

## 4. Capacity Buffer (13 features → 2 actions)

Dynamic safety stock on lane capacity — analogue of SCP's safety stock
on inventory. Sizes a buffer over forecast based on reject rate,
volatility, peak season, and demand trend.

### State: `CapacityBufferState`

| Field | Type | Meaning |
|-------|------|---------|
| lane_id | int | — |
| mode | str | FTL default |
| baseline_buffer_loads | int | Current buffer |
| buffer_policy | str | PCT_FORECAST, FIXED, CONFORMAL |
| forecast_loads / p10 / p90 | int | With conformal bounds |
| committed_loads | int | — |
| contract_capacity / spot_availability | int | — |
| recent_tender_reject_rate | float (0–1) | — |
| recent_capacity_miss_count | int | 30-day window |
| avg_spot_premium_pct | float | — |
| demand_cv | float | Coefficient of variation |
| demand_trend | float | +1 growing, −1 declining |
| is_peak_season | bool | — |

### Action Space

| Action | Idx |
|--------|-----|
| ACCEPT | 0 |
| MODIFY | 4 |

### Teacher Logic (`dispatch.py:270–320`)

```
multiplier = 1.0
if reject_rate > 0.15:          multiplier *= 1.3
if demand_cv > 0.3:             multiplier *= (1 + 0.5 * cv)
if is_peak_season:              multiplier *= 1.2
if demand_trend > 0.1:          multiplier *= (1 + 0.2 * trend)
if capacity_miss >= 3:          multiplier *= 1.25
if reject<0.05 and cv<0.15 and !peak and misses==0:
                                multiplier *= 0.85   # shrink buffer
action = MODIFY if |multiplier - 1| > 0.05 else ACCEPT
quantity = baseline_buffer × multiplier
```

---

## 5. Exception Management (17 features → 4 actions)

Routes response strategy for detected in-flight exceptions.

### State: `ExceptionManagementState`

| Field | Type | Meaning |
|-------|------|---------|
| exception_id / shipment_id | int | — |
| exception_type | str | TEMPERATURE_EXCURSION, LATE_DELIVERY, LATE_PICKUP |
| severity | str | LOW, MEDIUM, HIGH, CRITICAL |
| hours_since_detected | float | — |
| estimated_delay_hrs | float | — |
| estimated_cost_impact | float | — |
| revenue_at_risk | float | — |
| shipment_priority | int (1–5) | — |
| is_temperature_sensitive / is_hazmat | bool | — |
| delivery_window_remaining_hrs | float | — |
| carrier_id | int | — |
| carrier_reliability_score | float | Default 0.8 |
| carrier_response_time_hrs | float | Default 2.0 |
| can_retender / can_reroute / can_partial_deliver | bool | — |
| alternate_carriers_available | int | — |

### Action Space

| Action | Idx |
|--------|-----|
| ACCEPT | 0 |
| ESCALATE | 3 |
| RETENDER | 5 |
| REROUTE | 6 |

### Teacher Logic (`dispatch.py:327–377`)

```
critical = priority in (1,2) or severity == CRITICAL
if critical and delay_hrs > 4:
    if can_retender and alternates > 0: → RETENDER
    else:                                → ESCALATE
elif exception_type == TEMPERATURE_EXCURSION: → ESCALATE
elif late and window_remaining_hrs > 8 and can_reroute: → REROUTE
elif delay_hrs < 2 and severity in (LOW, MEDIUM):       → ACCEPT
else: → ESCALATE (default)
```

---

## 6. Freight Procurement (19 features → 2 actions)

Carrier waterfall tendering: primary → backups → spot → escalate.

### State: `FreightProcurementState`

| Field | Type | Meaning |
|-------|------|---------|
| load_id / lane_id | int | — |
| mode / required_equipment | str | FTL / DRY_VAN default |
| weight / pallet_count | float / int | — |
| is_hazmat / is_temperature_sensitive | bool | — |
| pickup_date / delivery_date | datetime | — |
| lead_time_hours | float | Default 48 |
| primary_carrier_id / rate / acceptance_pct | int / float / float | — |
| backup_carriers | List[{id, rate, acceptance_pct, priority}] | Waterfall tier-2/3 |
| spot_rate / contract_rate | float | — |
| market_tightness | float (0–1) | — |
| dat_benchmark_rate | float | Market index |
| tender_attempt / max_tender_attempts | int | Waterfall position |
| hours_to_tender_deadline | float | Default 24 |

### Action Space

| Action | Idx |
|--------|-----|
| ACCEPT | 0 |
| ESCALATE | 3 |

### Teacher Logic (`dispatch.py:384–433`)

```
if tender_attempt == 1 and primary_carrier:   → ACCEPT (contract rate)
elif tender_attempt <= max and backups:       → ACCEPT (best backup)
elif (spot_rate - contract_rate) / contract < 0.30: → ACCEPT (spot)
else:                                         → ESCALATE (broker)
```

params carried on ACCEPT: `{carrier_id, rate_type: "contract"|"backup"|"spot"}`

---

## 7. Broker Routing (11 features → 2 actions)

Broker selection when carrier waterfall is exhausted.

### State: `BrokerRoutingState`

| Field | Type | Meaning |
|-------|------|---------|
| load_id / lane_id | int | — |
| mode | str | — |
| tender_attempts_exhausted | int | — |
| all_contract_carriers_declined | bool | — |
| hours_to_pickup | float | Default 24 |
| available_brokers | List[{id, name, rate, reliability, coverage_score}] | — |
| contract_rate / spot_rate | float | — |
| broker_rate_premium_pct | float | Default 0.15 |
| budget_remaining | float | — |
| shipment_priority | int | — |
| is_customer_committed | bool | — |

### Action Space

| Action | Idx |
|--------|-----|
| ACCEPT | 0 |
| ESCALATE | 3 |

### Teacher Logic (`dispatch.py:440–498`)

```
if no brokers:                     → ESCALATE
if priority in (1,2):              → ACCEPT (highest-reliability broker)
else:
    score = rate / reliability     (cost-per-reliability, minimize)
    pick best broker
    if (rate - contract) / contract > 0.40: → ESCALATE
    else:                                   → ACCEPT (selection: "cost_reliability")
```

---

## 8. Dock Scheduling (16 features → 3 actions)

Appointment timing + door assignment + dwell/detention risk.

### State: `DockSchedulingState`

| Field | Type | Meaning |
|-------|------|---------|
| facility_id / appointment_id | int | — |
| appointment_type | str | PICKUP, DELIVERY, CROSS_DOCK |
| total_dock_doors / available_dock_doors | int | Default 10 / 5 |
| yard_spots_total / yard_spots_available | int | Default 50 / 20 |
| requested_time / earliest_available_slot / latest_acceptable_slot | datetime | — |
| appointments_in_window | int | Utilization context |
| avg_dwell_time_minutes | float | Default 45 |
| current_queue_depth | int | — |
| shipment_priority | int (1–5) | — |
| is_live_load | bool | — |
| estimated_load_time_minutes | float | Default 60 |
| free_time_minutes | float | Default 120 |
| detention_rate_per_hour | float | Default $75 |
| carrier_avg_dwell_minutes | float | Default 90 |

### Action Space

| Action | Idx |
|--------|-----|
| ACCEPT | 0 |
| DEFER | 2 |
| MODIFY | 4 |

### Teacher Logic (`dispatch.py:505–554`)

```
util = 1 - (available_doors / total_doors)
detention_risk = 0 if carrier_dwell <= free_time
                 else min(1, (carrier_dwell - free_time) / 120)

if priority in (1,2):                → ACCEPT
elif util > 0.85 and priority in (4,5): → DEFER
elif queue_depth > 3:                → MODIFY (recommend drop-trailer)
elif detention_risk > 0.7:           → MODIFY (prioritize turnaround)
else:                                → ACCEPT
```

---

## 9. Load Build (15 features → 5 actions)

Shipment consolidation: merge into FTL vs keep LTL vs split/defer.

### State: `LoadBuildState`

| Field | Type | Meaning |
|-------|------|---------|
| shipment_ids | List[int] | Candidate consolidation group |
| lane_id | int | — |
| mode / equipment_type | str | FTL / DRY_VAN default |
| max_weight | float | 44000 lbs |
| max_volume | float | 2700 cuft |
| max_pallets | int | 26 |
| total_weight / total_volume / total_pallets | float / int | Running sums |
| shipment_count | int | — |
| has_hazmat_conflict / has_temp_conflict / has_destination_conflict | bool | Consolidation blockers |
| max_stops | int | Default 3 |
| earliest_pickup / latest_pickup | datetime | Consolidation window |
| consolidation_window_hours | float | Default 24 |
| ftl_rate / ltl_rate_sum | float | Pricing comparison |
| consolidation_savings | float | ltl_sum − ftl |

### Action Space

| Action | Idx |
|--------|-----|
| ACCEPT | 0 (ship as-is) |
| REJECT | 1 (do not consolidate — conflict) |
| DEFER | 2 (wait for more loads) |
| CONSOLIDATE | 7 |
| SPLIT | 8 |

### Teacher Logic (`dispatch.py:561–610`)

```
if has_hazmat_conflict or has_temp_conflict:  → REJECT
elif should_consolidate():                    → CONSOLIDATE
    # savings > 0 AND weight<95% AND volume<95% AND no conflicts
elif weight > 95% or volume > 95%:            → SPLIT
elif weight < 50% and shipment_count == 1:    → DEFER (wait)
else:                                         → ACCEPT
```

---

## 10. Intermodal Transfer (14 features → 2 actions)

Mode shift evaluation: truck ↔ rail intermodal, truck ↔ ocean.

### State: `IntermodalTransferState`

| Field | Type | Meaning |
|-------|------|---------|
| shipment_id | int | — |
| current_mode / candidate_mode | str | FTL, RAIL_INTERMODAL, OCEAN |
| origin_to_ramp_miles / ramp_to_ramp_miles / ramp_to_dest_miles | float | Legs |
| total_truck_miles | float | All-truck alternative |
| truck_rate / intermodal_rate | float | — |
| drayage_rate_origin / drayage_rate_dest | float | First/last mile |
| truck_transit_days / intermodal_transit_days | float | — |
| delivery_window_days | float | Slack vs due date |
| rail_capacity_available | bool | Default True |
| ramp_congestion_level | float (0–1) | — |
| intermodal_reliability_pct | float | Default 0.85 |
| weather_risk_score | float (0–1) | — |

### Action Space

| Action | Idx |
|--------|-----|
| ACCEPT | 0 (mode shift approved) |
| REJECT | 1 (keep current mode) |

### Teacher Logic (`dispatch.py:617–672`)

```
savings = (truck_rate - intermodal_all_in_rate) / truck_rate
transit_penalty = intermodal_days - truck_days
has_time = transit_penalty <= delivery_window_days

if ramp_congestion > 0.7:                     → REJECT
elif not has_time:                            → REJECT
elif reliability < 0.80 and window < 2d:      → REJECT
elif savings > 0.15 and has_time:             → ACCEPT
elif miles > 800 and savings > 0.05:          → ACCEPT
else:                                         → REJECT
```

---

## 11. Equipment Reposition (14 features → 2 actions)

Network-wide empty equipment balancing between surplus and deficit
facilities.

### State: `EquipmentRepositionState`

| Field | Type | Meaning |
|-------|------|---------|
| equipment_type | str | DRY_VAN default |
| source_facility_id / source_equipment_count | int | — |
| source_demand_next_7d | int | Forward need |
| target_facility_id / target_equipment_count | int | — |
| target_demand_next_7d | int | — |
| reposition_miles / reposition_cost | float | Empty move economics |
| reposition_transit_hours | float | — |
| network_surplus_locations / network_deficit_locations | int | Portfolio view |
| total_fleet_size | int | — |
| fleet_utilization_pct | float (0–1) | — |
| cost_of_not_repositioning | float | Spot premium avoided |
| breakeven_loads | int | Default 1 |

### Action Space

| Action | Idx |
|--------|-----|
| REPOSITION | 9 |
| HOLD | 10 |

### Teacher Logic (`dispatch.py:679–739`)

```
roi = cost_of_not_repositioning / reposition_cost

if no surplus or no deficit:          → HOLD
elif fleet_util > 0.9 and deficit > 0: → REPOSITION
elif roi > 1.5:                        → REPOSITION
elif roi > 1.0 and miles < 200:        → REPOSITION
else:                                  → HOLD
```

---

## 12. Forecast Baseline (21 features → recommendation)

Orchestrator (not a discrete-action TRM). Classifies demand profile,
selects statistical model, detects drift, emits forecast trend signal.

### State: `ForecastBaselineState`

| Field | Type | Meaning |
|-------|------|---------|
| product_id / site_id | str | — |
| demand_cv | float | Coefficient of variation |
| demand_adi | float | Average Demand Interval |
| observation_count / non_zero_count | int | — |
| trend_slope | float | Linear trend |
| seasonality_strength | float (0–1) | — |
| lifecycle_stage | str | concept, launch, growth, maturity, decline, eol |
| current_model | str | lgbm, croston, holt_winters, lgbm_volatility, lgbm_lifecycle |
| current_mape / baseline_mape | float | — |
| cusum_statistic | float | Drift detector |
| current_p50 / p10 / p90 | float | — |
| conformal_coverage | float | Default 0.80 |
| category_demand_trend | float | Parent-category signal |
| sibling_count | int | Cross-product context |
| sibling_share_change | float | Market share delta |
| external_signals_active | List[str] | Active signal sources |
| signal_fva_scores | Dict[str, float] | Per-signal FVA contribution |
| stockout_periods_pct | float | Censored demand indicator |
| censored_demand_estimated | bool | — |

### Output: `ForecastBaselineRecommendation`

Structured recommendation (no discrete action):

- `demand_profile` ∈ {SMOOTH, ERRATIC, INTERMITTENT, LUMPY, NEW, DECLINING}
- `recommended_model` ∈ {lgbm, lgbm_volatility, croston, holt_winters, lgbm_lifecycle}
- `model_changed` bool
- `retrain_recommended` bool + `retrain_reason` ∈ {drift_cusum, mape_degradation, conformal_coverage_drift}
- `cross_product_features_enabled` bool
- `external_signals_enabled` List[str] (filtered by FVA)
- `censored_demand_correction` bool
- `forecast_p50 / p10 / p90` float
- `confidence` float (1 − interval_width)
- `mape / conformal_interval_width` float
- `fva_vs_naive` float
- `demand_trend_signal` ∈ {surge, drop, stable} + `magnitude` float

### Teacher Logic (`forecast_baseline_trm.py:336–429`)

**1. Demand profile classification:**
```
if observations < 26:                            → NEW
elif trend < -0.05 and stage in (decline,eol):   → DECLINING
elif cv < 0.5 and adi <= 1.32:                   → SMOOTH
elif cv < 0.5 and adi > 1.32:                    → INTERMITTENT
elif cv >= 0.5 and adi <= 1.32:                  → ERRATIC
else:                                            → LUMPY
```

**2. Model selection (MODEL_BY_PROFILE):**
```
SMOOTH        → lgbm
ERRATIC       → lgbm_volatility
INTERMITTENT  → croston
LUMPY         → croston
NEW           → holt_winters
DECLINING     → lgbm_lifecycle
```

**3. Drift detection:**
```
cooldown: skip if < 168h since last retrain
if cusum > 5.0:                                      → retrain (drift_cusum)
elif (current_mape - baseline_mape) / baseline > 0.15: → retrain (mape_degradation)
elif conformal_coverage < 0.70:                      → retrain (conformal_coverage_drift)
```

**4. Trend signal:**
```
if trend_slope > 0.10:                           → surge (magnitude = slope)
elif trend_slope < -0.10:                        → drop  (magnitude = |slope|)
elif category_trend > 0.15 and share_change < -0.05: → drop (magnitude = |share_change|)
else:                                            → stable
```

**5. Confidence + FVA:**
```
interval_width = (p90 - p10) / p50
confidence = clip(1 - interval_width, 0.1, 0.99)

naive_mape = max(1.5 × baseline_mape, 0.25)
fva = naive_mape - current_mape        # positive = we beat naive
```

---

## Training Configuration

Identical to SCP spec for architectural consistency:

| Parameter | Value |
|-----------|-------|
| Architecture | 2-layer MLP (input → 128 → 64 → num_actions) with LayerNorm + Dropout(0.1) |
| Optimizer | AdamW, lr=0.001, weight_decay=1e-4 |
| LR Schedule | Cosine annealing over epochs |
| Batch size | 2048 |
| Epochs | 20 |
| Loss | CrossEntropyLoss (+ 0.1× MSE on value head) |
| Validation | 10% holdout |
| Early stop | Best validation loss checkpoint saved |
| Samples per TRM | 50K states × 1 teacher = 50K rows (TMS single-teacher vs SCP's 8-teacher) |
| GPU | RTX 4060 Laptop or equivalent, ~3 seconds per TRM |

## Checkpoint Format

Same as SCP:
```python
{
    "model_state_dict": OrderedDict(...),
    "trm_type": "capacity_promise",
    "state_dim": 14,
    "num_actions": 3,
    "hidden_dim": 128,
}
```
Stored at `/app/models/pretrained/tms_trm_base_v1/trm_{type}.pt`.

---

## Synthetic Data Generator (Shared with SCP)

Source: `powell/synthetic_trm_data_generator.py`. TMS training samples
use the same underlying generator that SCP uses, with TMS-specific
state builders on top of the transport-oriented scenario archetypes.

### Demand patterns (weighted)
- STABLE 20% · SEASONAL 25% · TRENDING_UP 15% · TRENDING_DOWN 10%
- STEP_CHANGE 10% · PROMOTIONAL 5% · RANDOM 15%

### Phase-aware curriculum (DAT-anchored, TMS-native)

Replaces SCP's scalar variance multipliers (`0.15 / 0.40 / 0.75` on inventory
mean) with per-knob distributional bands tuned to transport benchmarks.
Live in `PHASES` at the top of
[`generate_tms_corpus.py`](../backend/scripts/pretraining/generate_tms_corpus.py).
Default is **phase 2**.

| Knob | Phase 1 baseline | Phase 2 normal | Phase 3 disruption | Industry anchor |
|---|---|---|---|---|
| `spot_premium_max` | 0.08 | 0.25 | 0.45 | DAT acceptance threshold 0.30 |
| `market_tightness_band` | (0.0, 0.30) | (0.10, 0.70) | (0.50, 1.0) | DAT / FreightWaves |
| `reject_rate_max` | 0.08 | 0.20 | 0.45 | Truckstop tranches; buffer-expansion threshold 0.15 |
| `primary_carrier_otp_band` | (0.92, 0.99) | (0.82, 0.97) | (0.70, 0.92) | Fleet quality |
| `intermodal_reliability_band` | (0.90, 0.97) | (0.80, 0.95) | (0.65, 0.90) | BNSF / CSX floor 0.80 |
| `ramp_congestion_band` | (0.0, 0.30) | (0.10, 0.70) | (0.50, 1.0) | AAR ramp data |
| `demand_cv_max` | 0.30 | 0.55 | 0.85 | Operations research |
| `exception_severity_weights` (LOW, MED, HIGH, CRITICAL) | (40, 35, 15, 10) | (20, 40, 25, 15) | (10, 25, 35, 30) | FMCSA / project44 |

Phase-aware samplers (consume the bands above): `_sample_capacity_promise`,
`_sample_capacity_buffer`, `_sample_freight_procurement`,
`_sample_intermodal_transfer`, `_sample_exception_management`. The other
samplers ignore the `phase` kwarg; corpus generation still passes it for
forward compatibility.

CLI: `--phase 1 | 2 | 3` (default 2). Backward-compatible — checkpoints
trained without `--phase` see the same distributions they did before
the curriculum was introduced.

### Decision source distribution (training labels)
- EXPERT_HUMAN 40% · AI_ACCEPTED 25% · AI_MODIFIED 15%
- AI_REJECTED 10% · AI_AUTONOMOUS 10%

---

## Reward Functions

Four TMS TRMs have **native transport-KPI reward weights** in
[`backend/app/services/powell/tms_reward_weights.py`](../backend/app/services/powell/tms_reward_weights.py)
— `TMS_TRM_REWARD_WEIGHTS`. The other seven retain SCP-analogue
weights from `DEFAULT_TRM_REWARD_WEIGHTS` (spec §6 considers those
mappings appropriate; revisit if SCP-shape signal semantics drift
from TMS reality).

| TMS TRM | Source | Weights |
|---------|--------|---------|
| Capacity Promise | SCP proxy (ATP_EXECUTOR) | fill_rate (0.4) + on_time_bonus (0.2) + priority_weight (0.2) + fairness_penalty (0.2) |
| Exception Management | SCP proxy (ORDER_TRACKING) | detection (0.4) + resolution_speed (0.3) + escalation (0.3) |
| **Intermodal Transfer** | **NATIVE TMS** | mode_shift_savings (0.40) + on_time_intermodal (0.25) + delivery_window_slack (0.20) + reliability_bonus (0.15) |
| **Equipment Reposition** | **NATIVE TMS** | reposition_roi (0.40) + fleet_utilization_gain (0.30) + network_balance_improvement (0.20) + transit_cost_penalty (0.10) |
| Freight Procurement | SCP proxy (PO_CREATION) | stockout_penalty (0.4) + target_coverage (0.3) + cost (0.2) + timing (0.1) |
| **Load Build** | **NATIVE TMS** | fill_rate (0.40) + consolidation_savings (0.30) + stops_efficiency (0.15) + priority_alignment (0.15); hazmat / temp conflicts hard-gate to REJECT |
| **Dock Scheduling** | **NATIVE TMS** | throughput_alignment (0.35) + detention_avoidance (0.35) + dwell_efficiency (0.20) + priority_alignment (0.10) |
| Broker Routing | SCP proxy (PO_CREATION) | cost + reliability blend |
| Capacity Buffer | SCP proxy (SAFETY_STOCK) | service_level_deviation (0.5) + holding_cost (0.3) + excess (0.2) |
| Shipment Tracking | SCP proxy (ORDER_TRACKING) | detection + resolution + escalation |
| Demand Sensing | SCP proxy (FORECAST_ADJUSTMENT) | bias_reduction (0.5) + MAPE_improvement (0.3) + stability (0.2) |
| Forecast Baseline | SCP proxy (FORECAST_BASELINE) | MAPE (0.5) + FVA (0.3) + conformal_coverage (0.2) |

Native-weight provenance — domain thresholds and weight magnitudes
follow §6 industry references (DAT, AAR, FMCSA, BNSF / CSX service
floors, operations-research consolidation literature). Each native
reward function is unit-tested for directional correctness against
clear-case scenarios in
[`backend/tests/services/powell/test_tms_reward_weights.py`](../backend/tests/services/powell/test_tms_reward_weights.py).

The native reward functions are invoked from
[`generate_tms_corpus.py`](../backend/scripts/pretraining/generate_tms_corpus.py)
via `compute_native_tms_reward(trm_name, state, decision)`. When this
returns `None` (the seven SCP-proxy TRMs) the corpus generator falls
back to the generic action-based `compute_reward()`.

---

## Differences From SCP TRM Spec

| Aspect | SCP | TMS |
|--------|-----|-----|
| Teacher consensus | 8 heuristic teachers per state | 1 deterministic teacher per state |
| Samples per TRM | 100K states × 8 = 800K | 50K states × 1 = 50K |
| State source | Inventory tables (MRP, BOM) | Lane/load/shipment/carrier/dock |
| Action semantics | Inventory (order/defer/expedite/cancel) | Waterfall (tender/retender/reroute/escalate) |
| Rewards | Economic: stockout + holding + ordering | Service: fill + detention + consolidation |
| Domain knobs | Lot sizing, SS methods, MOQ | Spot premium, reject rate, detention rate |
| Pretraining origin | CPIM/APICS + SAP MRP lot sizing keys | Carrier industry + waterfall tendering + mode-shift economics |

---

## References and Justification

### Industry Standards

| Source | Relevance | Applied to |
|--------|-----------|-----------|
| **SMC³ BOL / NMFC** — National Motor Freight Classification | LTL classification and consolidation logic | Load Build (hazmat, class-based constraints) |
| **ELD / HOS rules** (FMCSA 395) | Hours of Service → driver availability → dwell/detention economics | Dock Scheduling (detention risk), Exception Management (late delivery) |
| **AAR Intermodal Standards** — Association of American Railroads | Rail intermodal terminology, equipment classes, ramp congestion | Intermodal Transfer |
| **APICS CSCP** — Certified Supply Chain Professional | Transportation planning, DRP, deployment | Demand Sensing (lane-level forecast), Capacity Buffer |
| **CSCMP State of Logistics Report** (annual) | Industry benchmarks for spot vs contract premium, reject rates | Freight Procurement (waterfall thresholds), Capacity Buffer |

### TMS Vendor Documentation

| Vendor | Relevance | Applied to |
|--------|-----------|-----------|
| **Oracle OTM** — Oracle Transportation Management | Load planning, lane rating, carrier selection workflow | Freight Procurement, Load Build, Dock Scheduling |
| **SAP TM** — SAP Transportation Management | Tendering cycle, carrier waterfall, freight settlement | Freight Procurement (tender waterfall), Broker Routing |
| **Blue Yonder TMS** | Autonomous tendering, spot market integration, exception orchestration | Exception Management, Freight Procurement |
| **MercuryGate / Manhattan Associates** | Dock appointment management, yard visibility, detention tracking | Dock Scheduling, Shipment Tracking |
| **project44 / FourKites** | In-transit visibility, predictive ETA, exception detection | Shipment Tracking (ETA, silence detection, temperature monitoring) |

### Market-Index References

| Index | Applied to |
|-------|-----------|
| **DAT Freight Rate Index** — spot vs contract rate benchmarks | Freight Procurement (spot premium threshold: 30%), Broker Routing (premium alert: 40%) |
| **Cass Freight Index** — volume signals | Demand Sensing (seasonal indices, macro volume shifts) |
| **Truckstop Rate Reports** — reject rate benchmarks | Capacity Buffer (reject rate threshold: 15%) |

### Academic References

| Reference | Applied to |
|-----------|-----------|
| **Croston 1972** — forecasting intermittent demand | Forecast Baseline (INTERMITTENT/LUMPY → croston model) |
| **Holt-Winters** — exponential smoothing with seasonality | Forecast Baseline (NEW items, insufficient history) |
| **Wagner-Whitin** (1958) — dynamic lot sizing | Load Build (optimal consolidation window) |
| **Conformal Prediction** (Vovk et al.) — distribution-free intervals | Forecast Baseline (p10/p90 bands), Capacity Buffer (conformal policy) |
| **CUSUM** (Page 1954) — sequential change detection | Forecast Baseline (drift detection, threshold 5.0) |

### Domain Thresholds — Provenance

| Threshold | Value | Source |
|-----------|-------|--------|
| Silence detector | > 4h without GPS update | project44/FourKites exception taxonomy |
| Late exception delay | > 2h late = LOW/MEDIUM cutoff | Oracle OTM alert thresholds |
| Spot premium acceptance | < 30% over contract | DAT Freight weekly median |
| Broker premium alert | > 40% over contract | CSCMP benchmark |
| Dock utilization defer | > 85% + P4/P5 priority | MercuryGate dock scheduling defaults |
| Detention risk threshold | carrier dwell > (free_time + 2h) | FMCSA detention study (2018) |
| Reject rate for buffer expansion | > 15% | Truckstop reject-rate tranches |
| Demand CV for buffer expansion | > 0.30 | Operations research practice (3σ safety stock) |
| CUSUM drift threshold | > 5.0 | Montgomery, *Introduction to SQC* |
| Intermodal savings threshold | > 15% (with time slack) | AAR intermodal economic studies |
| Intermodal reliability floor | 80% | BNSF/CSX service-level agreements |

---

## Open Items

1. **Native TMS reward weights** — ~~Dock Scheduling, Intermodal Transfer, Equipment Reposition, Load Build currently inherit SC proxies.~~ **Closed 2026-05-10.** Native transport-KPI weights live in [`tms_reward_weights.py`](../backend/app/services/powell/tms_reward_weights.py); see the table above. Future revisit: replace any of the remaining seven SCP-proxy mappings if real TMS KPI data shows the proxy is misleading the training signal.

2. **Phase 2 teachers** — ~~TMS currently uses single-teacher labels.~~ **Closed 2026-05-11.** Two secondaries shipped in [`autonomy_tms_heuristics.library.secondary_teachers`](../packages/autonomy-tms-heuristics/src/autonomy_tms_heuristics/library/secondary_teachers.py): DAT-pure FreightProcurement + regulatory-first ExceptionManagement. CLI flag `--secondary-teachers` enables multi-teacher labeling for those two TRMs; corpus rows gain `consensus_action` / `disagreement` / per-teacher columns. Locked in [`test_secondary_teachers.py`](../packages/autonomy-tms-heuristics/tests/test_secondary_teachers.py) (16 tests).

3. **Live backtests** — ~~SCP has a live-data backtest (PO Creation → 99.6 % on real SAP decisions). TMS needs the same.~~ **Scaffold closed 2026-05-11.** Framework + schema + 50-row synthetic fixture + 16 tests live in [`backend/scripts/backtest/`](../backend/scripts/backtest/). Schema documented in [`TMS_LIVE_BACKTEST_SCHEMA.md`](TMS_LIVE_BACKTEST_SCHEMA.md). The real-data extract (Oracle OTM / SAP TM / MercuryGate frozen tender history) is a separate workstream — once a customer extract is in hand, run `python backend/scripts/backtest/run_tender_backtest.py extract.jsonl --report-path report.json` to compare heuristic teacher vs planner decisions. SCP's 99.6 % is the agreement target for the `freight_procurement` TRM.

4. **Training corpus generator** — ~~`generate_all_tms_trms.py` script not yet written.~~ **Closed 2026-05-11.** Both [`generate_tms_corpus.py`](../backend/scripts/pretraining/generate_tms_corpus.py) and the wrapper [`generate_all_tms_trms.py`](../backend/scripts/pretraining/generate_all_tms_trms.py) are shipped and tested end-to-end against all 12 samplers. CPU-dev smoke run: `python scripts/pretraining/generate_all_tms_trms.py --samples 5 --output-dir /tmp/smoke`.

5. **Curriculum phases** — ~~Current generator uses SCP's phase variance (0.15 / 0.40 / 0.75).~~ **Closed 2026-05-11.** Replaced with TMS-native per-knob `PhaseConfig` bands at the top of [`generate_tms_corpus.py`](../backend/scripts/pretraining/generate_tms_corpus.py), anchored to DAT spot-premium / Truckstop reject-rate / BNSF-CSX intermodal-reliability / FMCSA exception-severity benchmarks. CLI flag `--phase 1|2|3` with default 2. Phase-shift invariants locked in [`test_corpus_phase_curriculum.py`](../backend/tests/services/powell/test_corpus_phase_curriculum.py).

---

**Companion document**: `SCP/TRM_TRAINING_DATA_SPECIFICATION.md`
