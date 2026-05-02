"""TacticalForecastService — §3.37 Phase 1.

The L3 Tactical Demand Potential service per ``docs/TMS_DECISION_HIERARCHY.md``
§4.1. Phase 1 ships **heuristic-only aggregation** — the service consumes
``LaneVolumeForecastTRM`` outputs from L1 (per-lane forecasts with
mode/equipment segmentation per §3.36) and writes them to the canonical
``shipping_forecast`` table.

Phase 2 (deferred to a separate register entry) will add the LightGBM +
Trigg tracking quality model that §4.1 also specifies. Phase 1 is enough to
make L1 segmentation outputs visible to downstream TMS / SCP / DP consumers
through the canonical plan-of-record.

Plane-module placement: this is TMS-plane decision policy (the *service*
that decides how / when / what to forecast). The substrate it writes to
(``ShippingForecast`` ORM in Core) is canonical state that any plane can
read.

Service shape:

- ``publish_forecast()`` — synchronous, takes a list of
  ``(lane_id, period_start, LaneVolumeForecastState)`` tuples, runs the L1
  TRM per tuple, and persists per-mode + per-equipment rows.
- Returns the rows written so callers can ack / log / replay.
- Pure orchestration; no scheduled trigger in Phase 1 (provisioning /
  cron wiring lands in Phase 2 alongside the LightGBM model).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from azirella_data_model.transport_plan import (
    DEFAULT_PLAN_VERSION,
    ShippingForecast,
)

from app.services.powell.tms_heuristic_library import (
    LaneVolumeForecastState,
    compute_segmented_loads,
    compute_tms_decision,
)


_NO_SEG_MODE = "ALL"
"""Mode value used when the L1 segmentation helper returns
``"no_segmentation"`` (history unavailable). The aggregate forecast still
lands in ``shipping_forecast`` so consumers see *something*; the
``segmentation_method`` column records the unhappy-path provenance."""


@dataclass(frozen=True)
class LaneForecastInput:
    """One unit of work for the service: a lane × period × state triple."""

    lane_id: int
    period_start: date
    state: LaneVolumeForecastState


@dataclass(frozen=True)
class PublishResult:
    """Per-call summary: rows written + skipped + diagnostics."""

    rows_written: int
    rows_per_lane: dict
    """``{lane_id: count}``."""
    skipped_deferred: int
    """L1 returned DEFER (insufficient history); no row written."""
    rows: List[ShippingForecast]
    """The actual ORM rows persisted (caller may want to inspect)."""


class TacticalForecastService:
    """L3 Tactical Demand Potential service — Phase 1 heuristic aggregation."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def publish_forecast(
        self,
        *,
        tenant_id: int,
        config_id: int,
        inputs: Sequence[LaneForecastInput],
        scenario_id: Optional[int] = None,
        produced_by: str = "TacticalForecastService",
        plan_version: str = DEFAULT_PLAN_VERSION,
    ) -> PublishResult:
        """Compute L1 forecasts per input, write per-mode + per-equipment
        rows to ``shipping_forecast``.

        Returns a :class:`PublishResult` with rows-written + skipped count
        + the actual ORM rows. Caller is responsible for ``commit()``;
        the service ``add()``s and ``flush()``es but does not commit.
        """
        rows: List[ShippingForecast] = []
        per_lane: dict = {}
        skipped_deferred = 0

        for inp in inputs:
            decision = compute_tms_decision("lane_volume_forecast", inp.state)
            params = decision.params_used

            # DEFER means insufficient history; no rows written for this lane.
            # (Action 2 == DEFER per the Actions enum in dispatch.py.)
            if decision.action == 2:
                skipped_deferred += 1
                per_lane[inp.lane_id] = 0
                continue

            new_rows = self._build_rows_for_lane(
                tenant_id=tenant_id,
                config_id=config_id,
                scenario_id=scenario_id,
                lane_input=inp,
                decision_params=params,
                produced_by=produced_by,
                plan_version=plan_version,
            )
            for row in new_rows:
                self.db.add(row)
                rows.append(row)
            per_lane[inp.lane_id] = len(new_rows)

        if rows:
            self.db.flush()

        return PublishResult(
            rows_written=len(rows),
            rows_per_lane=per_lane,
            skipped_deferred=skipped_deferred,
            rows=rows,
        )

    # ------------------------------------------------------------------
    # Row construction
    # ------------------------------------------------------------------

    def _build_rows_for_lane(
        self,
        *,
        tenant_id: int,
        config_id: int,
        scenario_id: Optional[int],
        lane_input: LaneForecastInput,
        decision_params: dict,
        produced_by: str,
        plan_version: str,
    ) -> List[ShippingForecast]:
        """Build ShippingForecast ORM rows for a single lane.

        Three cases driven by ``segmentation_method``:

        - ``no_segmentation``: one row with ``mode='ALL'`` carrying the
          aggregate loads across all three bands.
        - ``single_mode_passthrough``: one row with the dominant mode
          (e.g., FTL) carrying the full aggregate.
        - ``ewma_share_history``: one row per mode in ``mode_mix``;
          additionally one row per equipment within the FTL share when
          ``equipment_mix`` is populated.

        Bands (P10/P50/P90) are split proportionally by mix share — the
        L1 TRM doesn't transform the bands, only routes the action; the
        service applies the share consistently to all three.
        """
        s = lane_input.state
        method = decision_params.get("segmentation_method", "no_segmentation")
        common = self._common_fields(
            tenant_id=tenant_id,
            config_id=config_id,
            scenario_id=scenario_id,
            lane_input=lane_input,
            decision_params=decision_params,
            produced_by=produced_by,
            plan_version=plan_version,
        )

        rows: List[ShippingForecast] = []

        if method == "no_segmentation":
            rows.append(self._row_for_share(
                **common,
                mode=_NO_SEG_MODE,
                equipment_type=None,
                share=1.0,
                base_p10=s.proposed_forecast_p10,
                base_p50=s.proposed_forecast_p50,
                base_p90=s.proposed_forecast_p90,
                weight_kg_p50=decision_params.get("forecast_weight_kg_p50") or None,
                volume_m3_p50=decision_params.get("forecast_volume_m3_p50") or None,
            ))
            return rows

        # Both single_mode_passthrough and ewma_share_history use mode_mix.
        mode_mix = decision_params.get("mode_mix") or {}
        for mode, share in mode_mix.items():
            mode_share = float(share)
            rows.append(self._row_for_share(
                **common,
                mode=mode,
                equipment_type=None,
                share=mode_share,
                base_p10=s.proposed_forecast_p10,
                base_p50=s.proposed_forecast_p50,
                base_p90=s.proposed_forecast_p90,
                # Weight + cube apply to the lane aggregate; carry on every
                # mode-level row scaled by share for consistency with loads.
                weight_kg_p50=self._scaled(
                    decision_params.get("forecast_weight_kg_p50"), mode_share,
                ),
                volume_m3_p50=self._scaled(
                    decision_params.get("forecast_volume_m3_p50"), mode_share,
                ),
            ))

        # Equipment-level rows live inside FTL only.
        equipment_mix = decision_params.get("equipment_mix") or {}
        ftl_share = float(mode_mix.get("FTL", 0.0))
        if equipment_mix and ftl_share > 0:
            for equipment, eq_share in equipment_mix.items():
                effective_share = float(eq_share) * ftl_share
                rows.append(self._row_for_share(
                    **common,
                    mode="FTL",
                    equipment_type=equipment,
                    share=effective_share,
                    base_p10=s.proposed_forecast_p10,
                    base_p50=s.proposed_forecast_p50,
                    base_p90=s.proposed_forecast_p90,
                    weight_kg_p50=self._scaled(
                        decision_params.get("forecast_weight_kg_p50"),
                        effective_share,
                    ),
                    volume_m3_p50=self._scaled(
                        decision_params.get("forecast_volume_m3_p50"),
                        effective_share,
                    ),
                ))

        return rows

    @staticmethod
    def _common_fields(
        *,
        tenant_id: int,
        config_id: int,
        scenario_id: Optional[int],
        lane_input: LaneForecastInput,
        decision_params: dict,
        produced_by: str,
        plan_version: str,
    ) -> dict:
        s = lane_input.state
        return {
            "tenant_id": tenant_id,
            "config_id": config_id,
            "scenario_id": scenario_id,
            "lane_id": lane_input.lane_id,
            "period_start": lane_input.period_start,
            "period_days": s.period_days or 7,
            "service_class": None,  # populated by L4 when that ships
            "forecast_method": decision_params.get("recommended_method")
                or s.proposed_method or None,
            "syntetos_boylan_class": decision_params.get("demand_class") or None,
            "forecast_mape": s.trailing_mape if s.trailing_mape > 0 else None,
            "conformal_coverage_p80": s.conformal_coverage_p80
                if s.conformal_coverage_p80 > 0 else None,
            "segmentation_method": decision_params.get("segmentation_method"),
            "plan_version": plan_version,
            "produced_by": produced_by,
        }

    @staticmethod
    def _row_for_share(
        *,
        mode: str,
        equipment_type: Optional[str],
        share: float,
        base_p10: float,
        base_p50: float,
        base_p90: float,
        weight_kg_p50: Optional[float],
        volume_m3_p50: Optional[float],
        **common: object,
    ) -> ShippingForecast:
        return ShippingForecast(
            mode=mode,
            equipment_type=equipment_type,
            forecast_loads_p10=round(float(base_p10) * share, 4),
            forecast_loads_p50=round(float(base_p50) * share, 4),
            forecast_loads_p90=round(float(base_p90) * share, 4),
            forecast_weight_kg_p50=weight_kg_p50,
            forecast_volume_m3_p50=volume_m3_p50,
            **common,
        )

    @staticmethod
    def _scaled(value: Optional[float], share: float) -> Optional[float]:
        if value is None or value <= 0:
            return None
        return round(float(value) * share, 4)


# Re-export the segmentation helper at this module level too so callers
# constructing scenarios without the L1 TRM can reach the same math without
# pulling in `tms_heuristic_library` directly.
__all__ = [
    "LaneForecastInput",
    "PublishResult",
    "TacticalForecastService",
    "compute_segmented_loads",
]
