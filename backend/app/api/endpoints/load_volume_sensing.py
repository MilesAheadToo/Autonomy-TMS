"""
Demand Sensing TRM API — Short-Horizon Forecast Adjustment (SENSE phase)

Eighth TMS-native TRM endpoint. Fills out the SENSE phase alongside
`/capacity-promise-trm/*` and `/shipment-tracking-trm/*`.

Evaluates ShippingForecast rows against order-pipeline velocity +
trailing actuals + structural bias tracking, and classifies each
forecast into ACCEPT (nominal) or MODIFY (adjust by proposed Δ). No
ShippingForecast mutation — demand sensing is observational in v1.

Endpoints:
  POST /demand-sensing-trm/evaluate/{forecast_id} — evaluate one + log
  POST /demand-sensing-trm/evaluate-all           — evaluate every pending forecast
  GET  /demand-sensing-trm/status/{forecast_id}   — stateless preview
"""
from __future__ import annotations

import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.models.tms_planning import ShippingForecast
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demand-sensing-trm", tags=["demand-sensing-trm"])


class DemandSensingResult(BaseModel):
    forecast_id: int
    lane_id: Optional[int] = None
    forecast_date: Optional[str] = None
    period_type: str = "WEEK"
    mode: Optional[str] = None
    forecast_loads: float = 0.0
    proposed_adjustment: float = 0.0
    proposed_forecast: float = 0.0
    week_over_week_change_pct: float = 0.0
    pipeline_velocity_24h: float = 0.0
    pipeline_velocity_prior_24h: float = 0.0
    forecast_mape: float = 0.0
    action_name: str = "ACCEPT"
    confidence: float = 1.0
    urgency: float = 0.1
    reasoning: str = ""
    decision_method: str = "heuristic"


def _result_to_schema(result: dict) -> DemandSensingResult:
    """Filter dict to fields the pydantic schema accepts."""
    return DemandSensingResult(**{
        k: v for k, v in result.items()
        if k in DemandSensingResult.model_fields
    })


@router.post("/evaluate/{forecast_id}", response_model=DemandSensingResult)
async def evaluate_demand_sensing(
    forecast_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Evaluate demand-sensing for one ShippingForecast and emit the log line.

    No ShippingForecast mutation — demand sensing is observational.
    """
    from app.db.session import sync_session_factory

    with sync_session_factory() as sync_db:
        forecast_q = select(ShippingForecast).where(ShippingForecast.id == forecast_id)
        forecast = sync_db.execute(forecast_q).scalar_one_or_none()
        if not forecast:
            raise HTTPException(404, f"ShippingForecast {forecast_id} not found")

        # Tenant check via config — ShippingForecast has no tenant_id column
        # but config_id → supply_chain_configs.tenant_id.
        if user.tenant_id is not None:
            from app.models.supply_chain_config import SupplyChainConfig
            cfg = sync_db.execute(
                select(SupplyChainConfig).where(SupplyChainConfig.id == forecast.config_id)
            ).scalar_one_or_none()
            if not cfg or cfg.tenant_id != user.tenant_id:
                raise HTTPException(404, f"ShippingForecast {forecast_id} not found")

        from app.services.powell.demand_sensing_trm import DemandSensingTRM
        effective_tenant_id = (
            user.tenant_id if user.tenant_id is not None else forecast.config_id
        )
        trm = DemandSensingTRM(sync_db, effective_tenant_id, forecast.config_id)

        result = trm.evaluate_and_log(forecast)
        if not result:
            raise HTTPException(422, "No demand-sensing decision produced")

        return _result_to_schema(result)


@router.post("/evaluate-all", response_model=List[DemandSensingResult])
async def evaluate_all_forecasts(
    config_id: int = Query(..., description="SupplyChainConfig id"),
    as_of: Optional[date] = Query(
        None, description="Reference date (defaults to today)"
    ),
    plan_version: str = Query(
        "live", description="ShippingForecast plan_version to evaluate"
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Evaluate every pending forecast for the given config.

    SYSTEM_ADMIN (tenant_id=None) can pass any config_id; tenant users
    must own the config (checked via SupplyChainConfig.tenant_id).
    """
    from app.db.session import sync_session_factory
    from app.models.supply_chain_config import SupplyChainConfig

    with sync_session_factory() as sync_db:
        cfg = sync_db.execute(
            select(SupplyChainConfig).where(SupplyChainConfig.id == config_id)
        ).scalar_one_or_none()
        if not cfg:
            raise HTTPException(404, f"SupplyChainConfig {config_id} not found")
        if user.tenant_id is not None and cfg.tenant_id != user.tenant_id:
            raise HTTPException(403, "Config belongs to a different tenant")

        from app.services.powell.demand_sensing_trm import DemandSensingTRM
        effective_tenant_id = user.tenant_id if user.tenant_id is not None else cfg.tenant_id
        trm = DemandSensingTRM(sync_db, effective_tenant_id, config_id)

        results = trm.evaluate_pending_forecasts(
            plan_version=plan_version, as_of=as_of
        )
        return [_result_to_schema(r) for r in results]


@router.get("/status/{forecast_id}", response_model=DemandSensingResult)
async def get_demand_sensing_status(
    forecast_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Live demand-sensing preview for any ShippingForecast. Pure read — no log."""
    from app.db.session import sync_session_factory

    with sync_session_factory() as sync_db:
        forecast_q = select(ShippingForecast).where(ShippingForecast.id == forecast_id)
        forecast = sync_db.execute(forecast_q).scalar_one_or_none()
        if not forecast:
            raise HTTPException(404, f"ShippingForecast {forecast_id} not found")

        if user.tenant_id is not None:
            from app.models.supply_chain_config import SupplyChainConfig
            cfg = sync_db.execute(
                select(SupplyChainConfig).where(SupplyChainConfig.id == forecast.config_id)
            ).scalar_one_or_none()
            if not cfg or cfg.tenant_id != user.tenant_id:
                raise HTTPException(404, f"ShippingForecast {forecast_id} not found")

        from app.services.powell.demand_sensing_trm import DemandSensingTRM
        effective_tenant_id = (
            user.tenant_id if user.tenant_id is not None else forecast.config_id
        )
        trm = DemandSensingTRM(sync_db, effective_tenant_id, forecast.config_id)

        result = trm.evaluate_forecast(forecast)
        if not result:
            raise HTTPException(422, "No demand-sensing decision produced")

        return _result_to_schema(result)
