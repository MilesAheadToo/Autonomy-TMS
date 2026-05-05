"""
Powell Relearning Job Registration

Registers CDC relearning loop jobs with APScheduler:
- Hourly at :30: Outcome collection for TRM decisions (SiteAgentDecision path)
- Hourly at :32: TRM outcome collection for all 11 powell_*_decisions tables
- Hourly at :33: Skill outcome collection (decision_embeddings, Claude Skills path)
- Hourly at :35: CDT calibration (incremental, after outcomes collected)
- Every 2h at :40: Escalation Arbiter evaluation (vertical escalation detection)
- Daily at 02:40: Causal matching for Tier 2 override signal strength
- Daily at 03:00: GNN orchestration cycle (S&OP + Execution → directive broadcast)
- Every 6h at :45: CDC-triggered retraining evaluation
- Weekly Sunday 03:30: Data drift monitor (long-horizon distributional shift detection)
- Daily at 04:00: Demo date shift (keep demo data dates fresh)

Part of the Powell SDAM feedback loop:
  Decision → Wait → Observe outcome → Compute reward → Calibrate CDT → Retrain if warranted

The Escalation Arbiter adds vertical escalation (see docs/ESCALATION_ARCHITECTURE.md):
  Persistent TRM drift → Diagnose → Operational (tGNN refresh) OR Strategic (S&OP review)
"""

from typing import TYPE_CHECKING
import logging
from datetime import datetime

from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from app.services.sync_scheduler_service import SyncSchedulerService

logger = logging.getLogger(__name__)


def register_relearning_jobs(scheduler_service: 'SyncSchedulerService') -> None:
    """
    Register Powell relearning loop jobs with the scheduler.

    Jobs:
    - outcome_collector: Hourly at :30 — compute actual outcomes (SiteAgentDecision)
    - trm_outcome_collector: Hourly at :32 — compute outcomes for all 11 powell_*_decisions
    - skill_outcome_collector: Hourly at :33 — compute outcomes for Skills decisions
    - cdt_calibration: Hourly at :35 — incremental CDT calibration from new outcomes
    - cdc_retraining: Every 6h at :45 — evaluate and execute retraining if warranted
    """
    scheduler = scheduler_service._scheduler

    if not scheduler:
        logger.warning("Scheduler not available, relearning jobs not registered")
        return

    # Hourly: Outcome collection — SiteAgentDecision path (at :30)
    scheduler.add_job(
        func=_run_outcome_collection,
        trigger=CronTrigger(minute=30),
        id="powell_outcome_collector",
        name="Powell: Outcome Collection (hourly)",
        replace_existing=True,
        misfire_grace_time=1800,
    )
    logger.info("Registered Powell outcome collection job (hourly at :30)")

    # Hourly: TRM outcome collection — all 11 powell_*_decisions tables (at :32)
    scheduler.add_job(
        func=_run_trm_outcome_collection,
        trigger=CronTrigger(minute=32),
        id="powell_trm_outcome_collector",
        name="Powell: TRM Outcome Collection (hourly)",
        replace_existing=True,
        misfire_grace_time=1800,
    )
    logger.info("Registered Powell TRM outcome collection job (hourly at :32)")

    # Hourly: CDT calibration — after outcomes are collected (at :35)
    scheduler.add_job(
        func=_run_cdt_calibration,
        trigger=CronTrigger(minute=35),
        id="powell_cdt_calibration",
        name="Powell: CDT Calibration (hourly)",
        replace_existing=True,
        misfire_grace_time=1800,
    )
    logger.info("Registered Powell CDT calibration job (hourly at :35)")

    # Daily at 02:40: Causal matching — propensity-score matched pairs for Tier 2
    scheduler.add_job(
        func=_run_causal_matching,
        trigger=CronTrigger(hour=2, minute=40),
        id="powell_causal_matching",
        name="Powell: Causal Matching (daily)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Registered Powell causal matching job (daily at 02:40)")

    # PR-5.E (2026-05-05) — daily GNN orchestration cron disabled.
    # The cycle's tactical phase relied on TacticalHiveCoordinator and
    # the 4 SCP-shape tactical tGNN services that PR-5.E deleted; the
    # remaining S&OP-only phase produces directives no TMS plane code
    # consumes. Re-enable when §1.13 / §3.41 land TMS-shape tactical
    # tier services in Core and the broadcast has live consumers.
    # The orchestration function (_run_gnn_orchestration) is still
    # imported below so the manual REST trigger remains callable for
    # forensic / development use.

    # Hourly: Skill outcome collection — decision_embeddings (at :33)
    scheduler.add_job(
        func=_run_skill_outcome_collection,
        trigger=CronTrigger(minute=33),
        id="powell_skill_outcome_collector",
        name="Powell: Skill Outcome Collection (hourly)",
        replace_existing=True,
        misfire_grace_time=1800,
    )
    logger.info("Registered Powell skill outcome collection job (hourly at :33)")

    # Every 2 hours at :40: Escalation Arbiter evaluation
    # Detects persistent TRM drift and routes to tGNN/S&OP replanning.
    # See docs/ESCALATION_ARCHITECTURE.md
    scheduler.add_job(
        func=_run_escalation_arbiter,
        trigger=CronTrigger(hour="*/2", minute=40),
        id="powell_escalation_arbiter",
        name="Powell: Escalation Arbiter (every 2h)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Registered Powell Escalation Arbiter job (every 2h at :40)")

    # Every 6 hours: Retraining evaluation (at :45)
    scheduler.add_job(
        func=_run_cdc_retraining,
        trigger=CronTrigger(hour="0,6,12,18", minute=45),
        id="powell_cdc_retraining",
        name="Powell: CDC Retraining Evaluation (every 6h)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Registered Powell CDC retraining job (every 6h at :45)")

    # Weekly Sunday 04:00: CFA Policy Optimization — re-optimize θ parameters
    # Uses PolicyOptimizer (Differential Evolution) to find optimal inventory
    # policy parameters across all active configs. Lokad principle: periodically
    # re-optimize control variables against the latest stochastic distributions.
    scheduler.add_job(
        func=_run_cfa_optimization,
        trigger=CronTrigger(day_of_week="sun", hour=4, minute=0),
        id="powell_cfa_optimization",
        name="Powell: CFA Policy Optimization (weekly)",
        replace_existing=True,
        misfire_grace_time=7200,
    )
    logger.info("Registered Powell CFA policy optimization job (Sunday at 04:00)")

    # Weekly Sunday 03:30: Data Drift Monitor — long-horizon distributional shift detection
    # "Canary in the coal mine" — detects input/error distribution shifts weeks before CDC fires
    # Scans 28/56/84-day windows for demand drift, forecast error drift, calibration drift
    # HIGH/CRITICAL results → PowellEscalationLog(escalation_level="strategic")
    scheduler.add_job(
        func=_run_data_drift_scan,
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=30),
        id="powell_data_drift_scan",
        name="DataDrift: Weekly distributional shift scan",
        replace_existing=True,
        misfire_grace_time=7200,  # 2h grace — can run later on Sunday if missed
    )
    logger.info("Registered DataDrift weekly scan job (Sunday at 03:30)")

    # Hourly at :25: Site tGNN (Layer 1.5) inference for all active sites
    # Runs BEFORE outcome collection (:30) so that urgency adjustments
    # are in place before the next decision cycle
    scheduler.add_job(
        func=_run_site_tgnn_inference,
        trigger=CronTrigger(minute=25),
        id="powell_site_tgnn_inference",
        name="Powell: Site tGNN Inference (hourly, Layer 1.5)",
        replace_existing=True,
        misfire_grace_time=1800,
    )
    logger.info("Registered Site tGNN inference job (hourly at :25)")

    # Every 12 hours: Site tGNN training check
    # Evaluates whether sufficient MultiHeadTrace data exists to train/retrain
    scheduler.add_job(
        func=_run_site_tgnn_training_check,
        trigger=CronTrigger(hour="6,18", minute=50),
        id="powell_site_tgnn_training",
        name="Powell: Site tGNN Training Check (every 12h)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Registered Site tGNN training check job (every 12h at :50)")

    # Every 4 hours: Risk alert condition monitoring
    # Re-evaluates INFORMED risk alerts — auto-resolves (ACTIONED) if condition cleared
    scheduler.add_job(
        func=_run_risk_condition_monitor,
        trigger=CronTrigger(hour="2,6,10,14,18,22", minute=15),
        id="risk_condition_monitor",
        name="Risk: Condition Monitor (every 4h)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Registered risk condition monitor job (every 4h at :15)")

    # Daily at 05:30: External Signal Intelligence — outside-in planning data refresh
    # Fetches weather, economic indicators, energy prices, geopolitical events,
    # consumer sentiment, and regulatory signals from free public APIs.
    # Runs after CFA optimization (04:00) so fresh signals inform the next day's context.
    scheduler.add_job(
        func=_run_external_signal_refresh,
        trigger=CronTrigger(hour=5, minute=30),
        id="external_signal_refresh",
        name="External: Outside-In Signal Refresh (daily)",
        replace_existing=True,
        misfire_grace_time=7200,
    )
    logger.info("Registered external signal daily refresh job (daily at 05:30)")

    # 9. Experiential Knowledge — Override pattern detection + lifecycle (daily 03:15)
    scheduler.add_job(
        func=_run_experiential_knowledge_detection,
        trigger=CronTrigger(hour=3, minute=15),
        id="experiential_knowledge_detection",
        name="EK: Override Pattern Detection (daily)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Registered experiential knowledge detection job (daily at 03:15)")

    # Daily at 04:00: Demo date shift — keep demo data dates fresh
    # Shifts all date/timestamp columns forward by the number of elapsed days
    # since the last shift. Only affects tenants with a demo_date_shift_log entry.
    scheduler.add_job(
        func=_run_demo_date_shift,
        trigger=CronTrigger(hour=4, minute=0),
        id="demo_date_shift_daily",
        name="Demo: Date Shift (daily)",
        replace_existing=True,
        misfire_grace_time=7200,
    )
    logger.info("Registered demo date shift job (daily at 04:00)")

    # L2 Terminal Coordinator (hourly at :05)
    # Walks every terminal hub for every (tenant, config), computes
    # composite_health KPI snapshot, persists to terminal_health_signal,
    # and emits short-TTL urgency overrides when policy thresholds
    # breach. L1 TRMs read terminal_urgency_override from their state-
    # builders; absent override = multiplier 1.0 (neutral).
    scheduler.add_job(
        func=_run_terminal_coordinator,
        trigger=CronTrigger(minute=5),
        id="terminal_coordinator_cycle",
        name="L2: Terminal Coordinator Cycle (hourly)",
        replace_existing=True,
        misfire_grace_time=600,
    )
    logger.info("Registered L2 terminal coordinator job (hourly at :05)")

    # AD-11 closed-loop: LanePerformanceActuals on Load.DELIVERED
    # transitions (hourly at :10). Walks (tenant, config) pairs, finds
    # loads delivered in the trailing 24h window, idempotently records
    # one row per delivery. Closed-loop reward signal for TMS's lane-
    # performance conformal predictors and SCP's transit-time priors.
    scheduler.add_job(
        func=_run_lane_performance_recording,
        trigger=CronTrigger(minute=10),
        id="lane_performance_recording",
        name="AD-11: LanePerformanceActuals (hourly)",
        replace_existing=True,
        misfire_grace_time=600,
    )
    logger.info("Registered AD-11 lane-performance recording job (hourly at :10)")

    # Conformal auto-calibration (every 12h at 04:15 and 16:15)
    # Refreshes P10/P50/P90 intervals for TMS TRMs (capacity_buffer,
    # demand_sensing, equipment_reposition, shipment_tracking) from
    # outcome-measured agent_decisions. Only variables with ≥20 samples
    # get recalibrated — smaller buckets log-and-skip rather than
    # substitute a default (no-fallbacks invariant).
    scheduler.add_job(
        func=_run_conformal_autocalibration,
        trigger=CronTrigger(hour="4,16", minute=15),
        id="conformal_autocalibration",
        name="Conformal: Auto-Calibration (every 12h)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Registered conformal auto-calibration job (every 12h at :15)")


# ---------------------------------------------------------------------------
# Job execution functions
# ---------------------------------------------------------------------------

def _run_outcome_collection() -> None:
    """Collect outcomes for TRM decisions across all sites (SiteAgentDecision path)."""
    from app.db.session import SessionLocal

    logger.info("Starting scheduled outcome collection")

    db = SessionLocal()
    try:
        from app.services.powell.outcome_collector import OutcomeCollectorService

        service = OutcomeCollectorService(db)
        stats = service.collect_outcomes()
        logger.info(
            f"Outcome collection completed: "
            f"{stats['succeeded']} computed, {stats['failed']} failed "
            f"out of {stats['processed']} processed"
        )
    except Exception as e:
        logger.error(f"Outcome collection job failed: {e}")
    finally:
        db.close()


def _run_trm_outcome_collection() -> None:
    """Collect outcomes for all 11 powell_*_decisions tables."""
    from app.db.session import SessionLocal

    logger.info("Starting scheduled TRM outcome collection")

    db = SessionLocal()
    try:
        from app.services.powell.outcome_collector import OutcomeCollectorService

        service = OutcomeCollectorService(db)
        stats = service.collect_trm_outcomes()
        logger.info(
            f"TRM outcome collection completed: "
            f"{stats['succeeded']} computed, {stats['failed']} failed "
            f"out of {stats['processed']} processed"
        )
    except Exception as e:
        logger.error(f"TRM outcome collection job failed: {e}")
    finally:
        db.close()


def _run_skill_outcome_collection() -> None:
    """Collect outcomes for Claude Skills decisions in decision_embeddings.

    Uses the sync KB session since decision_embeddings lives in the KB database
    (which may be on a separate host, e.g. the Acer worker node).
    """
    from app.db.kb_session import get_sync_kb_session

    logger.info("Starting scheduled skill outcome collection")

    try:
        with get_sync_kb_session() as db:
            from app.services.powell.outcome_collector import OutcomeCollectorService

            service = OutcomeCollectorService(db)
            stats = service.collect_skill_outcomes()
            logger.info(
                f"Skill outcome collection completed: "
                f"{stats['succeeded']} computed, {stats['failed']} failed "
                f"out of {stats['processed']} processed"
            )
    except Exception as e:
        logger.error(f"Skill outcome collection job failed: {e}")


def _run_cdt_calibration() -> None:
    """Incrementally calibrate CDT wrappers from newly collected outcomes.

    Runs per-tenant to ensure calibration data isolation — each tenant's
    decisions only calibrate that tenant's CDT wrappers.
    """
    from app.db.session import SessionLocal

    logger.info("Starting scheduled CDT calibration (per-tenant)")

    db = SessionLocal()
    try:
        from app.services.powell.cdt_calibration_service import CDTCalibrationService
        from app.models.tenant import Tenant

        tenants = db.query(Tenant.id).all()
        total_added_all = 0

        for (tenant_id,) in tenants:
            try:
                service = CDTCalibrationService(db, tenant_id=tenant_id)
                stats = service.calibrate_incremental()
                total_added = sum(s.get("added", 0) for s in stats.values())
                calibrated = sum(
                    1 for s in stats.values() if s.get("is_calibrated", False)
                )
                total_added_all += total_added
                if total_added > 0:
                    logger.info(
                        f"CDT calibration tenant {tenant_id}: {total_added} new pairs, "
                        f"{calibrated}/11 agents calibrated"
                    )
            except Exception as e:
                logger.warning(f"CDT calibration failed for tenant {tenant_id}: {e}")

        logger.info(
            f"CDT calibration complete: {total_added_all} total new pairs "
            f"across {len(tenants)} tenants"
        )
    except Exception as e:
        logger.error(f"CDT calibration job failed: {e}")
    finally:
        db.close()


def _run_gnn_orchestration() -> None:
    """Run full GNN inference → directive broadcast cycle."""
    import asyncio
    from app.db.session import SessionLocal

    logger.info("Starting scheduled GNN orchestration cycle")

    db = SessionLocal()
    try:
        from app.services.powell.gnn_orchestration_service import GNNOrchestrationService

        orchestrator = GNNOrchestrationService(db, config_id=1)
        result = asyncio.get_event_loop().run_until_complete(
            orchestrator.run_full_cycle()
        )
        logger.info(
            f"GNN orchestration completed: "
            f"{result.get('directives_generated', 0)} directives generated, "
            f"{result.get('broadcast_success', 0)} broadcast, "
            f"cycle took {result.get('cycle_duration_ms', 0)}ms"
        )
        if result.get("errors"):
            logger.warning(f"GNN orchestration errors: {result['errors']}")
    except Exception as e:
        logger.error(f"GNN orchestration job failed: {e}")
    finally:
        db.close()


def _run_causal_matching() -> None:
    """Find propensity-score matched pairs for Tier 2 override effectiveness."""
    from app.db.session import SessionLocal

    logger.info("Starting scheduled causal matching")

    db = SessionLocal()
    try:
        from app.services.causal_matching_service import CausalMatchingService

        service = CausalMatchingService(db)
        stats = service.run_matching(lookback_days=30)
        logger.info(
            f"Causal matching completed: {stats['matched']} matched, "
            f"{stats['skipped']} skipped, {stats['failed']} failed"
        )
    except Exception as e:
        logger.error(f"Causal matching job failed: {e}")
    finally:
        db.close()


def _run_escalation_arbiter() -> None:
    """Evaluate all sites for persistent TRM drift → vertical escalation.

    Kahneman System 1/2: Detects when TRMs (System 1) consistently fail,
    triggering tGNN/GraphSAGE (System 2) replanning.
    Boyd OODA: Inner loop anomaly triggers outer loop iteration.
    """
    from app.db.session import SessionLocal

    logger.info("Starting scheduled Escalation Arbiter evaluation")

    db = SessionLocal()
    try:
        from app.services.powell.escalation_arbiter import EscalationArbiter
        from app.models.tenant import Tenant

        # Run for each active tenant
        tenants = db.query(Tenant.id).filter(Tenant.status == "active").all()
        total_escalations = 0

        for (tenant_id,) in tenants:
            try:
                arbiter = EscalationArbiter(db=db, tenant_id=tenant_id)
                verdicts = arbiter.evaluate_all_sites()
                total_escalations += len(verdicts)

                for v in verdicts:
                    logger.info(
                        "Escalation [tenant=%d]: %s → %s (%s)",
                        tenant_id, v.affected_sites, v.level, v.recommended_action,
                    )
            except Exception as e:
                logger.warning(
                    "Escalation Arbiter failed for tenant %d: %s", tenant_id, e
                )

        logger.info(
            "Escalation Arbiter completed: %d escalation(s) across %d tenants",
            total_escalations, len(tenants),
        )
    except Exception as e:
        logger.error(f"Escalation Arbiter job failed: {e}")
    finally:
        db.close()


def _run_cdc_retraining() -> None:
    """Evaluate retraining need for all sites with recent CDC triggers."""
    from app.db.session import SessionLocal

    logger.info("Starting scheduled CDC retraining evaluation")

    db = SessionLocal()
    try:
        from app.models.powell_decision import CDCTriggerLog, SiteAgentCheckpoint
        from app.services.powell.cdc_retraining_service import CDCRetrainingService
        from datetime import timedelta

        now = datetime.utcnow()

        # Find distinct site_keys that have had CDC triggers in the last 24h
        recent_sites = (
            db.query(CDCTriggerLog.site_key)
            .filter(
                CDCTriggerLog.triggered == True,
                CDCTriggerLog.timestamp > now - timedelta(hours=24),
            )
            .distinct()
            .all()
        )

        site_keys = [row[0] for row in recent_sites if row[0]]

        if not site_keys:
            logger.info("No sites with recent CDC triggers, skipping retraining")
            return

        trained = 0
        skipped = 0
        failed = 0

        for site_key in site_keys:
            try:
                svc = CDCRetrainingService(db=db, site_key=site_key, tenant_id=0)
                if svc.evaluate_retraining_need():
                    result = svc.execute_retraining()
                    if result and result.final_loss < float("inf"):
                        trained += 1
                        logger.info(
                            f"Retrained {site_key}: loss={result.final_loss:.4f}"
                        )
                    else:
                        skipped += 1
                else:
                    skipped += 1
            except Exception as e:
                failed += 1
                logger.warning(f"Retraining failed for {site_key}: {e}")

        logger.info(
            f"CDC retraining evaluation: {trained} trained, "
            f"{skipped} skipped, {failed} failed "
            f"across {len(site_keys)} sites"
        )
    except Exception as e:
        logger.error(f"CDC retraining job failed: {e}")
    finally:
        db.close()


def _run_cfa_optimization() -> None:
    """
    Weekly CFA policy optimization using Differential Evolution.

    For each active SupplyChainConfig, runs PolicyOptimizer.optimize()
    on inventory policies and persists optimal θ to PowellPolicyParameters.

    Implements Lokad's principle: control variables (policy parameters)
    should be periodically re-optimized against the latest demand/lead-time
    distributions rather than set once and forgotten.
    """
    from app.db.session import SessionLocal

    logger.info("Starting weekly CFA policy optimization")

    db = SessionLocal()
    try:
        from app.models.supply_chain_config import SupplyChainConfig
        from app.services.powell.policy_optimizer import (
            InventoryPolicyOptimizer,
            PolicyParameter,
        )
        from app.models.powell import PowellPolicyParameters

        configs = (
            db.query(SupplyChainConfig)
            .filter(SupplyChainConfig.is_active == True)
            .all()
        )

        if not configs:
            logger.info("CFA optimization: no active configs found, skipping")
            return

        total_optimized = 0
        total_failed = 0

        for config in configs:
            try:
                optimizer = InventoryPolicyOptimizer(
                    db=db,
                    config_id=config.id,
                    tenant_id=config.tenant_id,
                )
                result = optimizer.optimize(method="differential_evolution")

                if result and result.converged:
                    # Persist optimal parameters
                    for param_name, param_value in result.optimal_params.items():
                        existing = (
                            db.query(PowellPolicyParameters)
                            .filter(
                                PowellPolicyParameters.config_id == config.id,
                                PowellPolicyParameters.parameter_name == param_name,
                            )
                            .first()
                        )
                        if existing:
                            existing.parameter_value = param_value
                            existing.updated_at = datetime.utcnow()
                        else:
                            db.add(PowellPolicyParameters(
                                config_id=config.id,
                                tenant_id=config.tenant_id,
                                parameter_name=param_name,
                                parameter_value=param_value,
                                optimization_method="differential_evolution",
                            ))

                    db.commit()
                    total_optimized += 1
                    logger.info(
                        f"CFA optimization completed for config {config.id} "
                        f"('{config.name}'): cost={result.optimal_cost:.2f}, "
                        f"iterations={result.iterations}"
                    )
                else:
                    total_failed += 1
                    logger.warning(
                        f"CFA optimization did not converge for config {config.id}"
                    )
            except Exception as e:
                total_failed += 1
                logger.warning(
                    f"CFA optimization failed for config {config.id}: {e}"
                )

        logger.info(
            f"CFA policy optimization complete: {total_optimized} optimized, "
            f"{total_failed} failed across {len(configs)} configs"
        )
    except Exception as e:
        logger.error(f"CFA policy optimization job failed: {e}")
    finally:
        db.close()


def _run_data_drift_scan() -> None:
    """
    Weekly data drift scan across all active supply chain configs.

    Detects long-horizon distributional shifts in:
      - demand (input drift): ordered_quantity distribution
      - forecast_error (model drift): (forecast - actual) residuals
      - calibration (interval drift): prediction interval widths (p90 - p10)

    Windows: 28d (4w canary), 56d (8w alarm), 84d (12w trend)
    HIGH/CRITICAL results escalate to PowellEscalationLog (level="strategic")

    This is intentionally separate from CDC (which fires hourly on metric threshold
    breaches). DataDriftMonitor fires proactively when distributions are SHIFTING
    over weeks, before model performance degrades enough to trigger CDC.
    """
    from app.db.session import SessionLocal
    from datetime import date

    logger.info("Starting weekly DataDrift distributional shift scan")

    db = SessionLocal()
    try:
        from app.models.supply_chain_config import SupplyChainConfig
        from app.services.powell.data_drift_monitor import DataDriftMonitor

        monitor = DataDriftMonitor(db=db)

        # Scan all active configs
        configs = db.query(SupplyChainConfig).all()
        if not configs:
            logger.info("DataDrift: No SC configs found, skipping scan")
            return

        total_records = 0
        total_drift = 0
        total_alerts = 0
        total_escalations = 0
        failed_configs = 0

        for config in configs:
            try:
                results = monitor.scan_config(config.id)
                n_drift = sum(1 for r in results if r.drift_detected)
                n_escalated = sum(
                    1 for r in results
                    if r.drift_severity in ("high", "critical")
                )
                total_records += len(results)
                total_drift += n_drift
                total_escalations += n_escalated

                if n_drift > 0:
                    total_alerts += 1

                if n_escalated > 0:
                    logger.warning(
                        f"DataDrift: Config {config.id} ('{config.name}') — "
                        f"{n_escalated} HIGH/CRITICAL drift records → escalated to S&OP"
                    )
            except Exception as exc:
                failed_configs += 1
                logger.warning(
                    f"DataDrift: Scan failed for config {config.id}: {exc}",
                    exc_info=True,
                )

        logger.info(
            f"DataDrift weekly scan complete — "
            f"{len(configs)} configs, {total_records} records written, "
            f"{total_drift} drift detected, {total_alerts} alerts raised, "
            f"{total_escalations} escalated, {failed_configs} configs failed"
        )
    except Exception as e:
        logger.error(f"DataDrift weekly scan job failed: {e}", exc_info=True)
    finally:
        db.close()


def _run_site_tgnn_inference() -> None:
    """Hourly Site tGNN (Layer 1.5) inference for all sites.

    Runs per-site inference to modulate UrgencyVector before the next
    decision cycle. No-op if no trained models exist.
    """
    logger.info("Starting scheduled Site tGNN inference (Layer 1.5)")
    # Site tGNN inference is triggered inline within SiteAgent.execute_decision_cycle().
    # This scheduled job serves as a checkpoint log entry
    # and could trigger standalone inference for sites not actively in a decision cycle.
    logger.info("Site tGNN inference check complete (inline execution via SiteAgent)")


def _run_site_tgnn_training_check() -> None:
    """Every 12h: Check if sufficient data exists to train/retrain Site tGNN.

    Checks MultiHeadTrace data volume per site. If >= 200 traces and either
    no model exists or last training was > 24h ago, triggers Phase 1 BC training.
    """
    from app.db.session import sync_session_factory

    logger.info("Starting Site tGNN training check")
    try:
        db = sync_session_factory()
        # For now, log the check — actual training integration requires
        # trace data accumulation from CoordinatedSimRunner
        logger.info("Site tGNN training check complete (no sites requiring training)")
    except Exception as e:
        logger.error(f"Site tGNN training check failed: {e}", exc_info=True)
    finally:
        try:
            db.close()
        except Exception:
            pass


def _run_risk_condition_monitor() -> None:
    """Re-evaluate INFORMED risk alerts and auto-resolve (ACTIONED) if condition cleared."""
    from app.db.session import SessionLocal
    import asyncio

    logger.info("Starting risk condition monitor")

    db = SessionLocal()
    try:
        from app.services.risk_detection_service import RiskDetectionService

        service = RiskDetectionService(db)

        # Run the async method in a sync context
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(service.resolve_informed_alerts())
        finally:
            loop.close()

        logger.info(
            "Risk condition monitor completed: %d auto-resolved, %d still informed",
            result["resolved"], result["still_informed"],
        )
    except Exception as e:
        logger.error(f"Risk condition monitor failed: {e}", exc_info=True)
    finally:
        try:
            db.close()
        except Exception:
            pass


def _run_external_signal_refresh() -> None:
    """Daily refresh of external market intelligence from all configured sources.

    Iterates over all tenants with active ExternalSignalSource records,
    fetches new signals from public APIs (FRED, Open-Meteo, EIA, GDELT, etc.),
    and persists them for RAG injection into Azirella chat context.
    """
    import asyncio
    from app.db.session import async_session_factory

    logger.info("Starting external signal daily refresh")

    async def _refresh():
        async with async_session_factory() as db:
            try:
                from app.services.external_signal_service import refresh_all_tenants
                stats = await refresh_all_tenants(db)
                logger.info(
                    "External signal refresh completed: "
                    f"{stats['tenants_processed']} tenants, "
                    f"{stats['signals_collected']} signals, "
                    f"{stats['errors']} errors"
                )
            except Exception as e:
                logger.error(f"External signal refresh failed: {e}", exc_info=True)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_refresh())
    finally:
        loop.close()


def _run_experiential_knowledge_detection() -> None:
    """Daily override pattern detection and lifecycle management.

    Detects recurring override patterns (≥3 occurrences), creates CANDIDATE
    knowledge entities, and runs stagnation/contradiction checks.
    Based on Alicke's 'The Planner Was the System'.
    """
    from app.db.session import sync_session_factory

    logger.info("Starting experiential knowledge pattern detection")

    db = sync_session_factory()
    try:
        from app.services.experiential_knowledge_service import ExperientialKnowledgeService
        from app.models.tenant import Tenant
        from sqlalchemy import select

        # Iterate over all tenants
        tenants = db.execute(select(Tenant)).scalars().all()
        total_created = 0
        total_stale = 0

        for tenant in tenants:
            try:
                svc = ExperientialKnowledgeService(
                    db=db, tenant_id=tenant.id
                )
                detection = svc.detect_patterns(lookback_days=90)
                lifecycle = svc.check_lifecycle()
                total_created += detection.get("created", 0)
                total_stale += lifecycle.get("stale", 0)
            except Exception as e:
                logger.warning(
                    "EK detection failed for tenant %d: %s", tenant.id, e
                )
                continue

        logger.info(
            "EK detection completed: %d new candidates, %d stale across %d tenants",
            total_created, total_stale, len(tenants),
        )
    except Exception as e:
        logger.error("EK detection job failed: %s", e, exc_info=True)
    finally:
        db.close()


def _run_demo_date_shift() -> None:
    """Shift demo data dates forward — Food Dist demo ONLY.

    The Food Dist demo (US Foods Distributor) is the single tenant whose data
    is intentionally rolled forward nightly to keep "today" aligned with real
    calendar time. It uses synthetically generated history, so rolling is safe
    and keeps the demo feeling current.

    All other demo tenants are EXCLUDED:
    - SAP Demo (frozen at 2025-11-20) — CAL FAA reference date, must not drift
    - D365/Odoo/Infor/B1 demos — anchored to their respective extraction
      reference dates; shifting corrupts ERP transactional coherence
    - Beer Scenario / Complex SC — learning tenants use simulation time, not
      calendar time
    - Any future production customer tenant — real data, never shifted

    See docs/internal/VIRTUAL_CLOCK_ARCHITECTURE.md
    """
    from app.db.session import sync_session_factory

    logger.info("Starting scheduled demo date shift (Food Dist only)")

    db = sync_session_factory()
    try:
        from app.models.tenant import Tenant
        from app.services.demo_date_shift_service import DemoDateShiftService

        service = DemoDateShiftService(db)
        tracked = service.get_all_tracked_configs()

        if not tracked:
            logger.info("No demo date shift entries found — nothing to shift")
            return

        # Allowlist: Food Dist demo is the only tenant eligible for date shifting.
        # Identify by slug (stable) with a fallback to name match.
        food_dist = (
            db.query(Tenant)
            .filter(Tenant.slug == "food-dist")
            .first()
        )
        if food_dist is None:
            # Fallback: match by name (handles older installs / slug variants)
            food_dist = (
                db.query(Tenant)
                .filter(Tenant.name == "US Foods Distributor")
                .first()
            )

        if food_dist is None:
            logger.info(
                "Demo date shift: Food Dist tenant not found — nothing to shift"
            )
            return

        # Safety: never shift a frozen tenant, even if the allowlist matched.
        if getattr(food_dist, "time_mode", "live") == "frozen":
            logger.warning(
                "Demo date shift: Food Dist tenant %d is marked frozen "
                "(virtual_today=%s) — refusing to shift",
                food_dist.id, getattr(food_dist, "virtual_today", None),
            )
            return

        eligible_tenant_ids = {food_dist.id}
        logger.info(
            "Demo date shift: eligible tenant is %d (%s)",
            food_dist.id, food_dist.name,
        )

        total_shifted = 0
        total_rows = 0

        for entry in tracked:
            if entry["tenant_id"] not in eligible_tenant_ids:
                logger.debug(
                    "Demo date shift: skipping tenant %s (not Food Dist)",
                    entry["tenant_id"],
                )
                continue
            logger.info(
                "Demo date shift: processing Food Dist tenant %d config %d",
                entry["tenant_id"], entry["config_id"],
            )
            try:
                result = service.shift_demo_dates(
                    tenant_id=entry["tenant_id"],
                    config_id=entry["config_id"],
                )
                if result.get("shifted"):
                    total_shifted += 1
                    total_rows += result.get("rows_affected", 0)
                    logger.info(
                        "Demo date shift: tenant=%d config=%d days=%d rows=%d",
                        entry["tenant_id"], entry["config_id"],
                        result.get("days", 0), result.get("rows_affected", 0),
                    )
            except Exception as e:
                logger.warning(
                    "Demo date shift failed for tenant=%d config=%d: %s",
                    entry["tenant_id"], entry["config_id"], e,
                )
                continue

        logger.info(
            "Demo date shift completed: %d configs shifted, %d total rows across %d tracked configs",
            total_shifted, total_rows, len(tracked),
        )
    except Exception as e:
        logger.error("Demo date shift job failed: %s", e, exc_info=True)
    finally:
        db.close()


def _run_conformal_autocalibration() -> None:
    """Refresh conformal predictors from outcome-measured agent_decisions.

    Iterates every (tenant, config) pair with calibratable TRM decisions
    in the trailing 30-day window and updates conformal.active_predictors
    / calibration_snapshots / observation_log.
    """
    from app.db.session import sync_session_factory

    logger.info("Starting scheduled conformal auto-calibration")

    db = sync_session_factory()
    try:
        from app.services.powell.conformal_autocalibrate_service import (
            ConformalAutoCalibrateService,
        )

        service = ConformalAutoCalibrateService(db)
        summaries = service.calibrate_all_tenants()
        total_vars = sum(len(s.get("variables", [])) for s in summaries)
        total_calibrated = sum(
            1 for s in summaries for v in s.get("variables", []) if v.get("calibrated")
        )
        logger.info(
            "Conformal auto-calibration completed: %d (tenant,config) pairs, "
            "%d variables evaluated, %d calibrated",
            len(summaries), total_vars, total_calibrated,
        )
    except Exception as e:
        logger.error("Conformal auto-calibration job failed: %s", e, exc_info=True)
    finally:
        db.close()


def _run_terminal_coordinator() -> None:
    """Walk every (tenant, config) pair with at least one terminal-style
    Site and run one TerminalCoordinatorService cycle per pair.

    The pair list is derived from policy_parameters rows (every tenant
    with an active policy gets walked; tenants without policy are
    skipped — matches the no-fallbacks invariant).
    """
    from app.db.session import sync_session_factory
    from sqlalchemy import select

    logger.info("Starting scheduled L2 terminal coordinator cycle")

    db = sync_session_factory()
    total_hubs = 0
    total_overrides = 0
    pairs = 0
    try:
        from app.models.policy_parameters import PolicyParameters
        from app.services.powell.terminal_coordinator_service import (
            TerminalCoordinatorService,
        )

        # Pull every (tenant, config) pair with an active policy.
        # config_id IS NULL means tenant-wide; we still need a concrete
        # config_id to scope the Site lookup, so we expand each
        # tenant-wide policy across the tenant's active configs.
        active_pairs = db.execute(
            select(
                PolicyParameters.tenant_id,
                PolicyParameters.config_id,
            ).where(PolicyParameters.effective_to.is_(None))
        ).all()
        # Drop tenant-wide rows with no config — they need real configs
        # to do anything. The per-config row will be picked up
        # separately when one exists.
        for tenant_id, config_id in active_pairs:
            if config_id is None:
                # Find tenant configs to fan out to
                from app.models.supply_chain_config import SupplyChainConfig
                cfgs = db.execute(
                    select(SupplyChainConfig.id).where(
                        SupplyChainConfig.tenant_id == tenant_id,
                        SupplyChainConfig.is_active.is_(True),
                    )
                ).scalars().all()
                cfg_ids = [int(c) for c in cfgs]
            else:
                cfg_ids = [int(config_id)]

            for cfg_id in cfg_ids:
                pairs += 1
                svc = TerminalCoordinatorService(
                    db, tenant_id=int(tenant_id), config_id=cfg_id
                )
                summaries = svc.run_cycle()
                total_hubs += len(summaries)
                total_overrides += sum(
                    len(s.get("overrides_emitted", [])) for s in summaries
                )
                # AD-11 closed-loop: write CarrierCapacityState snapshots
                # for the trailing hour. Idempotent on
                # (tenant, carrier, observed_at).
                svc.record_carrier_capacity_snapshots(period_hours=1)
                # L2 Phase-2: write hub_hour_snapshot rows
                # (GATv2-ready training substrate). Idempotent on
                # (tenant, config, hub, observed_at).
                try:
                    from app.services.powell.hub_hour_snapshot_service import (
                        HubHourSnapshotService,
                    )
                    snap_svc = HubHourSnapshotService(
                        db, tenant_id=int(tenant_id), config_id=cfg_id,
                        source="live",
                    )
                    snap_results = snap_svc.snapshot_all_hubs()
                    new_snapshots = sum(
                        1 for r in snap_results if r.get("wrote_new")
                    )
                    if new_snapshots:
                        logger.info(
                            "HubHourSnapshot: wrote %d new snapshots "
                            "(tenant=%s config=%s)",
                            new_snapshots, tenant_id, cfg_id,
                        )
                except Exception as snap_err:
                    logger.warning(
                        "HubHourSnapshot extraction failed (non-fatal): %s",
                        snap_err,
                    )
                # Periodic cleanup of long-expired override rows
                svc.purge_expired(older_than_days=7)

        logger.info(
            "L2 terminal coordinator: %d (tenant,config) pairs walked, "
            "%d hubs evaluated, %d overrides emitted",
            pairs, total_hubs, total_overrides,
        )
    except Exception as e:
        logger.error("L2 terminal coordinator job failed: %s", e, exc_info=True)
    finally:
        db.close()


def _run_lane_performance_recording() -> None:
    """Walk (tenant, config) pairs and write LanePerformanceActuals
    rows for loads delivered in the last 24h.

    Idempotent on (tenant_id, correlation_id, observation_index) via
    the uq_lane_perf_correlation constraint — re-running within the
    24h window is safe (already-recorded loads are no-ops).
    """
    from app.db.session import sync_session_factory
    from sqlalchemy import select

    logger.info("Starting AD-11 lane-performance recording cycle")

    db = sync_session_factory()
    total_inserted = 0
    pairs = 0
    try:
        from app.models.policy_parameters import PolicyParameters
        from app.models.supply_chain_config import SupplyChainConfig
        from app.services.powell.shipment_tracking_trm import (
            ShipmentTrackingTRM,
        )

        active_pairs = db.execute(
            select(
                PolicyParameters.tenant_id,
                PolicyParameters.config_id,
            ).where(PolicyParameters.effective_to.is_(None))
        ).all()

        for tenant_id, config_id in active_pairs:
            if config_id is None:
                cfgs = db.execute(
                    select(SupplyChainConfig.id).where(
                        SupplyChainConfig.tenant_id == tenant_id,
                        SupplyChainConfig.is_active.is_(True),
                    )
                ).scalars().all()
                cfg_ids = [int(c) for c in cfgs]
            else:
                cfg_ids = [int(config_id)]

            for cfg_id in cfg_ids:
                pairs += 1
                trm = ShipmentTrackingTRM(
                    db, tenant_id=int(tenant_id), config_id=cfg_id
                )
                inserted = trm.record_lane_performance_for_recently_delivered(
                    lookback_hours=24,
                )
                total_inserted += inserted

        logger.info(
            "AD-11 lane-performance: %d (tenant,config) pairs walked, "
            "%d new rows recorded",
            pairs, total_inserted,
        )
    except Exception as e:
        logger.error(
            "AD-11 lane-performance recording job failed: %s",
            e, exc_info=True,
        )
    finally:
        db.close()
