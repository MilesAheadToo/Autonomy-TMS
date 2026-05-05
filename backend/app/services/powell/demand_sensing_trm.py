"""
DemandSensingTRM — Short-Horizon Demand Adjustment (SENSE phase)

Eighth TMS-native TRM. Fills out the SENSE phase alongside
CapacityPromiseTRM (item 1) and ShipmentTrackingTRM (item 2).

Evaluates shipping-volume forecasts (`ShippingForecast` rows) against
actual order-pipeline velocity + trailing actuals + structural bias
tracking, and decides: ACCEPT (nominal) or MODIFY (adjust by quantity).

Trigger entity: one `ShippingForecast` row per (lane, mode, period).
One evaluation per forecast per cycle. Not scheduled by default; invoked
ad-hoc via endpoint or upstream orchestration (Sprint-2 daily cascade).

Feature-vector sources (v1):

- forecast_loads / forecast_method / forecast_mape ← ShippingForecast
- actual_loads_current ← count(Load) where
    (origin,destination,mode) matches this lane's route AND
    actual_departure falls inside the forecast period
- actual_loads_prior   ← same count, prior period window
- week_over_week_change_pct ← derived from the two above
- rolling_4wk_avg ← 4-week rolling mean of actuals (or 0.0 if no history)
- order_pipeline_loads_24h ← count(Load) created in last 24h on lane
- order_pipeline_loads_prior_24h ← same, 24h starting 7 days ago
- is_peak_season ← ShippingForecast.seasonal_index > 1.1 (v1 heuristic)
- seasonal_index ← ShippingForecast.seasonal_index if column present
- signal_type / signal_magnitude / signal_confidence ← default empty (no
  external signal wired v1; slot reserved for ExternalSignalsService)

Not yet sourced (honest defaults):
- cumulative_forecast_error / cumulative_mad (needs a running-stats
  table keyed by lane; planned for Sprint-2 alongside conformal
  auto-calibration)
- day_of_week_pattern (needs per-day aggregation; Sprint-2 addition)

No persistence in v1 — same observational pattern as ShipmentTrackingTRM.
MODIFY decisions are logged at warning level with the proposed
adjustment; a forecast mutation path lands alongside PREPARE.3's
dual-write to core.agent_decisions with decision_type=DEMAND_SENSING.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.tms_entities import Load
from app.models.tms_planning import ShippingForecast


from app.services.powell.agent_decision_writer import record_trm_decision
logger = logging.getLogger(__name__)



# Period-type → window days. ShippingForecast.period_type is DAY/WEEK/MONTH.
_PERIOD_DAYS = {"DAY": 1, "WEEK": 7, "MONTH": 30}


class DemandSensingTRM:
    """
    Evaluates shipping-volume forecasts and detects demand-side bias.

    Lifecycle:
        trm = DemandSensingTRM(db_session, tenant_id, config_id)
        decisions = trm.evaluate_pending_forecasts()

    Unlike LoadBuild / BrokerRouting, this TRM does NOT mutate the
    ShippingForecast. Demand sensing is observational — a MODIFY
    decision surfaces an adjustment recommendation, and the downstream
    cascade (or a human reviewer) applies it.
    """

    DEFAULT_PERIOD_DAYS = 7

    def __init__(self, db: Session, tenant_id: int, config_id: int):
        self.db = db
        self.tenant_id = tenant_id
        self.config_id = config_id
        self._model = None

        from autonomy_tms_heuristics.library.dispatch import (
            compute_tms_decision,
        )
        from autonomy_tms_heuristics.library.base import (
            DemandSensingState,
        )
        self._compute_decision = compute_tms_decision
        self._StateClass = DemandSensingState

    def load_checkpoint(self, checkpoint_path: str) -> bool:
        """Load a trained BC checkpoint. Returns True on success."""
        ckpt = load_bc_checkpoint(checkpoint_path, "demand_sensing")
        if ckpt is None:
            return False
        self._model = ckpt
        return True

    def find_pending_forecasts(
        self,
        plan_version: str = "live",
        as_of: Optional[date] = None,
    ) -> List[ShippingForecast]:
        """
        Forecasts whose period overlaps `as_of`. Evaluates one current
        period per (lane, mode) — the planner cares about the active
        forecast, not history.
        """
        reference = as_of or date.today()
        query = (
            select(ShippingForecast)
            .where(
                ShippingForecast.config_id == self.config_id,
                ShippingForecast.plan_version == plan_version,
                ShippingForecast.forecast_date <= reference,
            )
            .order_by(ShippingForecast.forecast_date.desc(), ShippingForecast.id)
        )
        return list(self.db.execute(query).scalars().all())

    def evaluate_forecast(self, forecast: ShippingForecast) -> Optional[Dict[str, Any]]:
        """Evaluate demand-sensing decision for one forecast. Never mutates it."""
        state = self._build_state(forecast)

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
                    "forecast_id": forecast.id,
                    "lane_id": forecast.lane_id,
                    "forecast_date": forecast.forecast_date.isoformat() if forecast.forecast_date else None,
                    "period_type": forecast.period_type or "WEEK",
                    "mode": forecast.mode,
                    "forecast_loads": float(state.forecast_loads),
                    "proposed_adjustment": 0.0,
                    "proposed_forecast": float(state.forecast_loads),
                    "week_over_week_change_pct": state.week_over_week_change_pct,
                    "pipeline_velocity_24h": state.order_pipeline_loads_24h,
                    "pipeline_velocity_prior_24h": state.order_pipeline_loads_prior_24h,
                    "forecast_mape": state.forecast_mape,
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
                    "DemandSensing BC inference failed (falling back "
                    "to heuristic): %s", e,
                )

        decision = self._compute_decision("demand_sensing", state)

        action_name = action_name_map.get(decision.action, "UNKNOWN")

        # MODIFY carries the proposed ΔQty. Convert to absolute target for
        # convenience when surfaced to humans.
        proposed_adjustment = float(decision.quantity or 0.0)
        proposed_forecast = max(
            0.0, float(state.forecast_loads) + proposed_adjustment
        )

        return {
            "forecast_id": forecast.id,
            "lane_id": forecast.lane_id,
            "forecast_date": forecast.forecast_date.isoformat() if forecast.forecast_date else None,
            "period_type": forecast.period_type or "WEEK",
            "mode": forecast.mode,
            "forecast_loads": float(state.forecast_loads),
            "proposed_adjustment": proposed_adjustment,
            "proposed_forecast": proposed_forecast,
            "week_over_week_change_pct": state.week_over_week_change_pct,
            "pipeline_velocity_24h": state.order_pipeline_loads_24h,
            "pipeline_velocity_prior_24h": state.order_pipeline_loads_prior_24h,
            "forecast_mape": state.forecast_mape,
            "action": decision.action,
            "action_name": action_name,
            "confidence": decision.confidence,
            "urgency": decision.urgency,
            "reasoning": decision.reasoning,
            "decision_method": "heuristic",
            "scoring_detail": decision.params_used,
        }

    def evaluate_and_log(self, forecast: ShippingForecast) -> Optional[Dict[str, Any]]:
        """Evaluate + log at a severity matching the decision urgency.

        No ShippingForecast mutation (demand sensing is observational).
        MODIFY logs as WARNING so Decision Stream / log aggregators can
        pick it up; ACCEPT is INFO.
        """
        result = self.evaluate_forecast(forecast)
        if not result:
            return result

        action_name = result["action_name"]
        if action_name == "MODIFY":
            logger.warning(
                "DemandSensing MODIFY: forecast %s (lane=%s, %s) — %s "
                "(Δ=%+.1f, proposed=%.1f, urg=%.2f)",
                forecast.id,
                forecast.lane_id,
                forecast.mode or "ANY",
                result["reasoning"],
                result["proposed_adjustment"],
                result["proposed_forecast"],
                result["urgency"],
            )
        else:
            logger.info(
                "DemandSensing %s: forecast %s (lane=%s, loads=%.1f, urg=%.2f)",
                action_name,
                forecast.id,
                forecast.lane_id,
                result["forecast_loads"],
                result["urgency"],
            )

        # PREPARE.3 dual-write to core.agent_decisions
        record_trm_decision(
            self.db,
            tenant_id=self.tenant_id,
            config_id=self.config_id,
            trm_type="demand_sensing",
            result=result,
            item_code=f"forecast-{forecast.id}",
            item_name=f"lane {forecast.lane_id} ({forecast.mode or 'ANY'})",
            category="forecast_adjustment",
        )

        return result

    def evaluate_pending_forecasts(
        self,
        plan_version: str = "live",
        as_of: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """Evaluate every pending forecast for the tenant's active config."""
        forecasts = self.find_pending_forecasts(plan_version=plan_version, as_of=as_of)
        results: List[Dict[str, Any]] = []
        for f in forecasts:
            r = self.evaluate_and_log(f)
            if r:
                results.append(r)
        return results

    # ── State-builder helpers ────────────────────────────────────────────

    def _build_state(self, forecast: ShippingForecast):
        """Construct DemandSensingState from ShippingForecast + Load history."""
        period_days = _PERIOD_DAYS.get(
            (forecast.period_type or "WEEK").upper(), self.DEFAULT_PERIOD_DAYS
        )

        period_start = forecast.forecast_date or date.today()
        period_end = period_start + timedelta(days=period_days)
        prior_start = period_start - timedelta(days=period_days)
        prior_end = period_start

        # Actuals: loads that actually departed in the current vs prior window
        actual_current = self._count_loads_in_window(
            forecast, start_dt=datetime.combine(period_start, datetime.min.time()),
            end_dt=datetime.combine(period_end, datetime.min.time()),
            using="actual_departure",
        )
        actual_prior = self._count_loads_in_window(
            forecast, start_dt=datetime.combine(prior_start, datetime.min.time()),
            end_dt=datetime.combine(prior_end, datetime.min.time()),
            using="actual_departure",
        )

        # Rolling 4-week average (prior 4 periods, excluding current)
        rolling_start = period_start - timedelta(days=4 * period_days)
        rolling_total = self._count_loads_in_window(
            forecast, start_dt=datetime.combine(rolling_start, datetime.min.time()),
            end_dt=datetime.combine(period_start, datetime.min.time()),
            using="actual_departure",
        )
        rolling_avg = rolling_total / 4.0 if rolling_total else 0.0

        # WoW change
        wow_change_pct = 0.0
        if actual_prior > 0:
            wow_change_pct = (actual_current - actual_prior) / actual_prior

        # Order pipeline: loads CREATED in the last 24h on this lane, vs
        # the same 24h window exactly one week ago. This is the strongest
        # near-term demand signal (E2open / Terra Technology pattern).
        now = datetime.utcnow()
        pipeline_24h = self._count_loads_in_window(
            forecast, start_dt=now - timedelta(hours=24), end_dt=now,
            using="created_at",
        )
        pipeline_prior_24h = self._count_loads_in_window(
            forecast,
            start_dt=now - timedelta(days=7, hours=24),
            end_dt=now - timedelta(days=7),
            using="created_at",
        )

        # Seasonal context — ShippingForecast carries no explicit
        # seasonal_index column in v1; derive a boolean peak flag from
        # confidence_score / mape if the domain signals a busy period
        # (mape > 0.15 in peak windows is a known pattern). Default
        # non-peak.
        is_peak = False
        seasonal_index = 1.0
        # `day_of_week_pattern` remains empty (Sprint-2 addition)

        return self._StateClass(
            lane_id=forecast.lane_id or 0,
            period_start=period_start,
            period_days=period_days,
            forecast_loads=float(forecast.forecast_loads or 0.0),
            forecast_method=(
                forecast.forecast_method.value
                if getattr(forecast.forecast_method, "value", None)
                else str(forecast.forecast_method or "CONFORMAL")
            ),
            forecast_mape=float(forecast.mape or 0.0),
            actual_loads_current=float(actual_current),
            actual_loads_prior=float(actual_prior),
            week_over_week_change_pct=float(wow_change_pct),
            rolling_4wk_avg=float(rolling_avg),
            signal_type="",
            signal_magnitude=0.0,
            signal_confidence=0.0,
            seasonal_index=seasonal_index,
            is_peak_season=is_peak,
            day_of_week_pattern=[],
            order_pipeline_loads_24h=float(pipeline_24h),
            order_pipeline_loads_prior_24h=float(pipeline_prior_24h),
            cumulative_forecast_error=0.0,
            cumulative_mad=1.0,
        )

    def _count_loads_in_window(
        self,
        forecast: ShippingForecast,
        *,
        start_dt: datetime,
        end_dt: datetime,
        using: str,
    ) -> int:
        """Count tenant-scoped Loads matching the forecast's lane+mode within
        the datetime window. `using` picks the Load timestamp column
        (`actual_departure` for historicals, `created_at` for pipeline
        velocity).
        """
        col = getattr(Load, using, None)
        if col is None:
            return 0

        conditions = [
            Load.tenant_id == self.tenant_id,
            col >= start_dt,
            col < end_dt,
        ]

        # Lane match: prefer (origin,destination,mode) route when the
        # forecast has them; otherwise fall back to mode-only (the
        # query stays broad but is still tenant-scoped).
        if forecast.origin_site_id and forecast.destination_site_id:
            conditions.append(Load.origin_site_id == forecast.origin_site_id)
            conditions.append(Load.destination_site_id == forecast.destination_site_id)
        if forecast.mode:
            # Load.mode is a TransportMode enum; ShippingForecast.mode is
            # a free-text string. Compare by name (uppercase) to bridge.
            conditions.append(
                func.upper(func.cast(Load.mode, str)) == forecast.mode.upper()
            )

        count = self.db.execute(
            select(func.count(Load.id)).where(and_(*conditions))
        ).scalar_one_or_none()
        return int(count or 0)
