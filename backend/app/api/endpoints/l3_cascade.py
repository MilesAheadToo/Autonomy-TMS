"""§3.46 Phase 4 — HTTP API endpoint for the L3 transport cascade.

Exposes :class:`L3CascadeRunner` as REST so web UIs / integration
tests / external orchestrators can invoke the cascade without going
through the CLI. Mirrors the CLI's argument vocabulary
(``scripts/l3_cascade_cli.py``) so a single config can drive either
surface — same scope semantics, same idempotency, same backfill
range.

Two endpoints:

- ``POST /api/v1/l3-cascade/run`` — invoke the cascade for a single
  ``(tenant_id, period_start)``. Returns the
  :class:`CascadeRunResult` (status / cascade_run_id / per-stage
  outcomes / cascade_run_pk).

- ``POST /api/v1/l3-cascade/backfill`` — invoke the cascade for a
  date range. Iterates one cascade per date and returns a list of
  results.

Both endpoints honour the same idempotency the runner enforces (an
existing L3 plan for the period skips with status="SKIPPED" unless
``force=true``).

Auth (§3.46 Phase 4 + Phase 5 — 2026-05-03):
``get_current_active_user`` is required. Cross-tenant invocation
requires *operator-level* authority — either ``is_superuser=True``
or ``user_type == UserTypeEnum.SYSTEM_ADMIN``. ``TENANT_ADMIN`` is
explicitly NOT operator-level: a tenant admin's authority is scoped
to *their own* tenant and they get the same same-tenant constraint
as a regular user. This matches how cross-tenant operations are
authorised elsewhere (see ``app/api/deps.py::require_tenant_admin``,
which gates on tenant-scope; the cascade's cross-tenant flavour
needs the strictly broader system-level role).

Plane-module placement: TMS-side decision-policy surface (mirrors
``scripts/l3_cascade_cli.py`` which is also TMS plane-specific). No
Core code.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_active_user
from app.services._l3_cascade_auth import (
    enforce_tenant_scope as _enforce_tenant_scope,
    is_operator as _is_operator,
)
from app.db.session import get_db, sync_session_factory
from app.models.user import User
from app.services.powell.l3_cascade_runner import (
    CascadeRunResult,
    L3CascadeRunner,
    StageResult,
)


logger = logging.getLogger(__name__)


router = APIRouter(tags=["l3-cascade"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class L3CascadeRunRequest(BaseModel):
    """Payload for ``POST /run``.

    Mirrors the single-period CLI mode
    (``--tenant-id N --period-start YYYY-MM-DD``).
    """

    tenant_id: int = Field(..., description="Target tenant.")
    config_id: Optional[int] = Field(
        None,
        description=(
            "SupplyChainConfig id. If omitted, the active BASELINE "
            "config for the tenant is used."
        ),
    )
    period_start: Optional[date] = Field(
        None,
        description=(
            "Planning-period start date. Defaults to today (UTC) if "
            "omitted."
        ),
    )
    period_days: int = Field(7, ge=1, le=90, description="Planning horizon.")
    force: bool = Field(
        False,
        description=(
            "Bypass the idempotency skip — re-run even if a prior "
            "L3 plan exists for the period."
        ),
    )
    resolve_capacity_from_db: bool = Field(
        True,
        description=(
            "Query CarrierCapacityCommitment rows for the LP. Set "
            "false for tenants without seeded commitments (Phase 1 "
            "clone-only fallback)."
        ),
    )


class L3CascadeBackfillRequest(BaseModel):
    """Payload for ``POST /backfill``.

    Mirrors the CLI backfill mode
    (``--tenant-id N --from-date X --to-date Y``).
    """

    tenant_id: int
    config_id: Optional[int] = None
    from_date: date
    to_date: date
    period_days: int = Field(7, ge=1, le=90)
    force: bool = False
    resolve_capacity_from_db: bool = True


class StageResultPayload(BaseModel):
    """Mirrors :class:`StageResult` for JSON output."""

    stage: str
    status: str
    plan_id: Optional[int] = None
    error: Optional[str] = None
    summary: dict = Field(default_factory=dict)


class L3CascadeRunResponse(BaseModel):
    """Mirrors :class:`CascadeRunResult` for JSON output."""

    cascade_run_id: str
    tenant_id: int
    config_id: int
    period_start: date
    status: str
    stages: List[StageResultPayload] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cascade_run_pk: Optional[int] = None


def _to_response(result: CascadeRunResult) -> L3CascadeRunResponse:
    return L3CascadeRunResponse(
        cascade_run_id=result.cascade_run_id,
        tenant_id=result.tenant_id,
        config_id=result.config_id,
        period_start=result.period_start,
        status=result.status,
        stages=[
            StageResultPayload(
                stage=s.stage,
                status=s.status,
                plan_id=s.plan_id,
                error=s.error,
                summary=s.summary,
            )
            for s in result.stages
        ],
        started_at=result.started_at,
        completed_at=result.completed_at,
        cascade_run_pk=result.cascade_run_pk,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_config_id(
    db_session, tenant_id: int, explicit_config_id: Optional[int],
) -> int:
    """Mirror the CLI's ``--config-id`` resolution: when not passed,
    look up the tenant's active BASELINE config."""
    if explicit_config_id is not None:
        return explicit_config_id
    from azirella_data_model.master.config import SupplyChainConfig
    row = (
        db_session.query(SupplyChainConfig.id)
        .filter(
            SupplyChainConfig.tenant_id == tenant_id,
            SupplyChainConfig.is_active.is_(True),
            SupplyChainConfig.scenario_type == "BASELINE",
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Tenant {tenant_id} has no active BASELINE config; "
                "pass config_id explicitly."
            ),
        )
    return int(row[0])


def _date_range(start: date, end: date) -> List[date]:
    n = (end - start).days
    return [start + timedelta(days=i) for i in range(n + 1)]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/run", response_model=L3CascadeRunResponse)
async def run_l3_cascade(
    body: L3CascadeRunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Invoke the L3 cascade for one (tenant, period).

    Idempotent: returns ``status="SKIPPED"`` when an L3 plan already
    exists for the period unless ``force=true``. Always returns —
    per-stage failures land in the response, not as exceptions.
    """
    _enforce_tenant_scope(current_user, body.tenant_id)
    period_start = body.period_start or datetime.utcnow().date()

    # Use sync session — L3 services are sync (mirror the CLI's
    # invocation pattern; APScheduler also uses sync sessions).
    with sync_session_factory() as sync_db:
        config_id = _resolve_config_id(sync_db, body.tenant_id, body.config_id)
        runner = L3CascadeRunner(sync_db)
        result = runner.run(
            tenant_id=body.tenant_id,
            config_id=config_id,
            period_start=period_start,
            period_days=body.period_days,
            force=body.force,
            resolve_capacity_from_db=body.resolve_capacity_from_db,
        )
    logger.info(
        "L3 cascade API run — tenant=%s period=%s status=%s "
        "cascade_run_id=%s user=%s",
        body.tenant_id, period_start, result.status,
        result.cascade_run_id, getattr(current_user, "id", None),
    )
    return _to_response(result)


@router.post("/backfill", response_model=List[L3CascadeRunResponse])
async def backfill_l3_cascade(
    body: L3CascadeBackfillRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Invoke the L3 cascade for every date in ``[from_date, to_date]``
    inclusive. Returns one :class:`L3CascadeRunResponse` per date.

    Per-period results are independent: an OK on day 1 + a FAILED on
    day 2 produces two separate response entries; the failure on day 2
    does not abort the iteration.
    """
    _enforce_tenant_scope(current_user, body.tenant_id)
    if body.from_date > body.to_date:
        raise HTTPException(
            status_code=400,
            detail="from_date must be ≤ to_date",
        )

    results: List[L3CascadeRunResponse] = []
    with sync_session_factory() as sync_db:
        config_id = _resolve_config_id(sync_db, body.tenant_id, body.config_id)
        for period_start in _date_range(body.from_date, body.to_date):
            runner = L3CascadeRunner(sync_db)
            result = runner.run(
                tenant_id=body.tenant_id,
                config_id=config_id,
                period_start=period_start,
                period_days=body.period_days,
                force=body.force,
                resolve_capacity_from_db=body.resolve_capacity_from_db,
            )
            results.append(_to_response(result))
    logger.info(
        "L3 cascade API backfill — tenant=%s range=%s..%s n=%d user=%s",
        body.tenant_id, body.from_date, body.to_date, len(results),
        getattr(current_user, "id", None),
    )
    return results
