"""L3 transport cascade runner — §3.46 Phase 1.

Orchestrates the L3 transport-side cascade for one (tenant, period):

    LaneVolumePlan (already published — upstream)
       ↓ MovementPlannerService.plan_movement
    TransportationPlan(unconstrained_reference)
       ↓ IntegratedBalancerService.balance_plan
    TransportationPlan(constrained_live)

This service ships the orchestration *primitive* — once it exists,
anyone (a cron job, a CLI, an API endpoint, a test) can invoke the
full cascade. APScheduler registration is §3.46 Phase 2.

**Per-stage transactions.** Each stage commits its writes before the
next runs, so a stage-2 exception leaves stage-1's
``unconstrained_reference`` plan intact. The Movement plan is
recoverable evidence even when the Balancer fails — operators can
inspect what the heuristic produced, fix the capacity / commitment
data, and re-run the Balancer alone.

**Shared cascade_run_id.** Both writes are tagged with the same
``cascade_run_id`` (shape: ``l3_<tenant>_<utc-iso>_<uuid8>``) so
consumers can correlate the unconstrained → constrained pair.
``TransportationPlan.cascade_run_id`` is a free-text column today;
§3.46 Phase 3 promotes it to an FK on a Core ``CascadeRun``
substrate.

**Idempotency.** ``run()`` checks for an existing
``TransportationPlan`` with ``plan_start_date == period_start`` AND
``cascade_run_id LIKE 'l3_%'`` for the tenant; skips with
``status="SKIPPED"`` unless ``force=True``. This makes the cron in
§3.46 Phase 2 safe to fire-and-forget — re-running on the same period
is a no-op.

**Plane-module placement.** TMS-side decision orchestration. Per the
plane-module invariant (CLAUDE.md): the cascade-runner pattern is
plane-specific (TMS and SCP have different cascades — different
upstream forecasts, different downstream gates); the *substrate* that
lets cascades correlate (a Core ``CascadeRun`` table) is Phase 3
follow-on.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from azirella_data_model.transport_plan import (
    DEFAULT_PLAN_VERSION,
    TransportationPlan,
)

from app.services.powell.integrated_balancer_service import (
    IntegratedBalancerService,
)
from app.services.powell.movement_planner_service import (
    MovementPlannerService,
)


logger = logging.getLogger(__name__)


_RUN_ID_PREFIX = "l3"
"""Prefix on every ``cascade_run_id`` produced by this runner. Lets
consumers filter ``cascade_run_id LIKE 'l3_%'`` to find L3-cascade
plans specifically, separate from any other cascade that might tag
plans with a different prefix (e.g., a future ``s_`` for the S&OP
weekly cascade)."""


@dataclass(frozen=True)
class StageResult:
    """One stage's outcome."""

    stage: str
    """``"movement"`` / ``"balancer"``."""

    status: str
    """``"OK"`` / ``"FAILED"``."""

    plan_id: Optional[int] = None
    """Plan id produced by this stage. ``None`` on FAILED."""

    error: Optional[str] = None
    """Exception summary string when ``status == "FAILED"``."""

    summary: dict = field(default_factory=dict)
    """Stage-specific facts lifted from the underlying service result
    (items_written, items_with_carrier, items_escalated,
    optimization_method, …) for downstream observability without
    forcing consumers to re-query the DB."""


@dataclass(frozen=True)
class CascadeRunResult:
    """Per-call summary."""

    cascade_run_id: str
    tenant_id: int
    config_id: int
    period_start: date

    status: str
    """``"OK"`` (all stages succeeded) / ``"FAILED"`` (one stage
    failed) / ``"SKIPPED"`` (idempotency check matched an existing
    L3 plan for the period)."""

    stages: List[StageResult] = field(default_factory=list)
    """Ordered ``StageResult`` list, one per stage that actually ran.
    Empty when ``status == "SKIPPED"``."""

    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class L3CascadeRunner:
    """Orchestrate the L3 transport cascade for one (tenant, period).

    Usage::

        runner = L3CascadeRunner(db)
        result = runner.run(
            tenant_id=42, config_id=1,
            period_start=date(2026, 5, 4), period_days=7,
        )
        if result.status == "OK":
            constrained_plan_id = result.stages[-1].plan_id
            ...

    The runner manages its own transaction boundaries (per-stage
    commit on success; rollback on per-stage failure). Caller does
    NOT need to commit.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def run(
        self,
        *,
        tenant_id: int,
        config_id: int,
        period_start: date,
        period_days: int = 7,
        scenario_id: Optional[int] = None,
        forecast_plan_version: str = DEFAULT_PLAN_VERSION,
        force: bool = False,
        resolve_capacity_from_db: bool = True,
    ) -> CascadeRunResult:
        """Run the cascade end-to-end for one (tenant, config, period).

        Returns a :class:`CascadeRunResult` documenting per-stage
        status. Always returns — does not raise on stage failure;
        callers inspect ``result.status`` and per-stage errors. (The
        scheduler in Phase 2 relies on this — one tenant's cascade
        failure must not kill the cron job for the rest of the
        tenants.)

        - ``force=True`` bypasses the idempotency skip (re-runs even
          when an L3 plan already exists for the period). Used by the
          manual-trigger CLI in Phase 4 for replan-after-data-fix.
        - ``resolve_capacity_from_db=True`` (default) makes the
          Balancer query ``CarrierCapacityCommitment`` rows (§3.42).
          Set False to keep the Phase 1 clone-only behaviour for
          tenants without seeded commitments.
        """
        cascade_run_id = self._new_run_id(tenant_id)
        started_at = datetime.utcnow()

        # Idempotency check.
        if not force:
            existing = self._existing_l3_plan(tenant_id, period_start)
            if existing is not None:
                logger.info(
                    "L3 cascade skipped (idempotent) — existing plan id=%s "
                    "cascade_run_id=%s for tenant=%s period=%s",
                    existing.id, existing.cascade_run_id,
                    tenant_id, period_start,
                )
                return CascadeRunResult(
                    cascade_run_id=cascade_run_id,
                    tenant_id=tenant_id, config_id=config_id,
                    period_start=period_start,
                    status="SKIPPED",
                    stages=[],
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                )

        stages: List[StageResult] = []

        # Stage 1 — Movement Planner (unconstrained_reference).
        movement = self._run_movement(
            tenant_id=tenant_id, config_id=config_id,
            period_start=period_start, period_days=period_days,
            scenario_id=scenario_id,
            forecast_plan_version=forecast_plan_version,
            cascade_run_id=cascade_run_id,
        )
        stages.append(movement)
        if movement.status != "OK" or movement.plan_id is None:
            return CascadeRunResult(
                cascade_run_id=cascade_run_id,
                tenant_id=tenant_id, config_id=config_id,
                period_start=period_start,
                status="FAILED",
                stages=stages,
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        # Stage 2 — Integrated Balancer (constrained_live).
        balancer = self._run_balancer(
            unconstrained_plan_id=movement.plan_id,
            cascade_run_id=cascade_run_id,
            resolve_capacity_from_db=resolve_capacity_from_db,
        )
        stages.append(balancer)

        return CascadeRunResult(
            cascade_run_id=cascade_run_id,
            tenant_id=tenant_id, config_id=config_id,
            period_start=period_start,
            status="OK" if balancer.status == "OK" else "FAILED",
            stages=stages,
            started_at=started_at,
            completed_at=datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # Stage runners (per-stage transaction boundary)
    # ------------------------------------------------------------------

    def _run_movement(
        self, *,
        tenant_id: int, config_id: int,
        period_start: date, period_days: int,
        scenario_id: Optional[int],
        forecast_plan_version: str,
        cascade_run_id: str,
    ) -> StageResult:
        try:
            svc = MovementPlannerService(self.db)
            result = svc.plan_movement(
                tenant_id=tenant_id, config_id=config_id,
                period_start=period_start, period_days=period_days,
                scenario_id=scenario_id,
                forecast_plan_version=forecast_plan_version,
                cascade_run_id=cascade_run_id,
            )
            self.db.commit()
            return StageResult(
                stage="movement", status="OK",
                plan_id=result.plan_id,
                summary={
                    "items_written": result.items_written,
                    "items_with_carrier": result.items_with_carrier,
                    "items_without_carrier": result.items_without_carrier,
                    "skipped_zero_loads": result.skipped_zero_loads,
                },
            )
        except Exception as exc:
            self.db.rollback()
            logger.exception(
                "L3 cascade — Movement Planner failed "
                "(cascade_run_id=%s tenant=%s period=%s)",
                cascade_run_id, tenant_id, period_start,
            )
            return StageResult(
                stage="movement", status="FAILED",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _run_balancer(
        self, *,
        unconstrained_plan_id: int,
        cascade_run_id: str,
        resolve_capacity_from_db: bool,
    ) -> StageResult:
        try:
            svc = IntegratedBalancerService(self.db)
            result = svc.balance_plan(
                unconstrained_plan_id=unconstrained_plan_id,
                cascade_run_id=cascade_run_id,
                resolve_capacity_from_db=resolve_capacity_from_db,
            )
            self.db.commit()
            return StageResult(
                stage="balancer", status="OK",
                plan_id=result.constrained_plan_id,
                summary={
                    "items_cloned": result.items_cloned,
                    "items_escalated": result.items_escalated,
                    "optimization_method": result.optimization_method,
                },
            )
        except Exception as exc:
            self.db.rollback()
            logger.exception(
                "L3 cascade — Integrated Balancer failed "
                "(cascade_run_id=%s unconstrained_plan_id=%s)",
                cascade_run_id, unconstrained_plan_id,
            )
            return StageResult(
                stage="balancer", status="FAILED",
                error=f"{type(exc).__name__}: {exc}",
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _new_run_id(tenant_id: int) -> str:
        """Build a human-readable + globally-unique cascade run id.

        Shape: ``l3_{tenant_id}_{utc_iso}_{uuid8}``. The tenant id +
        timestamp prefix lets log-grep match by tenant + day; the
        UUID suffix guarantees uniqueness across same-second runs.
        """
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        suffix = uuid.uuid4().hex[:8]
        return f"{_RUN_ID_PREFIX}_{tenant_id}_{ts}_{suffix}"

    def _existing_l3_plan(
        self, tenant_id: int, period_start: date,
    ) -> Optional[TransportationPlan]:
        """Return any existing L3-cascade plan for the (tenant, period)
        that idempotency should skip on. Matches by ``plan_start_date``
        + the ``l3_`` prefix on ``cascade_run_id`` so plans produced
        by a different cascade (or by manual operator action with no
        cascade_run_id) don't trigger the skip.
        """
        return (
            self.db.query(TransportationPlan)
            .filter(
                TransportationPlan.tenant_id == tenant_id,
                TransportationPlan.plan_start_date == period_start,
                TransportationPlan.cascade_run_id.like(f"{_RUN_ID_PREFIX}_%"),
            )
            .first()
        )


__all__ = [
    "CascadeRunResult",
    "L3CascadeRunner",
    "StageResult",
]
