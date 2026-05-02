"""MovementPlannerService — §3.38 Phase 1 (heuristic).

The L3 Unconstrained Movement Plan service per
``docs/TMS_DECISION_HIERARCHY.md`` §4.2. Phase 1 ships **heuristic-only
fan-out**: read `LaneVolumePlan` rows (the §3.37 L3 Demand Potential
output), and produce `TransportationPlan` + `TransportationPlanItem`
rows with `plan_version='unconstrained_reference'` — one item per
forecast load, dispatch dates distributed across the period.

Phase 1 deliberately produces a "skeleton" plan:

- Carrier assignment is `NULL`. The GraphSAGE Movement Planner that
  §4.2 specifies (transport-graph node features = lanes + hubs; edge
  features = mode alternatives + rate-card + transit time) is Phase 2.
  Phase 1's job is the data flow: read forecasts, write plan items,
  prove the L3-tier plumbing.
- No rate-card lookup. Cost / distance fields default to `NULL`.
- No multi-stop optimisation. Each forecast load becomes one direct
  origin-to-destination plan item.
- No mode optimisation. Each `LaneVolumePlan` row's mode is preserved
  on its plan items (no re-routing FTL → LTL even if cheaper).

Phase 2 will replace the heuristic with the GraphSAGE planner that:
- Optimises mode + carrier per (lane, period) given rate-card + transit
- Re-distributes loads across the week per service-level deadlines
- Considers multi-stop consolidations
- Outputs cost / distance / utilisation estimates

Plane-module placement: this is TMS-plane decision policy. The substrate
it reads (`LaneVolumePlan`) and writes (`TransportationPlan` +
`TransportationPlanItem`) is canonical state.

Schema location footnote: `TransportationPlan` + `TransportationPlanItem`
currently live in TMS at ``app/models/tms_planning.py``. CLAUDE.md says
`plan_*` tables belong in Core; moving them is tracked as deferred
debt in §3.38 register entry's "What is NOT in this entry" section.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from azirella_data_model.transport_plan import (
    DEFAULT_PLAN_VERSION,
    LaneVolumePlan,
)

from app.models.tms_planning import (
    PlanItemStatus,
    PlanStatus,
    TransportationPlan,
    TransportationPlanItem,
)


_PLAN_NAME_PATTERN = "L3 Unconstrained Movement Plan {period_start:%Y-%m-%d}"
_GENERATED_BY = "MovementPlannerService"


def _is_leaf_row(row: LaneVolumePlan, all_rows: Sequence[LaneVolumePlan]) -> bool:
    """True if `row` is a leaf in the segmentation tree.

    Skip the FTL parent row when its equipment-children rows exist for
    the same `(lane_id, period_start)`. Mode-level non-FTL rows are
    always leaves. The `mode='ALL'` no-segmentation row is always a
    leaf.
    """
    if row.mode == "FTL" and row.equipment_type is None:
        # Parent row only counts as a leaf if no equipment-children exist.
        has_children = any(
            other for other in all_rows
            if other.lane_id == row.lane_id
            and other.period_start == row.period_start
            and other.mode == "FTL"
            and other.equipment_type is not None
        )
        return not has_children
    return True


@dataclass(frozen=True)
class PlanResult:
    """Per-call summary."""

    plan_id: int
    """The TransportationPlan header id."""

    items_written: int
    """Total TransportationPlanItem rows written."""

    items_per_lane: dict = field(default_factory=dict)
    """``{lane_id: count}``."""

    skipped_zero_loads: int = 0
    """LaneVolumePlan rows with `forecast_loads_p50 < 0.5` (round → 0)
    skipped — no plan item to create."""


class MovementPlannerService:
    """L3 Unconstrained Movement Plan service — Phase 1 heuristic."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def plan_movement(
        self,
        *,
        tenant_id: int,
        config_id: int,
        period_start: date,
        period_days: int = 7,
        scenario_id: Optional[int] = None,
        forecast_plan_version: str = DEFAULT_PLAN_VERSION,
        cascade_run_id: Optional[str] = None,
    ) -> PlanResult:
        """Read LaneVolumePlan rows for the given (tenant, config,
        period_start) and produce a TransportationPlan with items.

        Phase 1 heuristic: one plan item per round(forecast_loads_p50)
        load, pickup dates distributed evenly across the period. Carrier,
        cost, distance fields are NULL — Phase 2 fills these via
        GraphSAGE + rate-card lookup.

        Returns a :class:`PlanResult` with the plan id + item counts.
        Caller is responsible for `commit()`.
        """
        # 1. Fetch the L3 Demand Potential rows for this period.
        forecast_rows = (
            self.db.query(LaneVolumePlan)
            .filter(
                LaneVolumePlan.tenant_id == tenant_id,
                LaneVolumePlan.config_id == config_id,
                LaneVolumePlan.scenario_id == scenario_id,
                LaneVolumePlan.period_start == period_start,
                LaneVolumePlan.plan_version == forecast_plan_version,
            )
            .all()
        )

        # 2. Build the TransportationPlan header.
        plan = TransportationPlan(
            config_id=config_id,
            tenant_id=tenant_id,
            plan_version="unconstrained_reference",
            plan_name=_PLAN_NAME_PATTERN.format(period_start=period_start),
            status=PlanStatus.DRAFT,
            plan_start_date=period_start,
            plan_end_date=period_start + timedelta(days=period_days - 1),
            planning_horizon_days=period_days,
            optimization_method="HEURISTIC_PHASE_1",
            generated_by="AGENT",
            cascade_run_id=cascade_run_id,
        )
        self.db.add(plan)
        self.db.flush()  # populate plan.id

        # 3. Fan out leaf rows into plan items.
        items_per_lane: dict = {}
        items_written = 0
        skipped_zero_loads = 0

        for row in forecast_rows:
            if not _is_leaf_row(row, forecast_rows):
                continue

            n_items = round(row.forecast_loads_p50)
            if n_items <= 0:
                skipped_zero_loads += 1
                continue

            origin_id, destination_id = self._lane_endpoints(row.lane_id)
            for i in range(n_items):
                pickup_dt = self._distribute_pickup(
                    period_start=period_start,
                    period_days=period_days,
                    item_index=i,
                    total_items=n_items,
                )
                # Phase 1 default delivery: pickup + 1 day (placeholder).
                # Phase 2 reads transit-time distribution from lane
                # metadata.
                delivery_dt = pickup_dt + timedelta(days=1)

                item = TransportationPlanItem(
                    plan_id=plan.id,
                    tenant_id=tenant_id,
                    origin_site_id=origin_id,
                    destination_site_id=destination_id,
                    lane_id=row.lane_id,
                    mode=row.mode,
                    equipment_type=row.equipment_type,
                    carrier_id=None,
                    rate_id=None,
                    status=PlanItemStatus.PLANNED,
                    planned_pickup_date=pickup_dt,
                    planned_delivery_date=delivery_dt,
                    shipment_count=1,
                    total_weight=self._derive_weight(row, n_items),
                    total_volume=self._derive_volume(row, n_items),
                    estimated_cost=None,
                    distance_miles=None,
                    is_multi_stop=False,
                )
                self.db.add(item)
                items_written += 1
                items_per_lane[row.lane_id] = items_per_lane.get(row.lane_id, 0) + 1

        # 4. Update plan-header summary metrics.
        plan.total_planned_loads = items_written
        plan.total_planned_shipments = items_written

        if items_written:
            self.db.flush()

        return PlanResult(
            plan_id=plan.id,
            items_written=items_written,
            items_per_lane=items_per_lane,
            skipped_zero_loads=skipped_zero_loads,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _lane_endpoints(self, lane_id: int) -> tuple:
        """Return (origin_site_id, destination_site_id) for the lane.

        Phase 1: looks up `transportation_lane.origin_id` /
        `destination_id`. If the lookup fails (e.g., FK target absent
        in test fixtures), returns (lane_id, lane_id) as a placeholder
        — keeps the plan items insertable without the lane row.
        """
        try:
            from azirella_data_model.master.config import TransportationLane
            lane = self.db.query(TransportationLane).filter_by(id=lane_id).first()
            if lane is None:
                return (lane_id, lane_id)
            origin_id = getattr(lane, "origin_site_id", None) or getattr(lane, "origin_id", None) or lane_id
            destination_id = getattr(lane, "destination_site_id", None) or getattr(lane, "destination_id", None) or lane_id
            return (origin_id, destination_id)
        except Exception:
            return (lane_id, lane_id)

    @staticmethod
    def _distribute_pickup(
        *,
        period_start: date,
        period_days: int,
        item_index: int,
        total_items: int,
    ) -> datetime:
        """Distribute a pickup across the period evenly.

        For total_items=N, item i goes at period_start + (i + 0.5) / N
        of period_days. Half-step centers items inside the period
        (so 1 item lands at the midpoint, not the start).
        """
        if total_items <= 1:
            offset_days = period_days / 2.0
        else:
            offset_days = ((item_index + 0.5) / total_items) * period_days
        return datetime.combine(period_start, datetime.min.time()) + timedelta(days=offset_days)

    @staticmethod
    def _derive_weight(row: LaneVolumePlan, n_items: int) -> Optional[float]:
        if not row.forecast_weight_kg_p50 or n_items <= 0:
            return None
        return round(float(row.forecast_weight_kg_p50) / n_items, 2)

    @staticmethod
    def _derive_volume(row: LaneVolumePlan, n_items: int) -> Optional[float]:
        if not row.forecast_volume_m3_p50 or n_items <= 0:
            return None
        return round(float(row.forecast_volume_m3_p50) / n_items, 2)


__all__ = [
    "MovementPlannerService",
    "PlanResult",
]
