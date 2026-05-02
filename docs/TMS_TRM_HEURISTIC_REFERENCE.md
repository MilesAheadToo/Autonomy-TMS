# TMS TRM Heuristic Reference — Implemented Logic, Algorithms & Sources

**Version:** 2.2 (lane-volume forecast segmentation extension, 2026-05-02)
**Location:** `app.services.powell.tms_heuristic_library` (this repo)
**Purpose:** Definitive reference for the deterministic heuristics that serve as
(a) cold-start fallback logic for live TMS agents, and (b) teacher policy for
TRM behavioral-cloning training. Every TRM's logic is documented here with
the industry source, canonical OR problem, implemented algorithm, and the
state fields consumed.

---

## How to read this document

Each TRM section contains:

| Section | What it tells you |
|---|---|
| **Canonical Problem** | The OR / logistics problem name and mathematical structure |
| **Industry Sources** | Production TMS systems, academic references, and standards consulted |
| **Implemented Algorithm** | Exactly what `dispatch.py` does, line-by-line |
| **State Fields** | Complete `*State` dataclass fields with types and semantics |
| **Action Space** | Discrete actions the TRM can emit |
| **Training Data Notes** | What the overlay generator must provide for this TRM |

---

## 1. CapacityPromiseTRM (SENSE)

### Canonical Problem
**Multi-Dimensional Knapsack / Available-to-Promise (ATP)** with temporal
constraints. Given a shipment request (lane × date × mode), evaluate
whether committed carrier capacity can promise fulfillment.

### Industry Sources
- **Oracle OTM** Capacity Consumption Engine — routing guide hierarchy with
  running tally of committed vs. consumed per lane/carrier/time-bucket
  [Oracle OTM documentation, Release 6.4+]
- **SAP TM** Transportation Capacity Booking (TCB) — soft/hard allocation
  with configurable safety margins [SAP TM 9.x Planning Guide]
- **BluJay / E2open** routing guide waterfall with tiered carrier allocation
  percentages (primary 70%, secondary 20%, spot 10%)
- EDI 204 (Motor Carrier Load Tender), 990 (Response to Load Tender)
  [ANSI X12 standards]

### Implemented Algorithm
**Lane-level composite scoring** with priority override.

```
Composite score (0–1):
  0.35 × capacity_factor       min(1, available/requested)
  0.25 × service_factor        primary carrier OTP trailing 90d
  0.20 × acceptance_factor     lane trailing acceptance rate
  0.10 × compliance_factor     allocation compliance %
  0.10 × market_factor         1 - market_tightness

P1–P2 priority override: always ACCEPT (consume buffer).
Score ≥ 0.60 → ACCEPT (spot premium gate if capacity insufficient)
Score 0.35–0.60 → DEFER to procurement
Score < 0.35 → REJECT
```

### State Fields (`CapacityPromiseState`)

| Field | Type | Source |
|---|---|---|
| shipment_id, lane_id | int | Request |
| requested_date | datetime | Request |
| requested_loads | int | Request |
| mode | str | FTL/LTL/INTERMODAL |
| priority | int (1–5) | Customer tier |
| committed_capacity | int | Current bookings on lane/date |
| total_capacity | int | Contract capacity |
| buffer_capacity | int | Safety reserve |
| forecast_loads | int | Demand forecast |
| booked_loads | int | Already committed |
| primary_carrier_available | bool | Equipment check |
| backup_carriers_count | int | Waterfall depth |
| spot_rate_premium_pct | float | Market premium |
| lane_acceptance_rate | float | Trailing acceptance (all carriers) |
| market_tightness | float | OTRI proxy (0=loose, 1=extreme) |
| primary_carrier_otp | float | On-time % trailing 90d |
| allocation_compliance_pct | float | Actual/committed ratio |

### Action Space
ACCEPT (0), REJECT (1), DEFER (2)

### Training Data Notes
Overlay must populate `lane_acceptance_rate` from tender reject history
and `market_tightness` from the freight rate model's `market_regime()`.

---

## 2. ShipmentTrackingTRM (SENSE)

### Canonical Problem
**State Estimation / Sensor Fusion** — combine multi-source tracking signals
into a single ETA with confidence interval. Anomaly detection for
tracking-lost, route-deviation, temperature-excursion.

### Industry Sources
- **project44** TrackedShipmentEvent schema — eventType taxonomy aligned with
  EDI 214 status codes [project44 API Reference v4]
- **FourKites** Dynamic ETA — XGBoost/LightGBM regression on historical
  transit features [FourKites Technical White Paper, 2023]
- **Conformal Prediction** for distribution-free intervals — Vovk et al.,
  "Algorithmic Learning in a Random World" (Springer, 2005)
- EDI 214 (Transportation Carrier Shipment Status) — status codes AG, X1,
  D1, SD, NS [ANSI X12]
- ELD Mandate (FMCSA 395.8) — guarantees GPS/HOS data availability for
  truck modes

### Implemented Algorithm
**Mode-aware threshold detection + conformal interval awareness +
carrier-reliability-weighted urgency.**

```
Mode-aware silence thresholds:
  FTL: 4h, LTL: 8h, PARCEL: 12h, FCL/LCL: 24h, BULK_OCEAN: 48h,
  AIR_STD: 2h, AIR_EXPRESS: 1h, RAIL_INTERMODAL: 12h

Temperature excursion: always ESCALATE (food safety override)

Late delivery with conformal awareness:
  If P90 still within window → lower urgency (may self-correct)
  Urgency discounted by carrier_reliability_score

At-risk: pct_complete < expected progress curve → MODIFY
```

### State Fields (`ShipmentTrackingState`)

| Field | Type | Source |
|---|---|---|
| shipment_status | str | Current status |
| planned_pickup/delivery | datetime | Plan of record |
| current_eta | datetime | Latest ETA |
| eta_p10/eta_p90 | datetime | Conformal bounds |
| current_lat/lon | float | GPS/telematics |
| last_update_hours_ago | float | Silence detector |
| total_miles/miles_remaining | float | Route context |
| pct_complete | float | Progress |
| carrier_otp_pct | float | Reliability weight |
| carrier_reliability_score | float | Composite carrier quality |
| transport_mode | str | Mode-aware thresholds |
| is_temperature_sensitive | bool | Commodity |
| current_temp/temp_min/temp_max | float | IoT sensors |

### Action Space
ACCEPT (0), ESCALATE (3), MODIFY (4)

---

## 3. DemandSensingTRM (SENSE)

### Canonical Problem
**Short-Horizon Forecast Adjustment with Exogenous Regressors.**
Distinct from demand planning (monthly baseline). Operates on 1–7 day
horizon using today's order book and leading indicators.

### Industry Sources
- **E2open / Terra Technology** demand sensing — pattern recognition /
  nearest-neighbor approach [E2open Product Documentation]
- **Trigg's Tracking Signal** — D.W. Trigg, "Monitoring a Forecasting
  System," Operational Research Quarterly, 15(3), 1964
- **CUSUM** (Cumulative Sum) for changepoint detection — Page, E.S.,
  "Continuous Inspection Schemes," Biometrika, 41(1/2), 1954
- **Holt-Winters** triple exponential smoothing for baseline —
  Winters, P.R., Management Science, 6(3), 1960
- **Croston's Method** for intermittent demand — Croston, J.D.,
  Operational Research Quarterly, 23(3), 1972
- VICS/CPFR (Collaborative Planning, Forecasting, and Replenishment)
  — GS1 standards
- EDI 852 (Product Activity Data) — retailer POS/inventory sharing

### Implemented Algorithm
**Priority-ordered signal evaluation with Trigg's tracking signal +
order pipeline velocity + asymmetric loss.**

```
1. Order pipeline velocity: if 24h bookings differ >15% from prior period
   → MODIFY with asymmetric correction (under-forecast 60%, over 40%)
2. Trigg's tracking signal: if |cum_error / MAD| > 4 → structural bias
   → MODIFY with asymmetric correction
3. Signal-type-specific: if signal_type provided with confidence > 0.5
   → MODIFY proportional to magnitude × confidence
4. Simple bias: if |bias| > 15% → MODIFY (asymmetric)
5. WoW change: if |wow_change| > 20% → MODIFY
6. Peak season + high MAPE → precautionary +10% buffer
7. Otherwise → ACCEPT
```

### State Fields (`DemandSensingState`)

| Field | Type | Source |
|---|---|---|
| lane_id | int | Lane scope |
| forecast_loads | float | Current P50 forecast |
| forecast_mape | float | Rolling accuracy |
| actual_loads_current/prior | float | Trailing actuals |
| week_over_week_change_pct | float | Trend |
| rolling_4wk_avg | float | Smoothed baseline |
| signal_type | str | VOLUME_SURGE, SEASONAL_SHIFT |
| signal_magnitude/confidence | float | External signal |
| is_peak_season | bool | Calendar |
| order_pipeline_loads_24h | float | Bookings last 24h |
| order_pipeline_loads_prior_24h | float | Same window prior period |
| cumulative_forecast_error | float | Running Σ(forecast-actual) |
| cumulative_mad | float | Running MAD |

### Action Space
ACCEPT (0), MODIFY (4)

---

## 4. CapacityBufferTRM (ASSESS)

### Canonical Problem
**Newsvendor Problem** extended to multi-period, multi-lane capacity
reservation. Transportation equivalent of safety stock sizing.

### Industry Sources
- **Newsvendor critical ratio**: Arrow, K.J., Harris, T., Marschak, J.,
  "Optimal Inventory Policy," Econometrica, 19(3), 1951
- **Oracle OTM** lane-level capacity planning module
- **SAP TM** capacity planning with configurable safety margins
- **FreightWaves SONAR** OTVI/OTRI indices for market tightness
  [FreightWaves SONAR documentation]
- **Conformal Prediction** for distribution-free forecast intervals —
  Vovk et al., 2005 (ibid)

### Implemented Algorithm
**Multiplicative buffer sizing with conformal P90-P50 as primary
volatility signal.**

```
1. Conformal spread: if (P90 - P50) / P50 > 10% → multiply by (1 + spread)
2. Tender reject rate: if > 15% → multiply by r/(1-r) (newsvendor-inspired)
3. Demand CV fallback (when conformal unavailable): if CV > 0.3 → multiply
4. Peak season → × 1.2
5. Demand trend > 0.1 → multiply by (1 + trend × 0.2)
6. Recent capacity misses ≥ 3 → × 1.25
7. Consistently oversupplied → × 0.85
```

### State Fields (`CapacityBufferState`)
Key fields: `baseline_buffer_loads`, `forecast_loads`, `forecast_p10`,
`forecast_p90`, `recent_tender_reject_rate`, `demand_cv`, `demand_trend`,
`is_peak_season`, `recent_capacity_miss_count`

### Action Space
ACCEPT (0), MODIFY (4)

---

## 5. ExceptionManagementTRM (ASSESS)

### Canonical Problem
**Multi-Criteria Decision Analysis (MCDA)** for resolution strategy
selection + **dynamic job-shop rescheduling** for cascading exceptions.

### Industry Sources
- **Oracle OTM** exception severity attribute + carrier collaboration workflow
- **SAP TM** tolerance-based auto-close + event-based replanning
- ANSI X12 EDI 214 status codes for exception detection (SD, A3, OA)
- NMFC exception codes for freight classification exceptions
- **Carmack Amendment** (49 USC §14706) — US domestic cargo claims
- **Hague-Visby Rules** — international ocean cargo claims

### Implemented Algorithm
**Weighted composite priority scoring with financial gating + tolerance-based
auto-resolution.**

```
Priority score (0–1):
  0.25 × severity_factor       {LOW:0.15, MEDIUM:0.40, HIGH:0.70, CRITICAL:1.0}
  0.20 × financial_factor      (penalty + cost_impact) / (value + penalty)
  0.30 × time_criticality      1 - exp(-2 / delivery_window_hrs)  ← sigmoid ramp
  0.15 × customer_factor       max(0.2, 1 - (tier-1)×0.2)
  0.10 × cascade_factor        min(1, downstream_affected / 5)

Temperature excursion: always ESCALATE (food safety override)
Delay within appointment_buffer_hrs + LOW/MEDIUM + no cascade → auto-ACCEPT
Priority ≥ 0.75 → RETENDER (if possible) or ESCALATE
Priority 0.50–0.75 → REROUTE (if available) or RETENDER (with financial gate)
Priority < 0.50 → ACCEPT
```

### State Fields (`ExceptionManagementState`)
Key additions: `shipment_value`, `penalty_exposure`, `expedite_cost_estimate`,
`appointment_buffer_hrs`, `downstream_shipments_affected`, `customer_tier`

### Action Space
ACCEPT (0), ESCALATE (3), RETENDER (5), REROUTE (6)

---

## 6. FreightProcurementTRM (ACQUIRE)

### Canonical Problem
**Weighted Bipartite Matching with Sequential Fallback** — carrier waterfall
tendering (routing guide).

### Industry Sources
- **Oracle OTM** Carrier Selection Workbench — weighted composite scoring
- **SAP TM** Freight Agreement Selection
- EDI 204 (Motor Carrier Load Tender), 990 (Accept/Decline) [ANSI X12]
- **FreightWaves SONAR** OTVI/OTRI — market tightness indices
- **DAT RateView** — lane benchmark rates
- **Greenscreens** — dynamic rate benchmarking

### Implemented Algorithm
**Composite carrier scoring + acceptance-rate gating + market-adjusted
waterfall depth + lead-time fast-path.**

```
Carrier composite score (0–1):
  0.35 × cost_factor       benchmark / rate
  0.25 × otp_factor        on-time %
  0.20 × acceptance_factor  historical accept rate
  0.10 × compliance_factor  allocation compliance
  0.10 × capacity_factor    equipment availability

Lead-time < 4h → skip waterfall, direct to spot
Primary: skip if acceptance_pct < 50% (known decliner)
Backups: filter by acceptance ≥ 50%, rank by composite score
Market tightness > 0.6 → shorten waterfall by 1 attempt
Spot: gate against DAT benchmark (not contract), threshold 25% normal / 35% tight
Exhausted → ESCALATE to broker
```

### State Fields (`FreightProcurementState`)
Key fields: `primary_carrier_acceptance_pct`, `market_tightness`,
`dat_benchmark_rate`, `lead_time_hours`, `backup_carriers[]` with
`acceptance_pct`, `rate`, `otp_pct`, `allocation_compliance`

### Action Space
ACCEPT (0), ESCALATE (3)

---

## 7. BrokerRoutingTRM (ACQUIRE)

### Canonical Problem
**Newsvendor under incomplete information** — accept known higher broker
price now, or wait for uncertain spot?

### Industry Sources
- **CH Robinson** Navisphere — broker panel management
- **XPO / RXO** digital brokerage platform
- **Uber Freight** / **Convoy (Flexport)** — digital freight matching APIs
- **DAT One** / **Truckstop.com (Turvo)** — spot boards
- **Kuebix** auto-approval within threshold

### Implemented Algorithm
**Reliability-adjusted cost + time-urgency scaling + market-adjusted
premium threshold + DAT benchmark anchoring.**

```
P1–P2 → most reliable broker regardless of cost
Score: expected_cost = rate × (1 + fallthrough_rate × rebooking_premium)

Premium threshold = base (25%)
  + market adjustment (tightness × 15%)
  + time urgency (max(0, (6 - hours_to_pickup) × 10%))

Premium vs. DAT benchmark (not contract rate).
Above threshold → ESCALATE for approval
Below → ACCEPT
```

### Action Space
ACCEPT (0), ESCALATE (3)

---

## 8. DockSchedulingTRM (PROTECT)

### Canonical Problem
**Parallel Machine Scheduling Problem (PMSP)** with time windows — variant
of `P|r_j, d_j|C_max`.

### Industry Sources
- **SAP EWM** time-slot booking with backend schedule compaction
- **Manhattan Associates WMS** constraint-based scheduling
- **Descartes** appointment management
- Detention industry standard: 2h free time, $50–$100/hr thereafter

### Implemented Algorithm
**Priority-ordered evaluation with equipment compatibility + detention
cost projection + yard capacity + congestion mode-switching.**

```
P1–P2 → always ACCEPT
Equipment compatibility: no reefer doors available → DEFER
Yard full (0 spots) + drop-trailer → DEFER (hold offsite)
Projected detention > $150 → MODIFY (expedite turnaround)
Queue > 3 + live-load → MODIFY (switch to drop-trailer)
Detention risk > 50% → MODIFY (monitor turnaround)
Utilization > 85% + P4–P5 → DEFER
Otherwise → ACCEPT
```

### Action Space
ACCEPT (0), DEFER (2), MODIFY (4)

---

## 9. LoadBuildTRM (BUILD)

### Canonical Problem
**3D Bin Packing + Set Partitioning** — two-stage: cluster compatible
shipments, then pack onto equipment.

### Industry Sources
- **Clarke-Wright Savings Algorithm** — Clarke, G., Wright, J.W.,
  "Scheduling of Vehicles from a Central Depot to a Number of Delivery
  Points," Operations Research, 12(4), 1964
- **Oracle OTM** two-stage consolidation (pooling + optimization)
- **Manhattan Associates** column generation for set partitioning
- **SAP TM** Transportation Optimizer (Clarke-Wright variant + bin-packing)
- NMFC (National Motor Freight Classification) — freight class rating
- DOT 49 CFR 177.848 — hazmat segregation table
- Industry FTL breakeven: 8,000–12,000 lbs / 10–12 pallets

### Implemented Algorithm
**FTL/LTL crossover economics + Clarke-Wright multi-stop savings +
compatibility gating.**

```
1. Hazmat/temp conflict → REJECT
2. Over-capacity (weight or volume >95%) → SPLIT
3. Multi-stop delivery window conflict → SPLIT
4. FTL/LTL crossover:
   - If FTL rate < LTL sum → "FTL"
   - If volume_LTL rate < FTL → "VOLUME_LTL"
   - Weight < 8K lbs + < 10 pallets → "LTL"
   - Weight 8K–12K → "VOLUME_LTL"
   - Weight > 12K → "FTL"
5. Multi-stop savings (Clarke-Wright inspired):
   savings = individual_FTL_total - (one_FTL + stop_off_charges)
6. Total savings > 0 + fits → CONSOLIDATE (stop limit check)
7. Single shipment below FTL breakeven → ACCEPT as LTL
8. Underutilized (<50% weight, single ship) → DEFER for consolidation
9. Otherwise → ACCEPT
```

### Action Space
ACCEPT (0), REJECT (1), DEFER (2), CONSOLIDATE (7), SPLIT (8)

---

## 10. IntermodalTransferTRM (BUILD)

### Canonical Problem
**Multi-Modal Freight Network Flow** — discrete mode choice between OTR,
intermodal (truck+rail), rail-carload, ocean.

### Industry Sources
- **Oracle OTM** multi-modal graph with label-correcting shortest path
- **SAP TM** route determination engine with multi-leg templates
- **J.B. Hunt 360** / **Hub Group** intermodal pricing engines
- **Surface Transportation Board (STB)** railroad service metrics
- Industry thresholds: 550+ miles viable, 800–1500 sweet spot;
  drayage $250–$500 local, $4–$6/mi beyond; drayage = 25–40% of total
  intermodal cost [Journal of Transport Economics and Policy, various]
- DOT hazmat restrictions on rail

### Implemented Algorithm
**Sequential eligibility gates + drayage-decomposed all-in cost with
inventory carrying cost adjustment.**

```
Hard gates (reject immediately):
1. Hazmat → REJECT (rail restrictions)
2. Temperature-controlled → REJECT (limited reefer availability)
3. Ramp proximity > 100 miles → REJECT (drayage kills economics)
4. Lane distance < 500 miles → REJECT (too short)
5. Ramp congestion > 70% → REJECT
6. Transit time exceeds delivery window → REJECT
7. Reliability < 80% + window < 2 days → REJECT

Cost evaluation:
  effective_savings = truck_rate - intermodal_rate - carrying_cost
  carrying_cost = commodity_value × 44000 × (10%/365) × transit_penalty_days
  threshold: 8% standard, 5% on long-haul > 800mi

Above threshold + transit feasible → ACCEPT
Below → REJECT
```

### Action Space
ACCEPT (0), REJECT (1)

---

## 11. EquipmentRepositionTRM (REFLECT)

### Canonical Problem
**Dynamic Multi-Commodity Minimum-Cost Network Flow** — empty vehicle
redistribution problem (EVR).

### Industry Sources
- **Powell, W.B.** "A Stochastic Formulation of the Dynamic Assignment
  Problem, with an Application to Truckload Motor Carriers,"
  Transportation Science, 30(3), 1996
- **Crainic, T.G., Gendreau, M., Dejax, P.** "Dynamic and Stochastic
  Models for the Allocation of Empty Containers," Operations Research,
  41(1), 1993
- **Oracle OTM** continuous-move optimizer — set-covering for loaded +
  empty leg chaining
- **TMW Suite / TruckMate** power board — nearest-load greedy dispatch
- **McLeod LoadMaster** load-matching by proximity + HOS + home-time
- Industry ROI threshold: 1.3× minimum (strategic), 1.5× standard, 2.0×
  speculative [industry practice via ATA Technology & Maintenance Council]

### Implemented Algorithm
**Demand-weighted surplus/deficit with ROI-gated greedy matching.**

The heuristic is the dispatch function; the *data generation* in the
overlay generator computes demand from SCP staging:

```
1. Equipment on-hand by (site, eq_type) — query equipment table
2. Demand next 7 days by (site, eq_type) — rolling window on SCP shipments
   with temperature category → equipment type mapping
3. Surplus = on_hand - demand_7d. Deficit = demand_7d - on_hand
4. For each eq_type: sort sites by surplus (desc) and deficit (asc)
5. Greedy: move from highest-surplus to highest-deficit
   - ROI = spot_premium_at_deficit / reposition_cost
   - If ROI < 1.3 → skip
   - Else → emit EquipmentMove row, update current_site_id
```

The dispatch.py heuristic:
```
surplus = source_equipment - source_demand_7d
deficit = target_demand_7d - target_equipment
If no surplus or no deficit → HOLD
Fleet util > 90% + deficit → urgent REPOSITION (0.8 urgency)
ROI > 1.5 → REPOSITION
ROI > 1.0 + short distance < 200mi → REPOSITION
Otherwise → HOLD
```

### State Fields (`EquipmentRepositionState`)
Key fields: `source_equipment_count`, `source_demand_next_7d`,
`target_equipment_count`, `target_demand_next_7d`, `reposition_miles`,
`reposition_cost`, `cost_of_not_repositioning`, `fleet_utilization_pct`

### Action Space
HOLD (10), REPOSITION (9)

---

## Consolidated References

### Academic / OR Literature
1. Arrow, K.J., Harris, T., Marschak, J. (1951). "Optimal Inventory Policy." *Econometrica*, 19(3).
2. Clarke, G., Wright, J.W. (1964). "Scheduling of Vehicles from a Central Depot." *Operations Research*, 12(4).
3. Crainic, T.G., Gendreau, M., Dejax, P. (1993). "Dynamic and Stochastic Models for Empty Containers." *Operations Research*, 41(1).
4. Croston, J.D. (1972). "Forecasting and Stock Control for Intermittent Demands." *Operational Research Quarterly*, 23(3).
5. Page, E.S. (1954). "Continuous Inspection Schemes." *Biometrika*, 41(1/2).
6. Powell, W.B. (1996). "Stochastic Formulation of Dynamic Assignment." *Transportation Science*, 30(3).
7. Trigg, D.W. (1964). "Monitoring a Forecasting System." *Operational Research Quarterly*, 15(3).
8. Vovk, V., Gammerman, A., Shafer, G. (2005). *Algorithmic Learning in a Random World*. Springer.
9. Winters, P.R. (1960). "Forecasting Sales by Exponentially Weighted Moving Averages." *Management Science*, 6(3).

### Industry Standards
10. ANSI X12 EDI: 204 (Tender), 214 (Status), 990 (Response), 210 (Invoice), 492 (Rate Contract), 852 (Product Activity)
11. NMFC — National Motor Freight Classification
12. DOT 49 CFR 177.848 — Hazmat Segregation Table
13. FMCSA ELD Mandate (49 CFR 395.8) — Hours of Service / Telematics
14. GS1/VICS CPFR — Collaborative Planning, Forecasting, and Replenishment
15. Carmack Amendment (49 USC §14706) — US Domestic Cargo Claims
16. Hague-Visby Rules — International Ocean Cargo Claims

### Production TMS Systems Consulted
17. Oracle OTM (Transportation Management) — Capacity Consumption, Carrier Selection Workbench, Continuous Move Optimizer, Multi-Modal Graph
18. SAP TM (Transportation Management) — TCB, Freight Agreement Selection, Tolerance-Based Auto-Close, Event-Based Replanning
19. Manhattan Associates TMS — Column Generation, Constraint-Based Scheduling
20. BluJay (E2open) TMS — Routing Guide Waterfall, Demand Sensing
21. MercuryGate TMS — Rule-Based Consolidation, Carrier Scoring
22. Descartes TMS — Appointment Management
23. Trimble TMW / TruckMate — Equipment Availability, Power Board
24. McLeod LoadMaster — Load Matching, Proximity Scoring

### Market Data Sources Referenced
25. FreightWaves SONAR — OTVI (Outbound Tender Volume Index), OTRI (Outbound Tender Reject Index)
26. DAT RateView — Lane Benchmark Rates
27. Greenscreens — Dynamic Rate Benchmarking
28. EIA — Weekly Diesel Retail Price (for FSC models)
29. Surface Transportation Board — Railroad Service Metrics

### Visibility / Integration Platforms
30. project44 — TrackedShipmentEvent schema, ETA ML models, CapacityProviderIdentifier
31. FourKites — Dynamic ETA (XGBoost/LightGBM), Visibility Events
32. Descartes MacroPoint — Carrier Location Tracking

---

## Changelog — v2.2 (2026-05-02): LaneVolumeForecast segmentation extension

The 2026-04-29 `LaneVolumeForecastTRM` shipped lane-aggregate forecasts only — no mode / equipment segmentation. v2.2 closes that gap per industry norms (e2open / Blue Yonder / Oracle OTM / MercuryGate / SAP TM all forecast multi-axis: mode mandatory, equipment within FTL mandatory).

**Design choice — share-of-aggregate, not Cartesian:** the L1 TRM still forecasts a single `loads` aggregate per (lane, period); mode + equipment are emitted as **mix shares** that split the aggregate at output time. This matches DAT / ACT Research publication patterns, is cleaner numerically on sparse lanes, and does not perturb the Syntetos-Boylan classification logic.

**New `LaneVolumeForecastState` fields** (all additive, default empty / 0):
- `mode_history: Dict[str, float]` — EWMA-smoothed share by mode (FTL / LTL / PARCEL / INTERMODAL / OCEAN / RAIL / AIR) over trailing 8 periods
- `equipment_history: Dict[str, float]` — EWMA-smoothed share by equipment (DRY_VAN / REEFER / FLATBED / TANKER / CONTAINER_20 / CONTAINER_40), inside FTL
- `mean_weight_kg_per_load: float` — historical mean weight per load
- `mean_volume_m3_per_load: float` — historical mean volume per load
- `proposed_weight_kg_p50: float` — optional caller-supplied weight P50 override
- `proposed_volume_m3_p50: float` — optional caller-supplied volume P50 override

**New helper `compute_segmented_loads(state, aggregate_loads_p50)`** in `dispatch.py`. Returns `Dict[str, Any]` with three `segmentation_method` values:
- `"ewma_share_history"` — multi-mode mix applied
- `"single_mode_passthrough"` — one mode dominates (≥ 95% share)
- `"no_segmentation"` — history unavailable; aggregate is the only signal

**Output keys merged into `TMSHeuristicDecision.params_used`** for every action path (DEFER / ESCALATE / MODIFY / ACCEPT):
- `forecast_loads_p50` — primary primitive
- `mode_mix`, `mode_loads_p50` — per-mode forecast (always present, single-key dict in no-segmentation case)
- `equipment_mix`, `equipment_loads_p50` — per-equipment forecast within FTL (empty if non-FTL)
- `forecast_weight_kg_p50` — secondary, P50 only (per industry norm)
- `forecast_volume_m3_p50` — secondary, P50 only

**Service level** is intentionally NOT a forecast facet — it's a planning constraint set at L4 customer-tier policy.

**Phase 2 follow-ons (not in v2.2):** mode-cross-elasticity (LTL → FTL substitution as size grows), per-mode confidence bands, L3 Tactical Forecast Service that consumes these L1 outputs.

See `Autonomy-Core/docs/MIGRATION_REGISTER.md` §3.36 for the full design rationale + industry citations.
