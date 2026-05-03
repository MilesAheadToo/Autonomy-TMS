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

**Shared cascade_run.** Both stages' TransportationPlan rows stamp
``cascade_run_id`` with the same integer FK pointing at a
``cascade_run`` row in Core's substrate (§3.46 Phase 3 / Phase
3-followon). The runner generates a human-readable
``cascade_run_label`` (shape: ``l3_<tenant>_<utc-iso>_<uuid8>``)
that lives on ``cascade_run.cascade_run_id`` for log-grep + audit;
plans link via the integer FK on ``cascade_run.id`` for fast joins.

**Idempotency.** ``run()`` checks for an existing
``TransportationPlan`` with ``plan_start_date == period_start`` and
a parent ``cascade_run`` with ``plane_type == L3_TRANSPORT`` for the
tenant; skips with ``status="SKIPPED"`` unless ``force=True``. This
makes the cron in §3.46 Phase 2 safe to fire-and-forget — re-running
on the same period is a no-op.

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
    """Human-readable cascade-run label (shape:
    ``l3_<tenant>_<utc-iso>_<uuid8>``). Stored on
    ``CascadeRun.cascade_run_id`` and useful for log-grep / debug.
    Plans produced by the cascade FK to ``CascadeRun.id`` (the int
    PK), exposed below as ``cascade_run_pk``."""

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

    cascade_run_pk: Optional[int] = None
    """Integer FK on ``cascade_run.id`` — the parent substrate row
    that the L3 plans stamp via ``TransportationPlan.cascade_run_id``
    (an Integer FK after §3.46 Phase 3-followon). ``None`` when the
    substrate write failed (e.g., older Core pin / partial deployment)
    — the cascade still ran to completion via the per-stage commits;
    plans just lack the substrate join key."""


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

        §3.46 Phase 3-followon: writes a :class:`CascadeRun` row to
        Core's substrate at the start of each run, updates per-stage
        counters as stages complete, and finalises status /
        completed_at / ``run_metadata`` at the end. When Core's
        ``CascadeRun`` ORM isn't importable (older pin / partial
        deployment), the writes silently no-op and the runner
        behaves exactly as before.
        """
        cascade_run_label = self._new_run_id(tenant_id)
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
                # SKIPPED runs still get a CascadeRun row so the
                # "did the cron consider this period?" question has a
                # definitive answer in the substrate.
                cascade_run_pk = self._record_cascade_run(
                    cascade_run_label=cascade_run_label,
                    tenant_id=tenant_id,
                    period_start=period_start,
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    status="SKIPPED",
                    stages=[],
                )
                return CascadeRunResult(
                    cascade_run_id=cascade_run_label,
                    tenant_id=tenant_id, config_id=config_id,
                    period_start=period_start,
                    status="SKIPPED",
                    stages=[],
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    cascade_run_pk=cascade_run_pk,
                )

        # Open the cascade-run row at the start of execution
        # (status=RUNNING). Stage updates and the final status
        # land via _record_cascade_run on the same row by
        # cascade_run_label.
        cascade_run_pk = self._record_cascade_run(
            cascade_run_label=cascade_run_label,
            tenant_id=tenant_id,
            period_start=period_start,
            started_at=started_at,
            status="RUNNING",
            stages=[],
        )

        stages: List[StageResult] = []

        # Stage 1 — Movement Planner (unconstrained_reference).
        # ``cascade_run_id=cascade_run_pk`` stamps the int FK on
        # ``TransportationPlan.cascade_run_id`` (Integer FK after
        # §3.46 Phase 3-followon). When pk is None (substrate
        # write failed), the column stays NULL — the cascade still
        # runs to completion; consumers just lose the substrate
        # join key.
        movement = self._run_movement(
            tenant_id=tenant_id, config_id=config_id,
            period_start=period_start, period_days=period_days,
            scenario_id=scenario_id,
            forecast_plan_version=forecast_plan_version,
            cascade_run_id=cascade_run_pk,
        )
        stages.append(movement)
        if movement.status != "OK" or movement.plan_id is None:
            completed_at = datetime.utcnow()
            self._record_cascade_run(
                cascade_run_label=cascade_run_label,
                tenant_id=tenant_id,
                period_start=period_start,
                started_at=started_at,
                completed_at=completed_at,
                status="FAILED",
                stages=stages,
            )
            return CascadeRunResult(
                cascade_run_id=cascade_run_label,
                tenant_id=tenant_id, config_id=config_id,
                period_start=period_start,
                status="FAILED",
                stages=stages,
                started_at=started_at,
                completed_at=completed_at,
                cascade_run_pk=cascade_run_pk,
            )

        # Stage 2 — Integrated Balancer (constrained_live).
        balancer = self._run_balancer(
            unconstrained_plan_id=movement.plan_id,
            cascade_run_id=cascade_run_pk,
            resolve_capacity_from_db=resolve_capacity_from_db,
        )
        stages.append(balancer)

        completed_at = datetime.utcnow()
        final_status = "OK" if balancer.status == "OK" else "FAILED"
        self._record_cascade_run(
            cascade_run_label=cascade_run_label,
            tenant_id=tenant_id,
            period_start=period_start,
            started_at=started_at,
            completed_at=completed_at,
            status=final_status,
            stages=stages,
        )

        return CascadeRunResult(
            cascade_run_id=cascade_run_label,
            tenant_id=tenant_id, config_id=config_id,
            period_start=period_start,
            status=final_status,
            stages=stages,
            started_at=started_at,
            completed_at=completed_at,
            cascade_run_pk=cascade_run_pk,
        )

    # ------------------------------------------------------------------
    # Stage runners (per-stage transaction boundary)
    # ------------------------------------------------------------------

    def _run_forecast(
        self, *,
        tenant_id: int, config_id: int,
        period_start: date, period_days: int,
        scenario_id: Optional[int],
        forecast_plan_version: str,
        cascade_run_id: str,
    ) -> StageResult:
        """Stage 0: build LaneForecastInputs from observed shipments
        and publish LaneVolumePlan rows via TacticalForecastService.

        Threads the lifecycle reactor (DP→TMS A2A bridge) so NPI/EOL
        adjustments published in DP propagate into lane volumes
        before the Movement Planner sees them. Reactor failures are
        already handled inside publish_forecast (logged WARNING and
        the run proceeds without overlays); this stage's try/except
        only catches catastrophic failures (e.g. SQL error during
        the forecast publish itself).

        Empty-history tenants (no shipment data) get OK status with
        rows_written=0 — not a failure. The Movement Planner stage
        will produce an empty plan in that case.
        """
        try:
            from app.services.powell.lane_forecast_input_builder import (
                LaneForecastInputBuilder,
            )
            from app.services.powell.lifecycle_reactor_factory import (
                make_lifecycle_reactor,
            )
            from app.services.powell.tactical_forecast_service import (
                TacticalForecastService,
            )

            builder = LaneForecastInputBuilder(period_days=period_days)
            inputs = builder.build_inputs(
                self.db,
                tenant_id=tenant_id,
                config_id=config_id,
                period_start=period_start,
            )

            reactor = make_lifecycle_reactor(
                self.db, tenant_id=tenant_id, config_id=config_id,
            )

            svc = TacticalForecastService(self.db)
            result = svc.publish_forecast(
                tenant_id=tenant_id,
                config_id=config_id,
                inputs=inputs,
                scenario_id=scenario_id,
                plan_version=forecast_plan_version,
                lifecycle_reactor=reactor,
            )
            self.db.commit()
            return StageResult(
                stage="forecast", status="OK",
                plan_id=None,  # publish_forecast writes many rows, not one plan
                summary={
                    "lanes_evaluated": len(inputs),
                    "rows_written": result.rows_written,
                    "skipped_deferred": result.skipped_deferred,
                    "lifecycle_reactor_active": reactor is not None,
                },
            )
        except Exception as exc:
            self.db.rollback()
            logger.exception(
                "L3 cascade — Tactical Forecast failed "
                "(cascade_run_id=%s tenant=%s period=%s)",
                cascade_run_id, tenant_id, period_start,
            )
            return StageResult(
                stage="forecast", status="FAILED",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _run_movement(
        self, *,
        tenant_id: int, config_id: int,
        period_start: date, period_days: int,
        scenario_id: Optional[int],
        forecast_plan_version: str,
        cascade_run_id: Optional[int],
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
        cascade_run_id: Optional[int],
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
        that idempotency should skip on.

        §3.46 Phase 3-followon: ``TransportationPlan.cascade_run_id``
        is now an Integer FK on ``cascade_run.id``. Matching the
        ``L3_TRANSPORT`` plane_type requires a JOIN through the
        substrate; the old ``LIKE 'l3_%'`` String filter is gone.

        Plans without a cascade_run_id (manual operator action / pre-
        substrate vintage) don't trigger the skip — same semantics
        as the pre-followon code, just expressed via JOIN now.
        """
        try:
            from azirella_data_model.powell.cascade_run import (
                CascadePlaneType,
                CascadeRun,
            )
        except ImportError:
            # Substrate not deployed yet — nothing to JOIN against;
            # idempotency check trivially returns None (no matching
            # L3 plan in the substrate sense). Pre-substrate plans
            # would have NULL cascade_run_id; we can't distinguish
            # them from manual operator plans, so the safe answer is
            # "no match → run the cascade".
            return None

        return (
            self.db.query(TransportationPlan)
            .join(
                CascadeRun,
                CascadeRun.id == TransportationPlan.cascade_run_id,
            )
            .filter(
                TransportationPlan.tenant_id == tenant_id,
                TransportationPlan.plan_start_date == period_start,
                CascadeRun.plane_type == CascadePlaneType.L3_TRANSPORT,
            )
            .first()
        )

    # ------------------------------------------------------------------
    # §3.46 Phase 3-followon — CascadeRun substrate writes
    # ------------------------------------------------------------------

    def _record_cascade_run(
        self, *,
        cascade_run_label: str,
        tenant_id: int,
        period_start: date,
        started_at: datetime,
        completed_at: Optional[datetime] = None,
        status: str,
        stages: List[StageResult],
    ) -> Optional[int]:
        """Upsert a :class:`CascadeRun` row in Core's substrate.

        Inserts on first call (status=RUNNING at run start) and
        updates on subsequent calls (status / completed_at / per-stage
        counters / run_metadata at terminal states). Keyed by the
        unique ``cascade_run_id`` string (the human-readable
        identifier — exposed here as ``cascade_run_label`` to make
        the role distinct from the integer FK
        ``TransportationPlan.cascade_run_id`` after §3.46 Phase 3-followon).

        Returns the row's integer primary key (``CascadeRun.id``),
        which downstream stages use as the FK on
        ``TransportationPlan.cascade_run_id``. Returns ``None`` when
        Core's ``CascadeRun`` substrate isn't importable (older Core
        pin / partial deployment); the runner falls back to plans-
        without-substrate-link in that case.

        Defensive: substrate write failures are logged but never
        surface to the caller — the cascade's primary outputs (the
        two TransportationPlan rows) are already committed by the
        stage runners. Substrate emission is observability, not
        load-bearing.
        """
        try:
            from azirella_data_model.powell.cascade_run import (
                CascadePlaneType,
                CascadeRun,
                CascadeRunStatus,
            )
        except ImportError:
            return None

        n_total = len(stages) if stages else 2  # L3 has 2 stages
        n_ok = sum(1 for s in stages if s.status == "OK")
        n_failed = sum(1 for s in stages if s.status == "FAILED")

        # Build run_metadata with per-stage plan ids + summary.
        # Strict shape per plane_type — L3 transport always emits
        # the same fields so the dashboard / audit query can rely
        # on them.
        run_metadata: dict = {
            "stages": [
                {
                    "stage": s.stage,
                    "status": s.status,
                    "plan_id": s.plan_id,
                    "error": s.error,
                    "summary": s.summary,
                }
                for s in stages
            ],
        }
        if stages:
            mvmt = next((s for s in stages if s.stage == "movement"), None)
            balncer = next((s for s in stages if s.stage == "balancer"), None)
            if mvmt and mvmt.plan_id is not None:
                run_metadata["unconstrained_plan_id"] = mvmt.plan_id
            if balncer and balncer.plan_id is not None:
                run_metadata["constrained_plan_id"] = balncer.plan_id
            if balncer and balncer.summary:
                opt = balncer.summary.get("optimization_method")
                if opt is not None:
                    run_metadata["optimization_method"] = opt
                escalated = balncer.summary.get("items_escalated")
                if escalated is not None:
                    run_metadata["items_escalated"] = escalated

        error_summary: Optional[str] = None
        if status == "FAILED":
            failed_stage = next(
                (s for s in stages if s.status == "FAILED"), None,
            )
            if failed_stage:
                error_summary = (
                    f"{failed_stage.stage}: {failed_stage.error}"
                )

        try:
            existing = (
                self.db.query(CascadeRun)
                .filter(CascadeRun.cascade_run_id == cascade_run_label)
                .first()
            )
            if existing is None:
                row = CascadeRun(
                    cascade_run_id=cascade_run_label,
                    tenant_id=tenant_id,
                    plane_type=CascadePlaneType.L3_TRANSPORT,
                    period_start=datetime.combine(
                        period_start, datetime.min.time(),
                    ),
                    started_at=started_at,
                    completed_at=completed_at,
                    status=CascadeRunStatus(status),
                    n_stages_total=n_total,
                    n_stages_ok=n_ok,
                    n_stages_failed=n_failed,
                    error_summary=error_summary,
                    run_metadata=run_metadata if stages else None,
                )
                self.db.add(row)
                self.db.flush()  # populate row.id without committing
                pk = row.id
            else:
                existing.status = CascadeRunStatus(status)
                existing.completed_at = completed_at
                existing.n_stages_total = n_total
                existing.n_stages_ok = n_ok
                existing.n_stages_failed = n_failed
                existing.error_summary = error_summary
                if stages:
                    existing.run_metadata = run_metadata
                pk = existing.id
            self.db.commit()
            return pk
        except Exception:
            # Don't let CascadeRun-write failures surface to the
            # caller — the cascade's primary outputs (the two
            # TransportationPlan rows) are already committed by the
            # stage runners. Substrate emission is observability,
            # not load-bearing.
            self.db.rollback()
            logger.exception(
                "L3 cascade — failed to record CascadeRun row "
                "(cascade_run_label=%s); cascade output is unaffected",
                cascade_run_label,
            )
            return None


__all__ = [
    "CascadeRunResult",
    "L3CascadeRunner",
    "StageResult",
]
