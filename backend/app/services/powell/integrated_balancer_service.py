"""IntegratedBalancerService — §3.38 Phase 1 (heuristic stub).

The L3 Constrained Balanced Plan service per
``docs/TMS_DECISION_HIERARCHY.md`` §4.3. Phase 1 ships a **clone-only
stub**: copy the unconstrained `TransportationPlan` to a new plan with
`plan_version='constrained_live'`, preserve all items, no constraint
application yet.

The point of Phase 1 is the data-flow scaffolding:

- The unconstrained plan exists (from `MovementPlannerService`).
- Downstream consumers (L1 TRMs, decision-stream UI) read
  `plan_version='constrained_live'` for the plan-of-record.
- Phase 1 keeps both versions populated so consumers never see an
  empty constrained plan, even though Phase 1's "balancing" is a no-op.

Phase 2 (deferred to a separate register entry) replaces the no-op
clone with the **Integrated Balancer**: GraphSAGE + LP-projection
feasibility repair on:

- Carrier-capacity commitments (contract minimums + spot caps)
- HOS calendar per carrier
- Dock appointment capacity
- Equipment pool state
- BSC weights from L4 θ
- Service-level tier deadlines

Constraint violations in Phase 2 are repaired by:
1. Switching to fallback carrier (next cheapest with capacity)
2. Mode substitution where service-level allows
3. Marking ESCALATE for items that exceed all capacities

Phase 1's clone-only behaviour is honest scaffolding — it does what
its name says and nothing more. Tests verify the data flow; the real
ML / OR work is Phase 2.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from azirella_data_model.transport_plan import (
    PlanStatus,
    TransportationPlan,
    TransportationPlanItem,
)


_GENERATED_BY = "IntegratedBalancerService"


@dataclass(frozen=True)
class BalanceResult:
    """Per-call summary."""

    constrained_plan_id: int
    """The new TransportationPlan header id with
    `plan_version='constrained_live'`."""

    items_cloned: int
    """Total TransportationPlanItem rows cloned from the unconstrained
    source plan."""

    constraints_applied: int = 0
    """Phase 1: always 0. Phase 2 will report how many items had a
    carrier-switch / mode-substitution / escalation applied."""

    items_escalated: int = 0
    """Phase 1: always 0. Phase 2 will report items that exceeded all
    fallback options."""


class IntegratedBalancerService:
    """L3 Constrained Balanced Plan service — Phase 1 clone-only stub."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def balance_plan(
        self,
        *,
        unconstrained_plan_id: int,
        cascade_run_id: Optional[str] = None,
    ) -> BalanceResult:
        """Clone the unconstrained plan to a constrained-live plan.

        Phase 1 behaviour: no constraints applied. Every item from the
        source plan is cloned 1:1 to the new plan. The new plan's
        ``optimization_method`` is set to ``'CLONE_PHASE_1'`` so
        consumers can tell this is the stub vs. the real Phase 2
        Integrated Balancer (which will be ``'GRAPHSAGE_LP_REPAIR'``).

        Returns a :class:`BalanceResult` with the new plan id + clone
        count. Caller commits.
        """
        source_plan = (
            self.db.query(TransportationPlan)
            .filter(TransportationPlan.id == unconstrained_plan_id)
            .first()
        )
        if source_plan is None:
            raise ValueError(
                f"unconstrained_plan_id={unconstrained_plan_id} not found"
            )
        if source_plan.plan_version != "unconstrained_reference":
            raise ValueError(
                f"plan {unconstrained_plan_id} has plan_version="
                f"{source_plan.plan_version!r}; expected "
                "'unconstrained_reference'"
            )

        # Build the constrained_live header.
        constrained_plan = TransportationPlan(
            config_id=source_plan.config_id,
            tenant_id=source_plan.tenant_id,
            plan_version="constrained_live",
            plan_name=(
                source_plan.plan_name.replace(
                    "L3 Unconstrained", "L3 Constrained",
                ) if source_plan.plan_name else "L3 Constrained Plan"
            ),
            status=PlanStatus.DRAFT,
            plan_start_date=source_plan.plan_start_date,
            plan_end_date=source_plan.plan_end_date,
            planning_horizon_days=source_plan.planning_horizon_days,
            optimization_method="CLONE_PHASE_1",
            generated_by="AGENT",
            cascade_run_id=cascade_run_id,
        )
        self.db.add(constrained_plan)
        self.db.flush()  # populate constrained_plan.id

        # Clone every item.
        source_items = (
            self.db.query(TransportationPlanItem)
            .filter(TransportationPlanItem.plan_id == unconstrained_plan_id)
            .all()
        )
        cloned = 0
        for src in source_items:
            cloned_item = TransportationPlanItem(
                plan_id=constrained_plan.id,
                tenant_id=src.tenant_id,
                origin_site_id=src.origin_site_id,
                destination_site_id=src.destination_site_id,
                lane_id=src.lane_id,
                mode=src.mode,
                equipment_type=src.equipment_type,
                carrier_id=src.carrier_id,
                rate_id=src.rate_id,
                status=src.status,
                planned_pickup_date=src.planned_pickup_date,
                planned_delivery_date=src.planned_delivery_date,
                shipment_count=src.shipment_count,
                total_weight=src.total_weight,
                total_volume=src.total_volume,
                total_pallets=src.total_pallets,
                utilization_pct=src.utilization_pct,
                estimated_cost=src.estimated_cost,
                estimated_cost_per_mile=src.estimated_cost_per_mile,
                distance_miles=src.distance_miles,
                stops=src.stops,
                is_multi_stop=src.is_multi_stop,
            )
            self.db.add(cloned_item)
            cloned += 1

        # Mirror the source plan's summary metrics on the clone.
        constrained_plan.total_planned_loads = source_plan.total_planned_loads
        constrained_plan.total_planned_shipments = source_plan.total_planned_shipments
        constrained_plan.total_estimated_cost = source_plan.total_estimated_cost
        constrained_plan.total_estimated_miles = source_plan.total_estimated_miles
        constrained_plan.avg_cost_per_mile = source_plan.avg_cost_per_mile
        constrained_plan.avg_utilization_pct = source_plan.avg_utilization_pct
        constrained_plan.carrier_count = source_plan.carrier_count

        if cloned:
            self.db.flush()

        return BalanceResult(
            constrained_plan_id=constrained_plan.id,
            items_cloned=cloned,
            # Phase 1: constraints not applied.
            constraints_applied=0,
            items_escalated=0,
        )


__all__ = [
    "BalanceResult",
    "IntegratedBalancerService",
]
