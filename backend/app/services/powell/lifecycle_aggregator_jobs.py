"""APScheduler registration for the §3.43 ProductLane aggregator.

Wires :class:`ProductLaneAggregator` to a daily 2:00 UTC cron — three
hours before the L3 cascade (5:00 UTC, see :mod:`l3_cascade_jobs`) so
that the lane-volume reactor reads a fresh-aggregated `ProductLane`
table when the L3 cascade fires.

What runs at 2:00 UTC every day:

  1. Discover active PRODUCTION + BASELINE-config tenants.
  2. For each tenant, run :class:`ProductLaneAggregator.aggregate_recent`
     for the last 4 weekly periods (default ``weeks_back=4``,
     ``period_days=7``).
  3. Per-tenant try/except: one tenant's failure is logged and the
     loop continues.

Why ``weeks_back=4``: shipment data is usually settled within 7 days
but late-arriving corrections (returns, billing adjustments) can
arrive over multiple weeks. Re-aggregating the last 4 weekly periods
catches those corrections; upsert semantics keep this idempotent
(re-running the same period does not create duplicate rows).

Same idempotency / misfire-grace pattern as :mod:`l3_cascade_jobs`.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from app.services.sync_scheduler_service import SyncSchedulerService


logger = logging.getLogger(__name__)


_LIFECYCLE_AGGREGATOR_JOB_ID = "lifecycle_product_lane_aggregator_daily"
_AGGREGATOR_CRON_HOUR = 2
_AGGREGATOR_CRON_MINUTE = 0
_AGGREGATOR_WEEKS_BACK = 4
_AGGREGATOR_PERIOD_DAYS = 7
_MISFIRE_GRACE_SECONDS = 3600  # 1 hour


def register_lifecycle_aggregator_jobs(
    scheduler_service: "SyncSchedulerService",
) -> None:
    """Register the ProductLane aggregator daily cron job.

    Idempotent: ``replace_existing=True`` lets this be called on every
    app start without piling up duplicate triggers.
    """
    scheduler = scheduler_service._scheduler
    if scheduler is None:
        logger.warning(
            "Scheduler not available — lifecycle aggregator job not registered",
        )
        return

    scheduler.add_job(
        func=_run_aggregator_for_all_tenants,
        trigger=CronTrigger(
            hour=_AGGREGATOR_CRON_HOUR,
            minute=_AGGREGATOR_CRON_MINUTE,
        ),
        id=_LIFECYCLE_AGGREGATOR_JOB_ID,
        name=(
            f"ProductLane aggregator (daily "
            f"{_AGGREGATOR_CRON_HOUR:02d}:{_AGGREGATOR_CRON_MINUTE:02d} UTC)"
        ),
        replace_existing=True,
        misfire_grace_time=_MISFIRE_GRACE_SECONDS,
    )
    logger.info(
        "Registered lifecycle aggregator job (daily %02d:%02d UTC, id=%s, "
        "weeks_back=%d)",
        _AGGREGATOR_CRON_HOUR, _AGGREGATOR_CRON_MINUTE,
        _LIFECYCLE_AGGREGATOR_JOB_ID, _AGGREGATOR_WEEKS_BACK,
    )


# ---------------------------------------------------------------------------
# Job body
# ---------------------------------------------------------------------------


def _run_aggregator_for_all_tenants() -> None:
    """Run the ProductLane aggregator for every active production tenant.

    Per-tenant failure is logged and skipped — one tenant's bad config
    must not stop the cron from servicing the rest.
    """
    from app.db.session import sync_session_factory
    from app.services.powell.product_lane_aggregator import (
        ProductLaneAggregator,
    )

    started_at = datetime.utcnow()
    logger.info(
        "ProductLane aggregator — daily run starting (weeks_back=%d)",
        _AGGREGATOR_WEEKS_BACK,
    )

    aggregator = ProductLaneAggregator(period_days=_AGGREGATOR_PERIOD_DAYS)

    n_ok = n_failed = 0
    total_rows_written = 0
    total_volume = 0.0
    with sync_session_factory() as discovery_db:
        tenant_configs = _discover_active_tenants(discovery_db)

    for tenant_id, config_id in tenant_configs:
        try:
            with sync_session_factory() as tenant_db:
                results = aggregator.aggregate_recent(
                    tenant_db,
                    tenant_id=tenant_id,
                    config_id=config_id,
                    weeks_back=_AGGREGATOR_WEEKS_BACK,
                )
                tenant_db.commit()
            tenant_rows = sum(r.rows_written for r in results)
            tenant_volume = sum(r.total_volume for r in results)
            total_rows_written += tenant_rows
            total_volume += tenant_volume
            n_ok += 1
            logger.info(
                "ProductLane aggregator OK — tenant=%s rows_written=%d "
                "lanes_affected=%d total_volume=%.0f periods_processed=%d",
                tenant_id, tenant_rows,
                sum(r.lanes_affected for r in results),
                tenant_volume, len(results),
            )
        except Exception:
            n_failed += 1
            logger.exception(
                "ProductLane aggregator — failure for tenant=%s "
                "(continuing with next tenant)",
                tenant_id,
            )

    duration = (datetime.utcnow() - started_at).total_seconds()
    logger.info(
        "ProductLane aggregator — daily run complete "
        "(tenants_ok=%d failed=%d total_rows=%d total_volume=%.0f "
        "duration_s=%.1f)",
        n_ok, n_failed, total_rows_written, total_volume, duration,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _discover_active_tenants(db) -> list:
    """Return ``[(tenant_id, config_id)]`` for every active PRODUCTION
    tenant with at least one active BASELINE supply-chain config.

    Identical to :mod:`l3_cascade_jobs._discover_active_tenants` —
    duplicated rather than imported because the cron jobs are
    independent registration units; if l3_cascade_jobs ever moves or
    its discovery logic drifts, this aggregator should not silently
    follow.
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
    "register_lifecycle_aggregator_jobs",
]
