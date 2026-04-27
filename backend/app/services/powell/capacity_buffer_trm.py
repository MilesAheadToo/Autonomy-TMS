"""
CapacityBufferTRM — Lane-Level Capacity Buffer Sizing (ASSESS phase)

Ninth TMS-native TRM. Fills out the ASSESS phase alongside
ExceptionManagementTRM (item 5). Transport analog of SCP's
InventoryBufferTRM — newsvendor-style safety sizing, but on carrier
capacity rather than physical stock.

Evaluates CapacityTarget rows and decides: ACCEPT (nominal) or MODIFY
(resize buffer to `quantity` loads) based on:

1. Conformal P90–P50 spread (primary volatility signal, distribution-free).
2. Tender-reject rate (primary capacity-side stress signal).
3. Demand CV fallback when conformal intervals are unavailable.
4. Peak-season multiplier.
5. Demand trend > 0.1 loads/period.
6. Recent capacity-miss count (≥3 → additional buffer).
7. Consistent oversupply → buffer reduction.

Trigger entity: `CapacityTarget` row (one per lane / mode / period).
One evaluation per target per cycle. Not scheduled by default — called
from the daily cascade (post-ShippingForecast publish) or ad-hoc via
endpoint.

Feature-vector sources (v1):

- baseline_buffer_loads / buffer_policy ← CapacityTarget.buffer_loads / .buffer_policy
- forecast_loads ← CapacityTarget.required_loads
- forecast_p10 / forecast_p90 ← CapacityTarget.required_loads_p10 / _p90
- committed_loads / contract_capacity ← CapacityTarget.committed_loads / .available_loads
- spot_availability ← (available_loads − committed_loads), floored at 0
- recent_tender_reject_rate ← rolling 14-day rate of DECLINED+EXPIRED
    tenders on this lane's carriers for this tenant (count matches the
    industry definition of OTRI: declined / (declined + accepted))
- recent_capacity_miss_count ← count of CapacityTargets in the trailing
    4 periods for this lane where gap_loads > 0
- demand_cv ← coefficient of variation of required_loads across the
    trailing 4 same-period CapacityTargets on this lane (fallback)
- demand_trend ← slope of the last 4 CapacityTargets' required_loads
    normalised by the mean (0 = flat, +0.1 = 10%/period growth)

Not yet sourced (honest defaults):
- is_peak_season  (Sprint 2: calendar / seasonal_index table)
- avg_spot_premium_pct (Sprint 2: needs spot-rate ingestion — FreightWaves
  SONAR / DAT / Truckstop ratesheets)

Observational v1: MODIFY decisions are logged at warning level and
returned in the API response; no CapacityTarget mutation. The write
path lands alongside PREPARE.3's dual-write to core.agent_decisions
with decision_type=CAPACITY_BUFFER.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from app.models.tms_entities import FreightTender, Load, TenderStatus
from app.models.tms_planning import CapacityTarget


from app.services.powell.agent_decision_writer import record_trm_decision
logger = logging.getLogger(__name__)



# Period-type → window days.
_PERIOD_DAYS = {"DAY": 1, "WEEK": 7, "MONTH": 30}


class CapacityBufferTRM:
    """
    Evaluates capacity buffers and resizes per the newsvendor-inspired
    heuristic.

    Lifecycle:
        trm = CapacityBufferTRM(db_session, tenant_id, config_id)
        decisions = trm.evaluate_pending_targets()

    No CapacityTarget mutation in v1. MODIFY decisions surface the
    proposed new buffer (`quantity`); downstream orchestration or a
    human reviewer applies it.
    """

    DEFAULT_PERIOD_DAYS = 7
    TENDER_LOOKBACK_DAYS = 14
    HISTORY_PERIODS = 4  # For CV / trend / capacity-miss counts

    def __init__(self, db: Session, tenant_id: int, config_id: int):
        self.db = db
        self.tenant_id = tenant_id
        self.config_id = config_id
        self._model = None

        from azirella_data_model.powell.tms.heuristic_library.dispatch import (
            compute_tms_decision,
        )
        from azirella_data_model.powell.tms.heuristic_library.base import (
            CapacityBufferState,
        )
        self._compute_decision = compute_tms_decision
        self._StateClass = CapacityBufferState

    def load_checkpoint(self, checkpoint_path: str) -> bool:
        """Load a trained BC checkpoint. Returns True on success."""
        ckpt = load_bc_checkpoint(checkpoint_path, "capacity_buffer")
        if ckpt is None:
            return False
        self._model = ckpt
        return True

    def find_pending_targets(
        self,
        plan_version: str = "live",
        as_of: Optional[date] = None,
    ) -> List[CapacityTarget]:
        """Active CapacityTargets whose period covers `as_of` (defaults to today)."""
        reference = as_of or date.today()
        query = (
            select(CapacityTarget)
            .where(
                CapacityTarget.tenant_id == self.tenant_id,
                CapacityTarget.config_id == self.config_id,
                CapacityTarget.plan_version == plan_version,
                CapacityTarget.target_date <= reference,
            )
            .order_by(CapacityTarget.target_date.desc(), CapacityTarget.id)
        )
        return list(self.db.execute(query).scalars().all())

    def evaluate_target(self, target: CapacityTarget) -> Optional[Dict[str, Any]]:
        """Evaluate buffer-sizing decision for one CapacityTarget. Never mutates it."""
        state = self._build_state(target)

        action_name_map = {
            0: "ACCEPT", 1: "REJECT", 2: "DEFER", 3: "ESCALATE", 4: "MODIFY",
        }

        if self._model is not None:
            from app.services.powell.bc_checkpoint_loader import predict_action
            try:
                action, confidence, probs = predict_action(self._model, state)
                action_name = action_name_map.get(action, "UNKNOWN")
                urgency = (
                    confidence if action != 0 else max(0.0, 1.0 - confidence)
                )
                return {
                    "target_id": target.id,
                    "lane_id": target.lane_id,
                    "mode": target.mode,
                    "target_date": target.target_date.isoformat() if target.target_date else None,
                    "period_type": target.period_type or "WEEK",
                    "baseline_buffer_loads": int(state.baseline_buffer_loads),
                    "proposed_buffer_loads": int(state.baseline_buffer_loads),
                    "forecast_loads": int(state.forecast_loads),
                    "committed_loads": int(state.committed_loads),
                    "recent_tender_reject_rate": state.recent_tender_reject_rate,
                    "recent_capacity_miss_count": state.recent_capacity_miss_count,
                    "demand_cv": state.demand_cv,
                    "demand_trend": state.demand_trend,
                    "action": action,
                    "action_name": action_name,
                    "confidence": confidence,
                    "urgency": urgency,
                    "reasoning": (
                        f"BC model predicted {action_name} (p={confidence:.3f})"
                    ),
                    "decision_method": "trm_model",
                    "scoring_detail": {
                        "model_probs": probs,
                        "model_val_acc": self._model.best_val_acc,
                    },
                }
            except Exception as e:
                logger.warning(
                    "CapacityBuffer BC inference failed (falling back "
                    "to heuristic): %s", e,
                )

        decision = self._compute_decision("capacity_buffer", state)

        action_name = action_name_map.get(decision.action, "UNKNOWN")

        proposed_buffer = int(decision.quantity or state.baseline_buffer_loads)

        return {
            "target_id": target.id,
            "lane_id": target.lane_id,
            "mode": target.mode,
            "target_date": target.target_date.isoformat() if target.target_date else None,
            "period_type": target.period_type or "WEEK",
            "baseline_buffer_loads": int(state.baseline_buffer_loads),
            "proposed_buffer_loads": proposed_buffer,
            "forecast_loads": int(state.forecast_loads),
            "committed_loads": int(state.committed_loads),
            "recent_tender_reject_rate": state.recent_tender_reject_rate,
            "recent_capacity_miss_count": state.recent_capacity_miss_count,
            "demand_cv": state.demand_cv,
            "demand_trend": state.demand_trend,
            "action": decision.action,
            "action_name": action_name,
            "confidence": decision.confidence,
            "urgency": decision.urgency,
            "reasoning": decision.reasoning,
            "decision_method": "heuristic",
            "scoring_detail": decision.params_used,
        }

    def evaluate_and_log(self, target: CapacityTarget) -> Optional[Dict[str, Any]]:
        """Evaluate + log at severity matching decision urgency. No DB write."""
        result = self.evaluate_target(target)
        if not result:
            return result

        action_name = result["action_name"]
        if action_name == "MODIFY":
            logger.warning(
                "CapacityBuffer MODIFY: target %s (lane=%s, mode=%s) — %s "
                "(baseline=%d → proposed=%d, urg=%.2f)",
                target.id,
                target.lane_id,
                target.mode or "ANY",
                result["reasoning"],
                result["baseline_buffer_loads"],
                result["proposed_buffer_loads"],
                result["urgency"],
            )
        else:
            logger.info(
                "CapacityBuffer %s: target %s (lane=%s, buffer=%d, urg=%.2f)",
                action_name,
                target.id,
                target.lane_id,
                result["baseline_buffer_loads"],
                result["urgency"],
            )

        # PREPARE.3 dual-write to core.agent_decisions
        record_trm_decision(
            self.db,
            tenant_id=self.tenant_id,
            config_id=self.config_id,
            trm_type="capacity_buffer",
            result=result,
            item_code=f"capacity-target-{target.id}",
            item_name=f"lane {target.lane_id} ({target.mode or 'ANY'})",
            category="capacity_buffer",
        )

        return result

    def evaluate_pending_targets(
        self,
        plan_version: str = "live",
        as_of: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """Evaluate every pending CapacityTarget for the tenant's active config."""
        targets = self.find_pending_targets(plan_version=plan_version, as_of=as_of)
        results: List[Dict[str, Any]] = []
        for t in targets:
            r = self.evaluate_and_log(t)
            if r:
                results.append(r)
        return results

    # ── State-builder helpers ────────────────────────────────────────────

    def _build_state(self, target: CapacityTarget):
        """Construct CapacityBufferState from CapacityTarget + history."""
        period_days = _PERIOD_DAYS.get(
            (target.period_type or "WEEK").upper(), self.DEFAULT_PERIOD_DAYS
        )

        # Direct fields from the trigger row
        baseline_buffer = int(target.buffer_loads or 0)
        buffer_policy = (target.buffer_policy or "PCT_FORECAST")
        forecast_loads = int(target.required_loads or 0)
        forecast_p10 = int(target.required_loads_p10 or 0)
        forecast_p90 = int(target.required_loads_p90 or 0)
        committed = int(target.committed_loads or 0)
        contract_cap = int(target.available_loads or 0)
        spot_availability = max(0, contract_cap - committed)

        # Tender reject rate — trailing 14 days on this lane. Joins
        # FreightTender -> Load to filter by lane; tenders without a
        # load assignment are ignored (shipment-level tenders on
        # non-load freight don't have lane context).
        reject_rate = self._compute_tender_reject_rate(target)

        # Capacity-miss count — trailing HISTORY_PERIODS where gap_loads>0
        miss_count = self._count_recent_capacity_misses(target, period_days)

        # Demand CV / trend — from trailing HISTORY_PERIODS of CapacityTargets
        demand_cv, demand_trend = self._compute_demand_stats(target, period_days)

        return self._StateClass(
            lane_id=target.lane_id or 0,
            mode=target.mode or "FTL",
            baseline_buffer_loads=baseline_buffer,
            buffer_policy=buffer_policy,
            forecast_loads=forecast_loads,
            forecast_p10=forecast_p10,
            forecast_p90=forecast_p90,
            committed_loads=committed,
            contract_capacity=contract_cap,
            spot_availability=spot_availability,
            recent_tender_reject_rate=reject_rate,
            recent_capacity_miss_count=miss_count,
            avg_spot_premium_pct=0.0,
            demand_cv=demand_cv,
            demand_trend=demand_trend,
            is_peak_season=False,
        )

    def _compute_tender_reject_rate(self, target: CapacityTarget) -> float:
        """Rolling 14-day share of declined / (declined + accepted) tenders
        on Loads matching this target's lane + mode.

        DECLINED and EXPIRED both count as reject in industry practice
        (FreightWaves OTRI methodology). Tenders with no load (e.g., on
        shipment-level freight) are excluded.
        """
        now = datetime.utcnow()
        window_start = now - timedelta(days=self.TENDER_LOOKBACK_DAYS)

        conditions = [
            FreightTender.tenant_id == self.tenant_id,
            FreightTender.created_at >= window_start,
            FreightTender.load_id.isnot(None),
        ]

        # Join Load for lane matching
        lane_conditions = [Load.tenant_id == self.tenant_id]
        if target.origin_site_id and target.destination_site_id:
            lane_conditions.append(Load.origin_site_id == target.origin_site_id)
            lane_conditions.append(Load.destination_site_id == target.destination_site_id)
        if target.mode:
            lane_conditions.append(
                func.upper(func.cast(Load.mode, str)) == target.mode.upper()
            )

        base_q = (
            select(FreightTender.status, func.count(FreightTender.id))
            .select_from(FreightTender.__table__.join(
                Load.__table__, FreightTender.load_id == Load.id
            ))
            .where(and_(*conditions, *lane_conditions))
            .group_by(FreightTender.status)
        )
        rows = self.db.execute(base_q).all()
        counts = {row[0]: int(row[1]) for row in rows}
        declined = counts.get(TenderStatus.DECLINED, 0) + counts.get(TenderStatus.EXPIRED, 0)
        accepted = counts.get(TenderStatus.ACCEPTED, 0)
        denom = declined + accepted
        return float(declined / denom) if denom > 0 else 0.0

    def _count_recent_capacity_misses(
        self, target: CapacityTarget, period_days: int
    ) -> int:
        """Count trailing-HISTORY_PERIODS CapacityTargets on this lane where
        gap_loads > 0 (required exceeded committed)."""
        if not target.target_date:
            return 0
        lookback_start = target.target_date - timedelta(
            days=period_days * self.HISTORY_PERIODS
        )

        conditions = [
            CapacityTarget.tenant_id == self.tenant_id,
            CapacityTarget.config_id == self.config_id,
            CapacityTarget.lane_id == target.lane_id,
            CapacityTarget.target_date >= lookback_start,
            CapacityTarget.target_date < target.target_date,
            CapacityTarget.gap_loads > 0,
        ]
        if target.mode:
            conditions.append(CapacityTarget.mode == target.mode)

        count = self.db.execute(
            select(func.count(CapacityTarget.id)).where(and_(*conditions))
        ).scalar_one_or_none()
        return int(count or 0)

    def _compute_demand_stats(
        self, target: CapacityTarget, period_days: int
    ) -> tuple[float, float]:
        """Coefficient of variation + normalised trend across the trailing
        HISTORY_PERIODS same-lane CapacityTargets.

        Returns (demand_cv, demand_trend). Both default to 0.0 when
        history is thin (<3 points) — the heuristic handles zero-signal
        cleanly.
        """
        if not target.target_date or not target.lane_id:
            return 0.0, 0.0

        lookback_start = target.target_date - timedelta(
            days=period_days * self.HISTORY_PERIODS
        )

        conditions = [
            CapacityTarget.tenant_id == self.tenant_id,
            CapacityTarget.config_id == self.config_id,
            CapacityTarget.lane_id == target.lane_id,
            CapacityTarget.target_date >= lookback_start,
            CapacityTarget.target_date < target.target_date,
        ]
        if target.mode:
            conditions.append(CapacityTarget.mode == target.mode)

        rows = self.db.execute(
            select(CapacityTarget.target_date, CapacityTarget.required_loads)
            .where(and_(*conditions))
            .order_by(CapacityTarget.target_date.asc())
        ).all()

        values = [float(r[1] or 0.0) for r in rows]
        if len(values) < 3:
            return 0.0, 0.0

        mean = sum(values) / len(values)
        if mean <= 0:
            return 0.0, 0.0

        # Coefficient of variation (sample std / mean)
        variance = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
        cv = math.sqrt(variance) / mean

        # Simple period-over-period trend: last value vs mean of earlier
        # values, normalised by the mean. +0.1 = 10%-above-trend last
        # period.
        earlier = values[:-1]
        if earlier:
            earlier_mean = sum(earlier) / len(earlier)
            trend = (values[-1] - earlier_mean) / max(1.0, earlier_mean)
        else:
            trend = 0.0

        return float(cv), float(trend)
