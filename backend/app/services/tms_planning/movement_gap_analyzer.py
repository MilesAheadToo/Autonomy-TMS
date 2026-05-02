"""MovementGapAnalyzer — TMS counterpart to SCP's gap_analysis service.

Aggregates `transportation_plan` rows across the three honest
`plan_version` views and reports per-lane and per-equipment-type gaps.
Same response shape as SCP's gap_analysis but keyed by lane/equipment
instead of product/site, because that's where transportation gaps
materialise.

See docs/TACTICAL_PLANNING_REARCHITECTURE.md §10.1 for the contract.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from azirella_data_model.transport_plan import TransportationPlan, TransportationPlanItem
from app.models.tms_entities import TransportMode, EquipmentType

logger = logging.getLogger(__name__)


_VARIANTS = {
    "unconstrained": "unconstrained_reference",
    "constrained": "constrained_live",
    "override": "decision_action",
}


class MovementGapAnalyzer:
    """Build the lane / equipment gap-analysis component for a config."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def analyze(self, config_id: int, top_n: int = 10) -> Dict[str, Any]:
        """Return the full gap-analysis payload for `config_id`.

        Reads every `transportation_plan` for the config across the three
        canonical `plan_version` values, aggregates by lane and by
        equipment_type, computes the gaps, and emits a worst-N summary.
        """
        # Pull every plan + plan-item row for the config in one round trip.
        plan_rows = (
            await self.db.execute(
                select(TransportationPlan).where(
                    TransportationPlan.config_id == config_id,
                )
            )
        ).scalars().all()

        plan_id_to_version: Dict[int, str] = {p.id: p.plan_version for p in plan_rows}

        if not plan_id_to_version:
            return self._empty_payload(config_id)

        items = (
            await self.db.execute(
                select(TransportationPlanItem).where(
                    TransportationPlanItem.plan_id.in_(plan_id_to_version.keys()),
                )
            )
        ).scalars().all()

        # variant_totals[variant_name] = aggregate dict
        variant_totals: Dict[str, Dict[str, float]] = {
            v: dict(loads=0, shipments=0, cost_usd=0.0, miles=0.0, util_sum=0.0, util_count=0)
            for v in _VARIANTS
        }

        # by_lane[lane_id][variant] = aggregate
        # by_equipment[equipment_type][variant] = aggregate
        by_lane: Dict[Optional[int], Dict[str, Dict[str, float]]] = {}
        by_equipment: Dict[Optional[str], Dict[str, Dict[str, float]]] = {}

        for item in items:
            plan_version = plan_id_to_version.get(item.plan_id)
            variant = self._variant_for(plan_version)
            if variant is None:
                continue

            lane_id = self._lane_id_of(item)
            equipment = self._equipment_of(item)
            loads = 1
            shipments = int(getattr(item, "shipment_count", 0) or 0)
            cost = float(getattr(item, "estimated_cost", 0) or 0)
            miles = float(getattr(item, "estimated_miles", 0) or 0)
            util = getattr(item, "expected_utilization_pct", None)

            for bucket_key, by_dict in ((lane_id, by_lane), (equipment, by_equipment)):
                if bucket_key not in by_dict:
                    by_dict[bucket_key] = {
                        v: dict(loads=0, shipments=0, cost_usd=0.0, miles=0.0,
                                util_sum=0.0, util_count=0)
                        for v in _VARIANTS
                    }
                cell = by_dict[bucket_key][variant]
                cell["loads"] += loads
                cell["shipments"] += shipments
                cell["cost_usd"] += cost
                cell["miles"] += miles
                if util is not None:
                    cell["util_sum"] += float(util)
                    cell["util_count"] += 1

            cell = variant_totals[variant]
            cell["loads"] += loads
            cell["shipments"] += shipments
            cell["cost_usd"] += cost
            cell["miles"] += miles
            if util is not None:
                cell["util_sum"] += float(util)
                cell["util_count"] += 1

        # Compose lane rows with gap metrics
        lane_rows = [
            self._compose_row(lane_id, "lane", per_variant)
            for lane_id, per_variant in by_lane.items()
        ]
        equipment_rows = [
            self._compose_row(eq, "equipment", per_variant)
            for eq, per_variant in by_equipment.items()
        ]

        # Worst-N by capacity gap (most negative loads_gap)
        worst_lanes = sorted(
            lane_rows, key=lambda r: r.get("loads_gap", 0)
        )[:top_n]

        return {
            "config_id": config_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "variants": {v: self._finalize_aggregate(cell) for v, cell in variant_totals.items()},
            "lanes": lane_rows,
            "equipment_types": equipment_rows,
            "summary": {
                "worst_capacity_gap_lanes": worst_lanes,
                "total_loads_gap": (
                    variant_totals["constrained"]["loads"]
                    - variant_totals["unconstrained"]["loads"]
                ),
                "total_cost_gap_usd": round(
                    variant_totals["constrained"]["cost_usd"]
                    - variant_totals["unconstrained"]["cost_usd"], 2,
                ),
                "interpretation": self._interpretation(variant_totals),
            },
        }

    # ── internals ───────────────────────────────────────────────────

    @staticmethod
    def _variant_for(plan_version: Optional[str]) -> Optional[str]:
        if plan_version == "unconstrained_reference":
            return "unconstrained"
        if plan_version == "constrained_live":
            return "constrained"
        if plan_version == "decision_action":
            return "override"
        return None

    @staticmethod
    def _lane_id_of(item: TransportationPlanItem) -> Optional[int]:
        # Lane key on plan items varies by schema; tolerate either.
        for attr in ("lane_id", "transportation_lane_id"):
            v = getattr(item, attr, None)
            if v is not None:
                return v
        return None

    @staticmethod
    def _equipment_of(item: TransportationPlanItem) -> Optional[str]:
        v = getattr(item, "equipment_type", None)
        if v is None:
            return None
        # Could be Enum or string
        return getattr(v, "value", str(v))

    @staticmethod
    def _finalize_aggregate(cell: Dict[str, float]) -> Dict[str, Any]:
        avg_util = (cell["util_sum"] / cell["util_count"]) if cell["util_count"] else None
        return {
            "loads": int(cell["loads"]),
            "shipments": int(cell["shipments"]),
            "cost_usd": round(cell["cost_usd"], 2),
            "miles": round(cell["miles"], 1),
            "avg_utilization_pct": round(avg_util, 2) if avg_util is not None else None,
        }

    @staticmethod
    def _interpretation(variant_totals: Dict[str, Dict[str, float]]) -> str:
        gap = variant_totals["constrained"]["loads"] - variant_totals["unconstrained"]["loads"]
        if gap == 0 and variant_totals["unconstrained"]["loads"] > 0:
            return (
                "Constrained plan covers full unconstrained demand — no aggregate "
                "capacity gap."
            )
        if gap < 0:
            return (
                f"Aggregate movement plan is {abs(int(gap))} loads short under "
                "current carrier capacity / equipment / dock availability."
            )
        if gap > 0:
            return (
                f"Constrained plan exceeds unconstrained demand by {int(gap)} "
                "loads — likely staging / repositioning overhead."
            )
        return "No transportation plan rows yet for this config."

    def _compose_row(
        self,
        bucket_key: Any,
        bucket_kind: str,
        per_variant: Dict[str, Dict[str, float]],
    ) -> Dict[str, Any]:
        unc = self._finalize_aggregate(per_variant["unconstrained"])
        con = self._finalize_aggregate(per_variant["constrained"])
        ovr = self._finalize_aggregate(per_variant["override"])

        loads_gap = con["loads"] - unc["loads"]
        cost_gap = round(con["cost_usd"] - unc["cost_usd"], 2)
        util_gap = None
        if con["avg_utilization_pct"] is not None and unc["avg_utilization_pct"] is not None:
            util_gap = round(con["avg_utilization_pct"] - unc["avg_utilization_pct"], 2)

        return {
            f"{bucket_kind}_id" if bucket_kind == "lane" else "equipment_type": bucket_key,
            "label": str(bucket_key) if bucket_key is not None else "(unassigned)",
            "unconstrained": unc,
            "constrained": con,
            "override": ovr,
            "loads_gap": loads_gap,
            "cost_gap_usd": cost_gap,
            "utilisation_gap_pct": util_gap,
        }

    def _empty_payload(self, config_id: int) -> Dict[str, Any]:
        empty = {"loads": 0, "shipments": 0, "cost_usd": 0.0, "miles": 0.0, "avg_utilization_pct": None}
        return {
            "config_id": config_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "variants": {v: dict(empty) for v in _VARIANTS},
            "lanes": [],
            "equipment_types": [],
            "summary": {
                "worst_capacity_gap_lanes": [],
                "total_loads_gap": 0,
                "total_cost_gap_usd": 0.0,
                "interpretation": "No transportation_plan rows yet for this config.",
            },
        }
