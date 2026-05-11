#!/usr/bin/env python3
"""
Generate TRM Training Corpus — per-TRM parquet files of (state, action, reward)

Sweeps each TRM's state dataclass across realistic distributions, labels
each sample with the deterministic heuristic teacher, and writes parquet
files suitable for TRM behavioral-cloning training.

Architecture (matches TMS_TRM_TRAINING_DATA_SPECIFICATION.md):
  State Sampler → compute_tms_decision() → (state_dict, action, reward) → parquet

Unlike SCP (8 teachers per state for consensus), TMS uses a single
deterministic teacher per state — appropriate because transportation
heuristics are discrete (waterfall, threshold-based) with less legitimate
disagreement across methods.

Usage:
    python scripts/pretraining/generate_tms_corpus.py --trm capacity_promise --samples 50000
    python scripts/pretraining/generate_tms_corpus.py --all --samples 50000
    python scripts/pretraining/generate_tms_corpus.py --all --samples 50000 --output-dir /data/tms_corpus

Output: one parquet file per TRM in output_dir, e.g.:
    capacity_promise_50000.parquet
    freight_procurement_50000.parquet
    ...
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from dataclasses import asdict, fields
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    HAS_PARQUET = True
except ImportError:
    HAS_PARQUET = False

# GPU image has torch installed, so normal imports work fine.
# CPU-only environments without torch need the installed package to have
# torch stubbed or optional — for now this script targets the GPU container.
from autonomy_tms_heuristics.library.dispatch import (
    compute_tms_decision, Actions,
)
from autonomy_tms_heuristics.library.base import (
    CapacityPromiseState,
    ShipmentTrackingState,
    DemandSensingState,
    CapacityBufferState,
    ExceptionManagementState,
    FreightProcurementState,
    BrokerRoutingState,
    DockSchedulingState,
    LoadBuildState,
    IntermodalTransferState,
    EquipmentRepositionState,
    LaneVolumeForecastState,
)

# Load tms_reward_weights via importlib so this script stays runnable
# in CPU dev (the powell package's __init__ transitively requires torch
# for SiteAgentModel; the reward-weights module itself is pure Python).
import importlib.util as _importlib_util
from types import ModuleType as _ModuleType
for _stub in ("app", "app.services", "app.services.powell"):
    sys.modules.setdefault(_stub, _ModuleType(_stub))
_rw_path = BACKEND_ROOT / "app" / "services" / "powell" / "tms_reward_weights.py"
_rw_spec = _importlib_util.spec_from_file_location(
    "app.services.powell.tms_reward_weights", _rw_path,
)
_rw_module = _importlib_util.module_from_spec(_rw_spec)
sys.modules["app.services.powell.tms_reward_weights"] = _rw_module
_rw_spec.loader.exec_module(_rw_module)
compute_native_tms_reward = _rw_module.compute_native_tms_reward

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("generate_tms_corpus")


# ============================================================================
# Curriculum phases — DAT-anchored transport-KPI bands
# ============================================================================
# Closes TMS_TRM_TRAINING_DATA_SPECIFICATION.md Open Item #5 — replaces
# SCP's scalar variance multipliers (0.15 / 0.40 / 0.75 on inventory mean)
# with per-knob distributional bands tuned to transport benchmarks:
#
#   * DAT Freight Rate Index — spot premium acceptance threshold <30 %;
#     broker premium alert >40 % over contract.
#   * Truckstop reject-rate tranches — >15 % triggers buffer expansion.
#   * BNSF / CSX service-level floors — intermodal reliability 80 %.
#   * FMCSA detention study (2018) — carrier dwell > free_time + 2h = risk.
#
# Phase semantics:
#   1. baseline   — stable market, contract-rate dominant, calm weather.
#                   Teaches the policy the easy path before it sees noise.
#   2. normal     — typical industry conditions; ≈ pre-curriculum ranges
#                   (preserves backward compatibility with existing
#                   checkpoints trained without --phase).
#   3. disruption — peak-season + capacity crisis; tail conditions.
#
# Phase 2 is the default to keep behaviour stable for callers that don't
# pass --phase.
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class PhaseConfig:
    """Curriculum phase bands. Used by phase-sensitive state samplers."""

    name: str
    # Spot premium over contract — DAT acceptance band.
    spot_premium_max: float
    # Market tightness — 0 loose, 1 tight.
    market_tightness_band: Tuple[float, float]
    # Tender reject rate — Truckstop tranches.
    reject_rate_max: float
    # Primary carrier on-time-performance — fleet quality at this phase.
    primary_carrier_otp_band: Tuple[float, float]
    # Intermodal reliability — BNSF / CSX service floor + tail.
    intermodal_reliability_band: Tuple[float, float]
    # Rail-ramp congestion — drayage / linehaul delays.
    ramp_congestion_band: Tuple[float, float]
    # Demand CV — lane volume variability.
    demand_cv_max: float
    # Exception severity weights — (LOW, MEDIUM, HIGH, CRITICAL) prob mass.
    exception_severity_weights: Tuple[int, int, int, int]


PHASES: Dict[int, PhaseConfig] = {
    1: PhaseConfig(
        name="phase1_baseline",
        spot_premium_max=0.08,
        market_tightness_band=(0.0, 0.30),
        reject_rate_max=0.08,
        primary_carrier_otp_band=(0.92, 0.99),
        intermodal_reliability_band=(0.90, 0.97),
        ramp_congestion_band=(0.0, 0.30),
        demand_cv_max=0.30,
        exception_severity_weights=(40, 35, 15, 10),
    ),
    2: PhaseConfig(
        name="phase2_normal",
        spot_premium_max=0.25,
        market_tightness_band=(0.10, 0.70),
        reject_rate_max=0.20,
        primary_carrier_otp_band=(0.82, 0.97),
        intermodal_reliability_band=(0.80, 0.95),
        ramp_congestion_band=(0.10, 0.70),
        demand_cv_max=0.55,
        exception_severity_weights=(20, 40, 25, 15),
    ),
    3: PhaseConfig(
        name="phase3_disruption",
        spot_premium_max=0.45,
        market_tightness_band=(0.50, 1.0),
        reject_rate_max=0.45,
        primary_carrier_otp_band=(0.70, 0.92),
        intermodal_reliability_band=(0.65, 0.90),
        ramp_congestion_band=(0.50, 1.0),
        demand_cv_max=0.85,
        exception_severity_weights=(10, 25, 35, 30),
    ),
}

DEFAULT_PHASE = 2


# ============================================================================
# State samplers — one per TRM
# ============================================================================
# Each returns a populated state dataclass with realistic field distributions.
# Distributions are calibrated against DAT / FreightWaves benchmarks and the
# Food Dist network topology. Samplers that consume curriculum-driven KPIs
# (capacity / market / reliability / exception severity) take a ``phase``
# kwarg defaulting to ``DEFAULT_PHASE`` (= 2, normal).

def _sample_capacity_promise(rng: random.Random, phase: int = DEFAULT_PHASE) -> CapacityPromiseState:
    cfg = PHASES[phase]
    total_cap = rng.randint(5, 50)
    booked = rng.randint(0, total_cap)
    return CapacityPromiseState(
        shipment_id=rng.randint(1, 100000),
        lane_id=rng.randint(1, 50),
        requested_loads=rng.randint(1, 5),
        mode=rng.choice(["FTL", "LTL", "INTERMODAL"]),
        priority=rng.choices([1, 2, 3, 4, 5], weights=[5, 10, 50, 25, 10])[0],
        committed_capacity=booked,
        total_capacity=total_cap,
        buffer_capacity=rng.randint(0, max(1, total_cap // 5)),
        forecast_loads=rng.randint(3, 30),
        booked_loads=booked,
        primary_carrier_available=rng.random() > 0.15,
        backup_carriers_count=rng.randint(0, 5),
        spot_rate_premium_pct=rng.uniform(0.0, cfg.spot_premium_max),
        lane_acceptance_rate=rng.uniform(0.50, 0.98),
        market_tightness=rng.uniform(*cfg.market_tightness_band),
        primary_carrier_otp=rng.uniform(*cfg.primary_carrier_otp_band),
        allocation_compliance_pct=rng.uniform(0.50, 1.20),
    )


def _sample_shipment_tracking(rng: random.Random) -> ShipmentTrackingState:
    now = datetime.utcnow()
    planned_delivery = now + timedelta(hours=rng.uniform(4, 96))
    pct = rng.uniform(0.0, 1.0)
    eta_offset = rng.gauss(0, 4)
    return ShipmentTrackingState(
        shipment_id=rng.randint(1, 100000),
        shipment_status=rng.choices(["IN_TRANSIT", "DELIVERED", "HELD"], weights=[80, 15, 5])[0],
        planned_delivery=planned_delivery,
        current_eta=planned_delivery + timedelta(hours=eta_offset),
        eta_p10=planned_delivery + timedelta(hours=eta_offset - 3),
        eta_p90=planned_delivery + timedelta(hours=eta_offset + 3),
        last_update_hours_ago=rng.expovariate(0.5),
        total_miles=rng.uniform(100, 2500),
        pct_complete=pct,
        miles_remaining=rng.uniform(0, 2000) * (1 - pct),
        carrier_otp_pct=rng.uniform(0.70, 0.99),
        carrier_reliability_score=rng.uniform(0.50, 0.99),
        is_temperature_sensitive=rng.random() < 0.35,
        current_temp=rng.uniform(-15, 80) if rng.random() < 0.3 else None,
        temp_min=-10.0 if rng.random() < 0.35 else None,
        temp_max=40.0 if rng.random() < 0.35 else None,
        transport_mode=rng.choice(["FTL", "LTL", "FCL", "AIR_STD", "RAIL_INTERMODAL"]),
    )


def _sample_demand_sensing(rng: random.Random) -> DemandSensingState:
    forecast = rng.uniform(5, 100)
    actual = forecast * rng.uniform(0.5, 1.5)
    return DemandSensingState(
        lane_id=rng.randint(1, 50),
        forecast_loads=forecast,
        forecast_mape=rng.uniform(0.05, 0.40),
        actual_loads_current=actual,
        actual_loads_prior=forecast * rng.uniform(0.7, 1.3),
        week_over_week_change_pct=rng.uniform(-0.40, 0.50),
        rolling_4wk_avg=forecast * rng.uniform(0.8, 1.2),
        signal_type=rng.choice(["", "VOLUME_SURGE", "SEASONAL_SHIFT", "PROMO_LIFT"]),
        signal_magnitude=rng.uniform(0, 0.3) if rng.random() < 0.3 else 0.0,
        signal_confidence=rng.uniform(0.3, 0.95) if rng.random() < 0.3 else 0.0,
        is_peak_season=rng.random() < 0.25,
        order_pipeline_loads_24h=rng.uniform(0, 20),
        order_pipeline_loads_prior_24h=rng.uniform(0, 20),
        cumulative_forecast_error=rng.gauss(0, forecast * 0.3),
        cumulative_mad=max(0.1, abs(rng.gauss(0, forecast * 0.15))),
    )


def _sample_capacity_buffer(rng: random.Random, phase: int = DEFAULT_PHASE) -> CapacityBufferState:
    cfg = PHASES[phase]
    forecast = rng.randint(5, 50)
    return CapacityBufferState(
        lane_id=rng.randint(1, 50),
        baseline_buffer_loads=rng.randint(1, 15),
        forecast_loads=forecast,
        forecast_p10=max(0, forecast - rng.randint(2, 10)),
        forecast_p90=forecast + rng.randint(2, 15),
        committed_loads=rng.randint(0, forecast),
        contract_capacity=forecast + rng.randint(0, 20),
        recent_tender_reject_rate=rng.uniform(0.0, cfg.reject_rate_max),
        demand_cv=rng.uniform(0.05, cfg.demand_cv_max),
        demand_trend=rng.uniform(-0.3, 0.5),
        is_peak_season=rng.random() < (0.10 if phase == 1 else 0.25 if phase == 2 else 0.50),
        recent_capacity_miss_count=rng.choices([0, 1, 2, 3, 4, 5], weights=[40, 25, 15, 10, 5, 5])[0],
    )


def _sample_exception_management(rng: random.Random, phase: int = DEFAULT_PHASE) -> ExceptionManagementState:
    cfg = PHASES[phase]
    exc_types = ["LATE_DELIVERY", "LATE_PICKUP", "DETENTION", "CARRIER_BREAKDOWN",
                 "WEATHER_DELAY", "REFUSED", "TEMPERATURE_EXCURSION", "DAMAGE"]
    severities = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    return ExceptionManagementState(
        exception_type=rng.choice(exc_types),
        severity=rng.choices(severities, weights=list(cfg.exception_severity_weights))[0],
        estimated_delay_hrs=rng.expovariate(0.2),
        estimated_cost_impact=rng.uniform(50, 5000),
        revenue_at_risk=rng.uniform(200, 20000),
        shipment_priority=rng.choices([1, 2, 3, 4, 5], weights=[5, 10, 50, 25, 10])[0],
        delivery_window_remaining_hrs=rng.uniform(0, 48),
        carrier_reliability_score=rng.uniform(0.50, 0.99),
        can_retender=rng.random() > 0.20,
        alternate_carriers_available=rng.randint(0, 5),
        can_reroute=rng.random() > 0.60,
        shipment_value=rng.uniform(500, 50000),
        penalty_exposure=rng.uniform(0, 5000),
        expedite_cost_estimate=rng.uniform(200, 3000),
        appointment_buffer_hrs=rng.choice([1.0, 2.0, 4.0]),
        downstream_shipments_affected=rng.choices([0, 1, 2, 3, 5], weights=[50, 25, 15, 7, 3])[0],
        customer_tier=rng.choices([1, 2, 3, 4, 5], weights=[10, 15, 40, 25, 10])[0],
    )


def _sample_freight_procurement(rng: random.Random, phase: int = DEFAULT_PHASE) -> FreightProcurementState:
    cfg = PHASES[phase]
    contract_rate = rng.uniform(1500, 4000)
    spot_multiplier = 1.0 + rng.uniform(-0.10, cfg.spot_premium_max)
    return FreightProcurementState(
        load_id=rng.randint(1, 100000),
        lane_id=rng.randint(1, 50),
        mode=rng.choice(["FTL", "LTL"]),
        weight=rng.uniform(5000, 44000),
        lead_time_hours=rng.uniform(1, 96),
        primary_carrier_id=rng.randint(1, 50) if rng.random() > 0.1 else None,
        primary_carrier_rate=contract_rate,
        primary_carrier_acceptance_pct=rng.uniform(0.30, 0.98),
        backup_carriers=[
            {"id": rng.randint(51, 100), "rate": contract_rate * rng.uniform(1.0, 1.15),
             "acceptance_pct": rng.uniform(0.40, 0.95), "priority": i + 2,
             "otp_pct": rng.uniform(*cfg.primary_carrier_otp_band)}
            for i in range(rng.randint(0, 4))
        ],
        spot_rate=contract_rate * spot_multiplier,
        contract_rate=contract_rate,
        market_tightness=rng.uniform(*cfg.market_tightness_band),
        dat_benchmark_rate=contract_rate * rng.uniform(0.95, 1.10),
        tender_attempt=rng.randint(1, 5),
        max_tender_attempts=rng.choice([3, 4, 5]),
        hours_to_tender_deadline=rng.uniform(2, 48),
    )


def _sample_broker_routing(rng: random.Random) -> BrokerRoutingState:
    contract_rate = rng.uniform(1500, 4000)
    return BrokerRoutingState(
        load_id=rng.randint(1, 100000),
        hours_to_pickup=rng.uniform(1, 48),
        available_brokers=[
            {"id": rng.randint(1, 20), "name": f"Broker_{i}",
             "rate": contract_rate * rng.uniform(1.05, 1.45),
             "reliability": rng.uniform(0.60, 0.98),
             "fallthrough_rate": rng.uniform(0.02, 0.20),
             "coverage_score": rng.uniform(0.50, 0.95)}
            for i in range(rng.randint(0, 5))
        ],
        contract_rate=contract_rate,
        spot_rate=contract_rate * rng.uniform(0.90, 1.35),
        shipment_priority=rng.choices([1, 2, 3, 4, 5], weights=[5, 10, 50, 25, 10])[0],
        market_tightness=rng.uniform(0.0, 1.0),
        dat_benchmark_rate=contract_rate * rng.uniform(0.95, 1.10),
    )


def _sample_dock_scheduling(rng: random.Random) -> DockSchedulingState:
    total_doors = rng.randint(4, 20)
    return DockSchedulingState(
        facility_id=rng.randint(1, 10),
        appointment_type=rng.choice(["PICKUP", "DELIVERY", "CROSS_DOCK"]),
        total_dock_doors=total_doors,
        available_dock_doors=rng.randint(0, total_doors),
        yard_spots_total=rng.randint(20, 100),
        yard_spots_available=rng.randint(0, 50),
        appointments_in_window=rng.randint(0, 10),
        current_queue_depth=rng.choices([0, 1, 2, 3, 4, 5, 6], weights=[20, 25, 20, 15, 10, 5, 5])[0],
        shipment_priority=rng.choices([1, 2, 3, 4, 5], weights=[5, 10, 50, 25, 10])[0],
        is_live_load=rng.random() > 0.30,
        estimated_load_time_minutes=rng.uniform(30, 120),
        free_time_minutes=rng.choice([60, 90, 120, 180]),
        detention_rate_per_hour=rng.choice([50, 75, 100]),
        carrier_avg_dwell_minutes=rng.uniform(30, 240),
        equipment_type=rng.choice(["DRY_VAN", "REEFER", "FLATBED"]),
    )


def _sample_load_build(rng: random.Random) -> LoadBuildState:
    n_ships = rng.randint(1, 8)
    weight_per = rng.uniform(2000, 12000)
    ftl_rate = rng.uniform(1500, 4000)
    return LoadBuildState(
        shipment_ids=list(range(n_ships)),
        lane_id=rng.randint(1, 50),
        mode=rng.choice(["FTL", "LTL"]),
        equipment_type=rng.choice(["DRY_VAN", "REEFER"]),
        total_weight=weight_per * n_ships,
        total_volume=rng.uniform(100, 2700),
        total_pallets=rng.randint(1, 26),
        shipment_count=n_ships,
        has_hazmat_conflict=rng.random() < 0.03,
        has_temp_conflict=rng.random() < 0.05,
        has_destination_conflict=rng.random() < 0.10,
        max_stops=rng.choice([3, 4, 5]),
        consolidation_window_hours=rng.choice([12, 24, 48]),
        ftl_rate=ftl_rate,
        ltl_rate_sum=ftl_rate * rng.uniform(0.8, 2.0) if n_ships > 1 else ftl_rate * 0.6,
        consolidation_savings=rng.uniform(-200, 1500) if n_ships > 1 else 0,
        stop_count=min(n_ships, rng.randint(1, 5)),
        stop_off_charge_per_stop=rng.choice([50, 75, 100, 150]),
        delivery_windows_compatible=rng.random() > 0.10,
        volume_ltl_rate=ftl_rate * rng.uniform(0.6, 0.95) if rng.random() < 0.3 else 0,
    )


def _sample_intermodal_transfer(rng: random.Random, phase: int = DEFAULT_PHASE) -> IntermodalTransferState:
    cfg = PHASES[phase]
    truck_miles = rng.uniform(200, 2500)
    truck_rate = truck_miles * rng.uniform(2.0, 3.5)
    im_rate = truck_rate * rng.uniform(0.60, 1.05)
    return IntermodalTransferState(
        shipment_id=rng.randint(1, 100000),
        total_truck_miles=truck_miles,
        truck_rate=truck_rate,
        intermodal_rate=im_rate,
        drayage_rate_origin=rng.uniform(200, 600),
        drayage_rate_dest=rng.uniform(200, 600),
        truck_transit_days=truck_miles / rng.uniform(400, 600),
        intermodal_transit_days=truck_miles / rng.uniform(250, 400),
        delivery_window_days=rng.uniform(0, 5),
        ramp_congestion_level=rng.uniform(*cfg.ramp_congestion_band),
        intermodal_reliability_pct=rng.uniform(*cfg.intermodal_reliability_band),
        is_hazmat=rng.random() < 0.05,
        is_temperature_controlled=rng.random() < 0.30,
        commodity_value_per_lb=rng.uniform(0.0, 5.0),
        origin_ramp_distance_miles=rng.uniform(5, 150),
        dest_ramp_distance_miles=rng.uniform(5, 150),
    )


def _sample_equipment_reposition(rng: random.Random) -> EquipmentRepositionState:
    src_eq = rng.randint(5, 40)
    tgt_eq = rng.randint(0, 30)
    src_demand = rng.randint(2, 25)
    tgt_demand = rng.randint(2, 25)
    miles = rng.uniform(50, 800)
    cost = miles * rng.uniform(1.5, 2.5)
    return EquipmentRepositionState(
        equipment_type=rng.choice(["DRY_VAN", "REEFER"]),
        source_equipment_count=src_eq,
        source_demand_next_7d=src_demand,
        target_equipment_count=tgt_eq,
        target_demand_next_7d=tgt_demand,
        reposition_miles=miles,
        reposition_cost=cost,
        cost_of_not_repositioning=cost * rng.uniform(0.5, 3.5),
        total_fleet_size=rng.randint(100, 1000),
        fleet_utilization_pct=rng.uniform(0.50, 0.98),
        breakeven_loads=rng.randint(1, 5),
    )


def _sample_lane_volume_forecast(rng: random.Random) -> LaneVolumeForecastState:
    """Sample a customer-facing lane's forecast-orchestration state.

    Distribution covers the full Syntetos-Boylan space (smooth, erratic,
    intermittent, lumpy) plus NEW (cold-start) and DECLINING (taper) lanes,
    weighted toward smooth/erratic which dominate real shipper outbound
    traffic. Recent forecast-performance fields (MAPE, conformal coverage,
    interval width) are sampled to exercise all four heuristic actions
    (ACCEPT / MODIFY / ESCALATE / DEFER).
    """
    # 1) Pick a demand archetype to sample around.
    archetype = rng.choices(
        ["smooth", "erratic", "intermittent", "lumpy", "new", "declining"],
        weights=[40, 20, 12, 10, 12, 6],
    )[0]

    # Defaults; overridden per archetype below.
    weeks = rng.randint(8, 156)
    mean_d = rng.uniform(5.0, 60.0)
    std_d = mean_d * rng.uniform(0.05, 0.40)
    adi = 1.0
    cv2 = 0.05
    nonzero = 1.0
    trend = rng.uniform(-0.02, 0.04)
    seasonal = rng.uniform(0.0, 0.6)
    last_actual = max(0.0, rng.gauss(mean_d, std_d))

    if archetype == "smooth":
        adi = rng.uniform(1.00, 1.25)
        cv2 = rng.uniform(0.02, 0.40)
        nonzero = rng.uniform(0.85, 1.0)
    elif archetype == "erratic":
        adi = rng.uniform(1.00, 1.30)
        cv2 = rng.uniform(0.50, 1.50)
        nonzero = rng.uniform(0.70, 0.95)
        std_d = mean_d * rng.uniform(0.40, 0.90)
    elif archetype == "intermittent":
        adi = rng.uniform(1.40, 3.50)
        cv2 = rng.uniform(0.05, 0.45)
        nonzero = rng.uniform(0.30, 0.65)
        std_d = mean_d * rng.uniform(0.10, 0.30)
    elif archetype == "lumpy":
        adi = rng.uniform(1.40, 4.00)
        cv2 = rng.uniform(0.55, 2.00)
        nonzero = rng.uniform(0.20, 0.55)
        std_d = mean_d * rng.uniform(0.50, 1.20)
    elif archetype == "new":
        weeks = rng.randint(0, 9)              # < 8 = NEW; allow 0-3 → DEFER
        adi = rng.uniform(1.00, 1.50)
        cv2 = rng.uniform(0.10, 0.80)
        nonzero = rng.uniform(0.50, 1.0)
        last_actual = max(0.0, rng.gauss(mean_d, std_d))
    elif archetype == "declining":
        # Persistent negative trend + recent actual << mean
        weeks = rng.randint(20, 100)
        adi = rng.uniform(1.00, 1.30)
        cv2 = rng.uniform(0.05, 0.40)
        nonzero = rng.uniform(0.70, 1.0)
        trend = rng.uniform(-0.20, -0.06)
        last_actual = mean_d * rng.uniform(0.10, 0.45)

    # 2) Recent forecast performance (varies by archetype; lumpy/intermittent
    #    have systematically wider intervals and worse MAPE).
    if archetype in ("smooth", "erratic"):
        trailing_mape = rng.uniform(0.05, 0.35)
        coverage = rng.uniform(0.70, 0.92)
        interval_width = rng.uniform(0.15, 0.80)
    elif archetype in ("intermittent", "lumpy"):
        trailing_mape = rng.uniform(0.20, 0.70)
        coverage = rng.uniform(0.55, 0.85)
        interval_width = rng.uniform(0.40, 2.50)
    else:  # new, declining
        trailing_mape = rng.uniform(0.15, 0.80)
        coverage = rng.uniform(0.45, 0.85)
        interval_width = rng.uniform(0.30, 1.80)

    # Occasionally inject a calibration drift (forces ESCALATE on coverage)
    if rng.random() < 0.05:
        coverage = rng.uniform(0.30, 0.55)

    # 3) Proposed forecast: roughly track recent actual with method-specific noise.
    p50 = max(0.5, last_actual * rng.uniform(0.85, 1.15) if last_actual > 0 else mean_d)
    half_width = p50 * interval_width / 2.0
    p10 = max(0.0, p50 - half_width)
    p90 = p50 + half_width

    # 4) Covariate availability — only smooth/erratic lanes typically have
    #    rate / market signals joined; intermittent lanes rarely do.
    has_rate = (archetype in ("smooth", "erratic")) and rng.random() < 0.55
    has_market = (archetype in ("smooth", "erratic")) and rng.random() < 0.40

    # 5) External signal overlay (sparse; mostly absent).
    if rng.random() < 0.18:
        signal_type = rng.choice(["PROMO_LIFT", "EVENT", "MARKET_SHIFT", "NPI", "EOL"])
        signal_magnitude = rng.uniform(0.05, 0.40) * (1 if rng.random() > 0.25 else -1)
        signal_confidence = rng.uniform(0.40, 0.95)
    else:
        signal_type = ""
        signal_magnitude = 0.0
        signal_confidence = 0.0

    # 6) Recommended method label — used only as a hint; the heuristic
    #    re-derives it inside compute_tms_decision.
    if archetype == "new":
        method = "AutoETS_coldstart"
    elif archetype == "declining":
        method = "TSB"
    elif archetype == "intermittent":
        method = "Croston"
    elif archetype == "lumpy":
        method = "TSB"
    elif has_rate or has_market:
        method = "LightGBM"
    else:
        method = "HoltWinters"

    return LaneVolumeForecastState(
        lane_id=rng.randint(1, 50),
        period_days=7,
        weeks_of_history=weeks,
        mean_demand=mean_d,
        demand_std=std_d,
        avg_demand_interval=adi,
        squared_cv=cv2,
        nonzero_period_pct=nonzero,
        trend_slope=trend,
        seasonal_strength=seasonal,
        is_peak_season=rng.random() < 0.25,
        forecast_method_in_use=method if weeks >= 8 else "",
        trailing_mape=trailing_mape,
        trailing_wape=trailing_mape * rng.uniform(0.85, 1.15),
        forecast_bias=rng.gauss(0.0, 0.10),
        conformal_coverage_p80=coverage,
        has_rate_covariate=has_rate,
        has_market_signal=has_market,
        has_calendar_features=True,
        signal_type=signal_type,
        signal_magnitude=signal_magnitude,
        signal_confidence=signal_confidence,
        proposed_forecast_p50=p50,
        proposed_forecast_p10=p10,
        proposed_forecast_p90=p90,
        proposed_method=method,
        last_period_actual=last_actual,
        forecast_interval_width_pct=interval_width,
    )


SAMPLERS = {
    "capacity_promise": (_sample_capacity_promise, CapacityPromiseState),
    "shipment_tracking": (_sample_shipment_tracking, ShipmentTrackingState),
    "demand_sensing": (_sample_demand_sensing, DemandSensingState),
    "capacity_buffer": (_sample_capacity_buffer, CapacityBufferState),
    "exception_management": (_sample_exception_management, ExceptionManagementState),
    "freight_procurement": (_sample_freight_procurement, FreightProcurementState),
    "broker_routing": (_sample_broker_routing, BrokerRoutingState),
    "dock_scheduling": (_sample_dock_scheduling, DockSchedulingState),
    "load_build": (_sample_load_build, LoadBuildState),
    "intermodal_transfer": (_sample_intermodal_transfer, IntermodalTransferState),
    "equipment_reposition": (_sample_equipment_reposition, EquipmentRepositionState),
    "lane_volume_forecast": (_sample_lane_volume_forecast, LaneVolumeForecastState),
}

ALL_TRMS = list(SAMPLERS.keys())


# ============================================================================
# Reward function
# ============================================================================

def compute_reward(action: int, urgency: float, quantity: float) -> float:
    """
    Reward signal for behavioral cloning.
    Positive for correct decisive actions, scaled by urgency.
    HOLD/DEFER penalized slightly to encourage action-taking (AIIO: agent always acts).
    """
    if action in (Actions.ACCEPT, Actions.CONSOLIDATE, Actions.REPOSITION,
                  Actions.RETENDER, Actions.REROUTE):
        return 1.0 + urgency * 0.5
    if action == Actions.ESCALATE:
        return 0.8 + urgency * 0.3
    if action in (Actions.MODIFY,):
        return 0.7 + urgency * 0.2
    if action in (Actions.DEFER, Actions.HOLD):
        return 0.3
    if action == Actions.REJECT:
        return 0.5 + urgency * 0.2
    return 0.5


# ============================================================================
# Corpus generator
# ============================================================================

def _state_to_flat_dict(state) -> Dict[str, Any]:
    """Flatten a state dataclass to a dict suitable for parquet columns.
    Handles datetime → ISO string, lists → JSON string, nested dicts → JSON."""
    import json
    d = {}
    for f in fields(state):
        val = getattr(state, f.name)
        if isinstance(val, datetime):
            val = val.isoformat()
        elif isinstance(val, date):
            val = val.isoformat()
        elif isinstance(val, (list, dict)):
            val = json.dumps(val)
        d[f"state_{f.name}"] = val
    return d


def _add_derived_features(row: Dict[str, Any], state: Any, trm_type: str) -> None:
    """
    Add derived numeric features that the heuristic internally computes.
    These are the signals the model actually needs to learn — raw datetime
    and JSON-list fields are not learnable by the MLP.
    """
    if trm_type == "shipment_tracking":
        # The heuristic decides on: is_late, hours_late, silence_over_threshold, pct_behind
        hours_to_delivery = state.hours_to_delivery() or 0.0
        is_late = 1.0 if state.is_late() else 0.0
        hours_late = 0.0
        if state.planned_delivery and state.current_eta:
            hours_late = max(0, (state.current_eta - state.planned_delivery).total_seconds() / 3600)
        # Conformal: is P90 also late?
        p90_late = 0.0
        if state.eta_p90 and state.planned_delivery:
            p90_late = 1.0 if state.eta_p90 > state.planned_delivery else 0.0
        row["derived_hours_to_delivery"] = hours_to_delivery
        row["derived_is_late"] = is_late
        row["derived_hours_late"] = hours_late
        row["derived_p90_also_late"] = p90_late
        row["derived_silence_ratio"] = state.last_update_hours_ago / max(0.1, hours_to_delivery) if hours_to_delivery > 0 else 0

    elif trm_type == "broker_routing":
        # The heuristic decides on: best broker rate/reliability, premium vs benchmark, time urgency
        brokers = state.available_brokers or []
        row["derived_num_brokers"] = len(brokers)
        if brokers:
            best_by_cost = min(brokers, key=lambda b: b.get("rate", float("inf")))
            best_by_rel = max(brokers, key=lambda b: b.get("reliability", 0))
            benchmark = state.dat_benchmark_rate or state.contract_rate or 1.0
            row["derived_best_rate"] = best_by_cost.get("rate", 0)
            row["derived_best_reliability"] = best_by_rel.get("reliability", 0)
            row["derived_best_rate_premium"] = (best_by_cost.get("rate", 0) - benchmark) / max(1, benchmark)
            row["derived_avg_fallthrough"] = sum(b.get("fallthrough_rate", 0.1) for b in brokers) / len(brokers)
        else:
            row["derived_best_rate"] = 0.0
            row["derived_best_reliability"] = 0.0
            row["derived_best_rate_premium"] = 0.0
            row["derived_avg_fallthrough"] = 0.0
        # Time urgency
        row["derived_time_urgency"] = max(0, (6 - state.hours_to_pickup) * 0.10) if state.hours_to_pickup < 6 else 0

    elif trm_type == "exception_management":
        # Pre-compute the composite priority score
        import math
        sev_map = {"LOW": 0.15, "MEDIUM": 0.40, "HIGH": 0.70, "CRITICAL": 1.0}
        sev_f = sev_map.get(state.severity, 0.40)
        total_exp = state.penalty_exposure + state.estimated_cost_impact
        fin_f = min(1.0, total_exp / max(1.0, state.shipment_value + state.penalty_exposure))
        if state.delivery_window_remaining_hrs <= 0:
            time_f = 1.0
        else:
            time_f = 1.0 - math.exp(-2.0 / max(0.1, state.delivery_window_remaining_hrs))
        cust_f = max(0.2, 1.0 - (state.customer_tier - 1) * 0.2)
        cascade_f = min(1.0, state.downstream_shipments_affected / 5.0)
        priority = 0.25 * sev_f + 0.20 * fin_f + 0.30 * time_f + 0.15 * cust_f + 0.10 * cascade_f
        row["derived_priority_score"] = priority
        row["derived_severity_factor"] = sev_f
        row["derived_financial_factor"] = fin_f
        row["derived_time_criticality"] = time_f
        row["derived_within_buffer"] = 1.0 if state.estimated_delay_hrs <= state.appointment_buffer_hrs else 0.0

    elif trm_type == "freight_procurement":
        # Composite carrier score for primary
        benchmark = state.dat_benchmark_rate or state.contract_rate or state.primary_carrier_rate
        if benchmark > 0 and state.primary_carrier_rate > 0:
            row["derived_primary_cost_factor"] = min(1.0, benchmark / state.primary_carrier_rate)
        else:
            row["derived_primary_cost_factor"] = 0.5
        row["derived_is_short_lead"] = 1.0 if state.lead_time_hours < 4 else 0.0
        row["derived_num_eligible_backups"] = sum(1 for b in state.backup_carriers if b.get("acceptance_pct", 0.8) >= 0.5)
        if state.spot_rate > 0 and benchmark > 0:
            row["derived_spot_premium_vs_benchmark"] = (state.spot_rate - benchmark) / benchmark
        else:
            row["derived_spot_premium_vs_benchmark"] = 0.0

    elif trm_type == "lane_volume_forecast":
        # Make Syntetos-Boylan class boundaries explicit so the BC MLP
        # doesn't have to rediscover them from ADI/CV² alone.
        adi = max(1.0, state.avg_demand_interval)
        cv2 = max(0.0, state.squared_cv)
        is_new = state.weeks_of_history < 8
        is_declining = (
            state.trend_slope < -0.05
            and state.mean_demand > 0
            and state.last_period_actual < state.mean_demand * 0.5
            and not is_new
        )
        is_smooth = not is_new and not is_declining and adi < 1.32 and cv2 < 0.49
        is_erratic = not is_new and not is_declining and adi < 1.32 and cv2 >= 0.49
        is_intermittent = not is_new and not is_declining and adi >= 1.32 and cv2 < 0.49
        is_lumpy = not is_new and not is_declining and adi >= 1.32 and cv2 >= 0.49
        row["derived_is_new"] = 1.0 if is_new else 0.0
        row["derived_is_declining"] = 1.0 if is_declining else 0.0
        row["derived_is_smooth"] = 1.0 if is_smooth else 0.0
        row["derived_is_erratic"] = 1.0 if is_erratic else 0.0
        row["derived_is_intermittent"] = 1.0 if is_intermittent else 0.0
        row["derived_is_lumpy"] = 1.0 if is_lumpy else 0.0
        # Volatility composite — helps the MLP learn the wide-interval ESCALATE branch
        row["derived_volatility"] = adi * cv2
        # Has-any-covariate flag for the LightGBM eligibility branch
        row["derived_has_any_covariate"] = 1.0 if (state.has_rate_covariate or state.has_market_signal) else 0.0
        # Has-active-signal flag for the MODIFY signal-overlay branch
        row["derived_has_active_signal"] = 1.0 if (
            state.signal_type and state.signal_magnitude != 0 and state.signal_confidence > 0.5
        ) else 0.0
        # Peak-season precaution composite
        row["derived_peak_season_mape"] = (1.0 if state.is_peak_season else 0.0) * state.trailing_mape


def generate_corpus(
    trm_type: str,
    n_samples: int,
    seed: int = 42,
    output_dir: Optional[Path] = None,
    phase: int = DEFAULT_PHASE,
) -> Path:
    """Generate n_samples of (state, action, reward) for one TRM.

    ``phase`` selects the curriculum band (1 baseline / 2 normal /
    3 disruption). Samplers that don't consume curriculum bands
    ignore the kwarg. See ``PHASES`` above.
    """
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {sorted(PHASES)}; got {phase}")
    sampler_fn, state_cls = SAMPLERS[trm_type]
    rng = random.Random(seed)
    logger.info(f"{trm_type}: phase={phase} ({PHASES[phase].name})")

    rows: List[Dict[str, Any]] = []
    t0 = time.time()
    for i in range(n_samples):
        try:
            state = sampler_fn(rng, phase=phase)
        except TypeError:
            # Sampler not yet phase-aware — fall back to legacy signature.
            state = sampler_fn(rng)
        decision = compute_tms_decision(trm_type, state)

        row = _state_to_flat_dict(state)
        _add_derived_features(row, state, trm_type)
        row["action"] = decision.action
        row["action_name"] = {
            0: "ACCEPT", 1: "REJECT", 2: "DEFER", 3: "ESCALATE",
            4: "MODIFY", 5: "RETENDER", 6: "REROUTE", 7: "CONSOLIDATE",
            8: "SPLIT", 9: "REPOSITION", 10: "HOLD",
        }.get(decision.action, f"UNKNOWN_{decision.action}")
        row["quantity"] = decision.quantity
        row["urgency"] = decision.urgency
        row["confidence"] = decision.confidence
        row["reasoning"] = decision.reasoning
        native = compute_native_tms_reward(trm_type, state, decision)
        row["reward"] = (
            native
            if native is not None
            else compute_reward(decision.action, decision.urgency, decision.quantity)
        )
        rows.append(row)

        if (i + 1) % 10000 == 0:
            logger.info(f"  {trm_type}: {i+1}/{n_samples} ({time.time()-t0:.1f}s)")

    elapsed = time.time() - t0
    logger.info(f"{trm_type}: {n_samples} samples generated in {elapsed:.1f}s")

    # Action distribution
    from collections import Counter
    action_dist = Counter(r["action_name"] for r in rows)
    logger.info(f"  Action distribution: {dict(action_dist)}")

    # Write output
    if output_dir is None:
        output_dir = Path(BACKEND_ROOT) / "training_data" / "tms_corpus"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{trm_type}_{n_samples}.parquet"

    if HAS_PARQUET:
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, out_path)
        logger.info(f"  Written: {out_path} ({out_path.stat().st_size / 1024:.0f} KB)")
    else:
        # Fallback to JSON lines if pyarrow not available
        import json
        out_path = out_path.with_suffix(".jsonl")
        with open(out_path, "w") as f:
            for row in rows:
                f.write(json.dumps(row, default=str) + "\n")
        logger.info(f"  Written: {out_path} (jsonl fallback, {out_path.stat().st_size / 1024:.0f} KB)")

    return out_path


# ============================================================================
# Entry points
# ============================================================================

def main():
    ap = argparse.ArgumentParser(description="Generate TMS TRM training corpus")
    ap.add_argument("--trm", type=str, default=None, choices=ALL_TRMS,
                    help="Single TRM to generate (default: all)")
    ap.add_argument("--all", action="store_true", help="Generate all 11 TRMs")
    ap.add_argument("--samples", type=int, default=50000,
                    help="Samples per TRM (default: 50000)")
    ap.add_argument("--seed", type=int, default=42, help="Random seed")
    ap.add_argument("--output-dir", type=str, default=None,
                    help="Output directory (default: training_data/tms_corpus/)")
    ap.add_argument(
        "--phase", type=int, default=DEFAULT_PHASE, choices=sorted(PHASES),
        help=(
            "Curriculum phase: 1 baseline (stable market), "
            "2 normal (typical industry, default), "
            "3 disruption (peak / capacity crisis)"
        ),
    )
    args = ap.parse_args()

    if not args.trm and not args.all:
        ap.error("Specify --trm <name> or --all")

    trms = ALL_TRMS if args.all else [args.trm]
    output_dir = Path(args.output_dir) if args.output_dir else None

    logger.info(
        f"Generating corpus: {len(trms)} TRMs × {args.samples} samples, "
        f"seed={args.seed}, phase={args.phase}"
    )
    paths = []
    for trm in trms:
        p = generate_corpus(
            trm, args.samples, seed=args.seed, output_dir=output_dir,
            phase=args.phase,
        )
        paths.append(p)

    logger.info(f"Done. {len(paths)} files written.")
    for p in paths:
        logger.info(f"  {p}")


if __name__ == "__main__":
    main()
