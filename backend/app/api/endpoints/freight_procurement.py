"""
Freight Procurement API — Carrier Waterfall Tendering

The first TMS-native endpoint wiring the full inference loop:
  Load READY → FreightProcurementTRM → tender decision →
  governance → Decision Stream → user INSPECT/OVERRIDE

Endpoints:
  POST /freight-procurement/evaluate/{load_id} — evaluate one load
  POST /freight-procurement/evaluate-all — evaluate all pending loads
  GET  /freight-procurement/waterfall/{load_id} — view carrier waterfall
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.models.tms_entities import (
    Load, LoadStatus, Carrier, CarrierTMSProfile, CarrierLane, FreightRate,
    FreightTender, TenderStatus,
)
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/freight-procurement", tags=["freight-procurement"])


class EvaluationResult(BaseModel):
    load_id: int
    load_number: str
    carrier_id: Optional[int] = None
    carrier_name: Optional[str] = None
    offered_rate: float = 0
    action_name: str
    composite_score: Optional[float] = None
    confidence: float = 1.0
    urgency: float = 0.5
    reasoning: str = ""
    decision_method: str = "heuristic"


class WaterfallEntry(BaseModel):
    carrier_id: int
    carrier_name: str
    carrier_code: str
    rate: float
    priority: int
    acceptance_pct: float
    tender_status: Optional[str] = None


class WaterfallResponse(BaseModel):
    load_id: int
    load_number: str
    load_status: str
    carriers: List[WaterfallEntry]
    selected_carrier_id: Optional[int] = None


@router.post("/evaluate/{load_id}", response_model=EvaluationResult)
async def evaluate_load(
    load_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Evaluate carrier selection for a specific load using FreightProcurementTRM."""
    from sqlalchemy.orm import Session as SyncSession
    from app.db.session import sync_session_factory

    with sync_session_factory() as sync_db:
        load_q = select(Load).where(Load.id == load_id)
        if user.tenant_id is not None:
            load_q = load_q.where(Load.tenant_id == user.tenant_id)
        load = sync_db.execute(load_q).scalar_one_or_none()
        if not load:
            raise HTTPException(404, f"Load {load_id} not found")

        from app.services.powell.freight_procurement_trm import FreightProcurementTRM
        effective_tenant_id = user.tenant_id if user.tenant_id is not None else load.tenant_id
        trm = FreightProcurementTRM(sync_db, effective_tenant_id, load.config_id or 0)

        # Try loading checkpoint
        import os
        ckpt_path = f"/app/training_data/checkpoints/trm_freight_procurement_bc_v1.pt"
        if os.path.exists(ckpt_path):
            trm.load_checkpoint(ckpt_path)

        result = trm.evaluate_and_persist(load)
        if not result:
            raise HTTPException(422, "No carrier candidates for this load")
        sync_db.commit()

        return EvaluationResult(**{k: v for k, v in result.items()
                                   if k in EvaluationResult.model_fields})


@router.post("/evaluate-all", response_model=List[EvaluationResult])
async def evaluate_all_pending(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Evaluate all pending loads for the current tenant."""
    from app.db.session import sync_session_factory

    with sync_session_factory() as sync_db:
        from app.services.powell.freight_procurement_trm import FreightProcurementTRM
        trm = FreightProcurementTRM(sync_db, user.tenant_id, 0)

        import os
        ckpt_path = f"/app/training_data/checkpoints/trm_freight_procurement_bc_v1.pt"
        if os.path.exists(ckpt_path):
            trm.load_checkpoint(ckpt_path)

        results = trm.evaluate_pending_loads()
        return [
            EvaluationResult(**{k: v for k, v in r.items()
                                if k in EvaluationResult.model_fields})
            for r in results
        ]


@router.get("/waterfall/{load_id}", response_model=WaterfallResponse)
async def get_carrier_waterfall(
    load_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """View the carrier waterfall for a load — all candidates ranked by score."""
    from app.db.session import sync_session_factory

    with sync_session_factory() as sync_db:
        load_q = select(Load).where(Load.id == load_id)
        if user.tenant_id is not None:
            load_q = load_q.where(Load.tenant_id == user.tenant_id)
        load = sync_db.execute(load_q).scalar_one_or_none()
        if not load:
            raise HTTPException(404, f"Load {load_id} not found")

        # §3.47 Phase 2 — outer-join CarrierTMSProfile so dispatch
        # fields (`code` etc.) come from the new substrate. Outer-join
        # keeps the row when no profile exists yet (defensive).
        lane_q = (
            select(CarrierLane, Carrier, CarrierTMSProfile)
            .join(Carrier, CarrierLane.carrier_id == Carrier.id)
            .outerjoin(
                CarrierTMSProfile,
                CarrierTMSProfile.carrier_id == Carrier.id,
            )
            .where(CarrierLane.is_active.is_(True))
            .order_by(CarrierLane.priority)
        )
        if user.tenant_id is not None:
            lane_q = lane_q.where(CarrierLane.tenant_id == user.tenant_id)
        else:
            lane_q = lane_q.where(CarrierLane.tenant_id == load.tenant_id)
        cls = sync_db.execute(lane_q).all()

        tenders = sync_db.execute(
            select(FreightTender).where(FreightTender.load_id == load_id)
        ).scalars().all()
        tender_status_by_carrier = {t.carrier_id: t.status.value for t in tenders}

        entries = []
        tenant_for_rates = user.tenant_id if user.tenant_id is not None else load.tenant_id
        for cl, carrier, profile in cls[:20]:
            rate = sync_db.execute(
                select(FreightRate.rate_flat).where(
                    and_(
                        FreightRate.carrier_id == carrier.id,
                        FreightRate.tenant_id == tenant_for_rates,
                        FreightRate.is_active.is_(True),
                    )
                ).limit(1)
            ).scalar_one_or_none()

            entries.append(WaterfallEntry(
                carrier_id=carrier.id,
                carrier_name=carrier.display_name,
                carrier_code=profile.code if profile else None,
                rate=float(rate) if rate else 0,
                priority=cl.priority,
                acceptance_pct=0.85,
                tender_status=tender_status_by_carrier.get(carrier.id),
            ))

        selected = next(
            (t.carrier_id for t in tenders if t.status == TenderStatus.ACCEPTED),
            None,
        )

        return WaterfallResponse(
            load_id=load.id,
            load_number=load.load_number,
            load_status=load.status.value,
            carriers=entries,
            selected_carrier_id=selected,
        )
