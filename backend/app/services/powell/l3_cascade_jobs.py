"""§3.46 Phase 2 — APScheduler registration for the L3 transport cascade.

Wires :class:`L3CascadeRunner` (Phase 1) to a daily 5:00 UTC cron via
the existing ``SyncSchedulerService`` (which TMS already runs as a
``BackgroundScheduler`` singleton; see ``app/services/sync_scheduler_service.py``
and the prior callers in ``tms_extraction_jobs.py`` / ``conformal_orchestrator.py``).

What runs at 5:00 UTC every day:

1. Open a fresh sync DB session (the scheduler runs in a background
   thread; we don't reuse FastAPI request sessions).
2. Query every PRODUCTION-mode tenant with ``status='ACTIVE'``.
3. For each tenant: find its active production-baseline
   ``SupplyChainConfig``, run :class:`L3CascadeRunner` for
   ``period_start = today UTC, period_days = 7``.
4. Per-tenant try/except: one tenant's cascade failure is logged and
   the loop continues. The Phase 1 runner already returns a
   :class:`CascadeRunResult` with status="FAILED" for stage exceptions
   (it doesn't raise) — this layer only needs to handle infrastructure
   exceptions (DB connectivity, missing config, etc.).

**Why 5:00 UTC and not the 5:15 / 5:30 split documented in CLAUDE.md.**
The Phase 1 runner produces both ``unconstrained_reference`` and
``constrained_live`` in one cascade_run with per-stage commits, so the
"5am MPS refresh" and "5:30am constrained solve" become a single
atomic two-stage event at 5:00. The 30-minute gap in CLAUDE.md was a
budget for MPS to finish before the LP runs; per-stage commits make
that gap implicit (Stage 1 commits before Stage 2 starts).

**Idempotency.** The Phase 1 runner skips when a prior L3 plan exists
for the period. So a misfire that re-fires the cron 10 minutes late
is a safe no-op, not a duplicate plan. Set ``misfire_grace_time=3600``
(1 hour) to give the cron room to recover from short host outages.

**`period_start` policy.** Today (UTC). This refreshes a rolling
7-day plan daily — what CLAUDE.md calls "Plan of Record refresh from
conformal P50." Tenants on weekly cadence (rare for transport) should
override at the per-tenant config level once §3.46 Phase 4 ships the
manual-trigger CLI.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from app.services.sync_scheduler_service import SyncSchedulerService


logger = logging.getLogger(__name__)


_L3_CASCADE_JOB_ID = "l3_transport_cascade_daily"
_L3_CASCADE_CRON_HOUR = 5
_L3_CASCADE_CRON_MINUTE = 0
_L3_CASCADE_PERIOD_DAYS = 7
_MISFIRE_GRACE_SECONDS = 3600  # 1 hour


def register_l3_cascade_jobs(
    scheduler_service: "SyncSchedulerService",
) -> None:
    """Register the L3 transport cascade daily cron job.

    Idempotent: ``replace_existing=True`` lets this be called on every
    app start without piling up duplicate triggers.
    """
    scheduler = scheduler_service._scheduler
    if scheduler is None:
        logger.warning(
            "Scheduler not available — L3 cascade job not registered",
        )
        return

    scheduler.add_job(
        func=_run_l3_cascade_for_all_tenants,
        trigger=CronTrigger(
            hour=_L3_CASCADE_CRON_HOUR,
            minute=_L3_CASCADE_CRON_MINUTE,
        ),
        id=_L3_CASCADE_JOB_ID,
        name=(
            f"L3 transport cascade (daily "
            f"{_L3_CASCADE_CRON_HOUR:02d}:{_L3_CASCADE_CRON_MINUTE:02d} UTC)"
        ),
        replace_existing=True,
        misfire_grace_time=_MISFIRE_GRACE_SECONDS,
    )
    logger.info(
        "Registered L3 transport cascade job (daily %02d:%02d UTC, id=%s)",
        _L3_CASCADE_CRON_HOUR, _L3_CASCADE_CRON_MINUTE,
        _L3_CASCADE_JOB_ID,
    )


# ---------------------------------------------------------------------------
# Job body
# ---------------------------------------------------------------------------


def _run_l3_cascade_for_all_tenants() -> None:
    """Run the L3 cascade for every active production tenant.

    Per-tenant failure is logged and skipped — one tenant's bad config
    must not stop the cron from servicing the rest.
    """
    from app.db.session import sync_session_factory
    from app.services.powell.l3_cascade_runner import L3CascadeRunner

    period_start = _today_utc()
    started_at = datetime.utcnow()
    logger.info(
        "L3 cascade — daily run starting (period_start=%s)", period_start,
    )

    n_ok = n_skipped = n_failed = 0
    with sync_session_factory() as discovery_db:
        tenant_configs = _discover_active_tenants(discovery_db)

    for tenant_id, config_id in tenant_configs:
        # Per-tenant DB session — failure on one doesn't poison the next.
        try:
            with sync_session_factory() as tenant_db:
                runner = L3CascadeRunner(tenant_db)
                result = runner.run(
                    tenant_id=tenant_id,
                    config_id=config_id,
                    period_start=period_start,
                    period_days=_L3_CASCADE_PERIOD_DAYS,
                    resolve_capacity_from_db=True,
                )
            if result.status == "OK":
                n_ok += 1
                logger.info(
                    "L3 cascade OK — tenant=%s cascade_run_id=%s "
                    "constrained_plan_id=%s",
                    tenant_id, result.cascade_run_id,
                    result.stages[-1].plan_id if result.stages else None,
                )
            elif result.status == "SKIPPED":
                n_skipped += 1
                logger.info(
                    "L3 cascade SKIPPED (idempotent) — tenant=%s period=%s",
                    tenant_id, period_start,
                )
            else:  # FAILED
                n_failed += 1
                failed_stage = next(
                    (s for s in result.stages if s.status == "FAILED"),
                    None,
                )
                logger.error(
                    "L3 cascade FAILED — tenant=%s cascade_run_id=%s "
                    "stage=%s error=%s",
                    tenant_id, result.cascade_run_id,
                    failed_stage.stage if failed_stage else "?",
                    failed_stage.error if failed_stage else "?",
                )
        except Exception:
            n_failed += 1
            logger.exception(
                "L3 cascade — infra failure for tenant=%s "
                "(continuing with next tenant)",
                tenant_id,
            )

    duration = (datetime.utcnow() - started_at).total_seconds()
    logger.info(
        "L3 cascade — daily run complete (period_start=%s "
        "tenants_ok=%d skipped=%d failed=%d duration_s=%.1f)",
        period_start, n_ok, n_skipped, n_failed, duration,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today_utc() -> date:
    """Today, UTC. Factored out so tests can monkeypatch."""
    return datetime.utcnow().date()


def _discover_active_tenants(db) -> list:
    """Return ``[(tenant_id, config_id)]`` for every PRODUCTION-mode
    tenant with ``status='ACTIVE'`` that has at least one active
    BASELINE supply-chain config.

    Tenants without a usable config are silently skipped (logged
    upstream when they hit the runner). LEARNING-mode tenants are
    excluded — they run the cascade on-demand for scenario testing,
    not on a daily cron.
    """
    from azirella_data_model.master.config import SupplyChainConfig
    from azirella_data_model.tenant import Tenant, TenantMode

    rows = (
        db.query(Tenant.id, SupplyChainConfig.id)
        .join(
            SupplyChainConfig,
            SupplyChainConfig.tenant_id == Tenant.id,
        )
        .filter(
            Tenant.status == "ACTIVE",
            Tenant.mode == TenantMode.PRODUCTION,
            SupplyChainConfig.is_active.is_(True),
            SupplyChainConfig.scenario_type == "BASELINE",
        )
        .all()
    )
    return [(tid, cid) for tid, cid in rows]


__all__ = [
    "register_l3_cascade_jobs",
]
