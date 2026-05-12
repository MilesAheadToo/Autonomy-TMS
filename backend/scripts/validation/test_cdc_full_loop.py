#!/usr/bin/env python3
"""D1: CDC Full Loop Validation — OutcomeCollector -> CDCMonitor -> Retraining pipeline"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from datetime import date, datetime, timedelta

passed = 0
failed = 0
errors = []

def test(name, condition, detail=""):
    global passed, failed, errors
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        errors.append(f"{name}: {detail}")
        print(f"  FAIL: {name} — {detail}")

if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"D1: CDC Full Loop Validation")
    print(f"{'='*60}")

    # ── 1. Core OutcomeCollectorService methods ───────────────────────
    print("\n--- Core OutcomeCollectorService ---")
    try:
        # §3.64 → §3.69: full outcome-collection arc lives in Core.
        # Legacy plane-side outcome_collector.py was deleted under §3.69.
        from azirella_data_model.governance.causal import (
            OutcomeCollectorService as CoreOutcomeCollectorService,
        )
        test("Core OutcomeCollectorService importable", True)
        test("Core collect_site_agent_outcomes() method exists (§3.64)",
             hasattr(CoreOutcomeCollectorService, 'collect_site_agent_outcomes'),
             "Missing Core collect_site_agent_outcomes method")
        test("Core collect_trm_outcomes() method exists (§3.66)",
             hasattr(CoreOutcomeCollectorService, 'collect_trm_outcomes'),
             "Missing Core collect_trm_outcomes method")
        test("Core collect_service_commitment_outcomes() exists (§3.67)",
             hasattr(CoreOutcomeCollectorService, 'collect_service_commitment_outcomes'),
             "Missing Core collect_service_commitment_outcomes method")
        test("Core collect_skill_outcomes() method exists (§3.69)",
             hasattr(CoreOutcomeCollectorService, 'collect_skill_outcomes'),
             "Missing Core collect_skill_outcomes method")
    except ImportError as e:
        test("Core OutcomeCollectorService importable", False, str(e))

    # ── 2. Outcome delays for all 11 TRM types ────────────────────────
    print("\n--- TRM Outcome Delays ---")
    try:
        from azirella_data_model.governance.causal import (
            TRM_DECISION_HORIZONS as TRM_OUTCOME_DELAY,
            SITE_AGENT_DECISION_HORIZONS as OUTCOME_DELAY,
        )
        expected_trm_types = {
            # 11 execution TRMs
            "atp", "rebalance", "po", "order_tracking", "mo", "to",
            "quality", "maintenance", "subcontracting",
            "forecast_adjustment", "inventory_buffer",
            # 4 planning TRMs
            "demand_adjustment", "inventory_adjustment",
            "supply_adjustment", "rccp_adjustment",
        }
        test("TRM_OUTCOME_DELAY has 15 entries (11 exec + 4 planning)",
             len(TRM_OUTCOME_DELAY) == 15,
             f"Found {len(TRM_OUTCOME_DELAY)}: {set(TRM_OUTCOME_DELAY.keys())}")
        test("TRM_OUTCOME_DELAY covers all 15 TRM types",
             set(TRM_OUTCOME_DELAY.keys()) == expected_trm_types,
             f"Missing: {expected_trm_types - set(TRM_OUTCOME_DELAY.keys())}")
        test("SiteAgentDecision OUTCOME_DELAY has 4 entries",
             len(OUTCOME_DELAY) == 4,
             f"Found {len(OUTCOME_DELAY)}")
    except ImportError as e:
        test("TRM_OUTCOME_DELAY importable", False, str(e))

    # ── 3. CDCMonitor import and thresholds ────────────────────────────
    print("\n--- CDCMonitor ---")
    try:
        from app.services.powell.cdc_monitor import CDCMonitor, CDCConfig, TriggerReason, ReplanAction
        test("CDCMonitor importable", True)
    except ImportError as e:
        test("CDCMonitor importable", False, str(e))

    try:
        config = CDCConfig()
        threshold_count = len(config.thresholds)
        test("CDCConfig has thresholds dict",
             isinstance(config.thresholds, dict),
             f"Type: {type(config.thresholds)}")
        # Expect at least 6 thresholds (demand_deviation, inventory_ratio_low,
        # inventory_ratio_high, service_level_drop, lead_time_increase, backlog_growth_days)
        test(f"CDCConfig has >= 6 thresholds ({threshold_count} found)",
             threshold_count >= 6,
             f"Only {threshold_count}: {list(config.thresholds.keys())}")
        test("demand_deviation threshold exists",
             "demand_deviation" in config.thresholds,
             "Missing demand_deviation")
        test("inventory_ratio_low threshold exists",
             "inventory_ratio_low" in config.thresholds,
             "Missing inventory_ratio_low")
        test("service_level_drop threshold exists",
             "service_level_drop" in config.thresholds,
             "Missing service_level_drop")
        test("cooldown_hours is 24",
             config.cooldown_hours == 24,
             f"Got {config.cooldown_hours}")
    except Exception as e:
        test("CDCConfig instantiation", False, str(e))

    # ── 4. TriggerReason and ReplanAction enums ────────────────────────
    print("\n--- CDC Enums ---")
    try:
        reasons = [r.value for r in TriggerReason]
        test("TriggerReason has DEMAND_DEVIATION",
             "demand_deviation" in reasons,
             f"Values: {reasons}")
        test("TriggerReason has SERVICE_LEVEL_DROP",
             "service_level_drop" in reasons,
             f"Values: {reasons}")

        actions = [a.value for a in ReplanAction]
        test("ReplanAction has FULL_CFA",
             "full_cfa" in actions,
             f"Values: {actions}")
        test("ReplanAction has VERTICAL_OPERATIONAL",
             "vertical_operational" in actions,
             f"Values: {actions}")
        test("ReplanAction has VERTICAL_STRATEGIC",
             "vertical_strategic" in actions,
             f"Values: {actions}")
    except NameError as e:
        test("TriggerReason/ReplanAction enums", False, str(e))

    # ── 5. CDCRetrainingService and constants ──────────────────────────
    print("\n--- CDCRetrainingService ---")
    try:
        from app.services.powell.cdc_retraining_service import (
            CDCRetrainingService,
            MIN_TRAINING_EXPERIENCES,
            RETRAIN_COOLDOWN_HOURS,
            MAX_REGRESSION_PCT,
        )
        test("CDCRetrainingService importable", True)
        test("MIN_TRAINING_EXPERIENCES is 100",
             MIN_TRAINING_EXPERIENCES == 100,
             f"Got {MIN_TRAINING_EXPERIENCES}")
        test("RETRAIN_COOLDOWN_HOURS is 6",
             RETRAIN_COOLDOWN_HOURS == 6,
             f"Got {RETRAIN_COOLDOWN_HOURS}")
        test("MAX_REGRESSION_PCT is 0.10",
             MAX_REGRESSION_PCT == 0.10,
             f"Got {MAX_REGRESSION_PCT}")
        test("evaluate_retraining_need() method exists",
             hasattr(CDCRetrainingService, 'evaluate_retraining_need'),
             "Missing method")
    except ImportError as e:
        test("CDCRetrainingService importable", False, str(e))

    # ── 6. Relearning jobs and schedules ───────────────────────────────
    print("\n--- Relearning Jobs ---")
    try:
        from app.services.powell.relearning_jobs import register_relearning_jobs
        test("register_relearning_jobs importable", True)
    except ImportError as e:
        test("register_relearning_jobs importable", False, str(e))

    # Verify job functions exist in the module
    try:
        import app.services.powell.relearning_jobs as rj
        test("_run_outcome_collection function exists",
             hasattr(rj, '_run_outcome_collection'),
             "Missing _run_outcome_collection")
        test("_run_trm_outcome_collection function exists",
             hasattr(rj, '_run_trm_outcome_collection'),
             "Missing _run_trm_outcome_collection")
        test("_run_cdt_calibration function exists",
             hasattr(rj, '_run_cdt_calibration'),
             "Missing _run_cdt_calibration")
        test("_run_cdc_retraining function exists",
             hasattr(rj, '_run_cdc_retraining'),
             "Missing _run_cdc_retraining")
        test("_run_skill_outcome_collection function exists",
             hasattr(rj, '_run_skill_outcome_collection'),
             "Missing _run_skill_outcome_collection")
        test("_run_escalation_arbiter function exists",
             hasattr(rj, '_run_escalation_arbiter'),
             "Missing _run_escalation_arbiter")
        test("_run_cfa_optimization function exists",
             hasattr(rj, '_run_cfa_optimization'),
             "Missing _run_cfa_optimization")
    except Exception as e:
        test("Relearning job functions", False, str(e))

    # ── 7. CDCTriggerLog model ─────────────────────────────────────────
    print("\n--- CDC Trigger Log Model ---")
    try:
        from app.models.powell_decision import CDCTriggerLog
        test("CDCTriggerLog model importable", True)
        test("CDCTriggerLog has __tablename__",
             hasattr(CDCTriggerLog, '__tablename__'),
             "Missing __tablename__")
        test("CDCTriggerLog table is powell_cdc_trigger_log",
             CDCTriggerLog.__tablename__ == "powell_cdc_trigger_log",
             f"Got {CDCTriggerLog.__tablename__}")
    except ImportError as e:
        test("CDCTriggerLog importable", False, str(e))

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print("Failures:")
        for e in errors:
            print(f"  - {e}")
    print(f"{'='*60}")
    sys.exit(0 if failed == 0 else 1)
