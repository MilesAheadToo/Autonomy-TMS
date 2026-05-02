"""IntegratedBalancerService — §3.38 Phase 2B (LP-projection capacity enforcement).

The L3 Constrained Balanced Plan service per
``docs/TMS_DECISION_HIERARCHY.md`` §4.3. Phase 2B implements **transportation-
style LP-projection capacity enforcement**: starting from the unconstrained
plan, redistribute carrier assignments so that no carrier exceeds its
per-period commit volume.

## Algorithm — LP-projection (Phase 2B)

Given:
- N plan items from the unconstrained plan, each with a Phase 2A-assigned
  carrier and `estimated_cost`.
- C eligible carriers (the union of carriers across all items + any
  fallback carriers reachable through their tenant's contracts).
- Per-carrier capacity from `CarrierCapacityCommitment` rows (Phase 2B
  introduces this concept — see "Capacity model" below).

Decision variables `x[i,c] ∈ [0, 1]`: should item `i` be assigned to
carrier `c`?

Constraints:
- `sum_c x[i,c] + e[i] = 1` per item — assigned to exactly one carrier
  or escalated (slack ``e[i]``)
- `sum_i x[i,c] ≤ capacity[c]` per carrier — capacity respected

Objective: ``minimize sum(cost[i,c] × x[i,c]) + ESCALATION_PENALTY × sum(e[i])``

Solved via ``scipy.optimize.linprog(method='highs')``. The LP relaxation
of integer-valued ``x[i,c]`` is acceptable for this transportation-style
problem (totally-unimodular constraint matrix; vertex solutions are
integer-feasible). Fractional solutions are rounded by argmax.

## Capacity model — Phase 2B simplification

Full carrier-capacity commitments are a future ``carrier_capacity_commitment``
table (§3.40 follow-on). For Phase 2B, capacity is passed via
``balance_plan(carrier_capacity={carrier_id: max_loads})``.

When ``carrier_capacity`` is omitted, the balancer falls back to the
**clone-only Phase 1 stub** (``optimization_method='CLONE_PHASE_1'``).
This preserves backward compatibility with callers that haven't yet
wired up capacity data.

## What's NOT in Phase 2B

- HOS calendar enforcement (driver-level constraint; needs
  ``Driver.available_hours_remaining_h`` integration)
- Dock appointment slot capacity (needs yard-side dock-slot table)
- Equipment pool state (needs fleet-asset availability tracking)
- Contract-minimum guarantees (track committed-vs-spent volumes)
- BSC weighted re-optimisation (Phase 3 — uses L4 θ)
- Re-resolving rate cards on fallback carriers (Phase 2B uses a 50%
  surcharge approximation for items reassigned away from their Phase
  2A-picked carrier)

These are GraphSAGE / LP-extension concerns deferred to Phase 3 per
``MIGRATION_REGISTER.md`` §3.38.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from sqlalchemy.orm import Session

from app.models.tms_planning import (
    PlanStatus,
    PlanItemStatus,
    TransportationPlan,
    TransportationPlanItem,
)


_GENERATED_BY = "IntegratedBalancerService"

_DEFAULT_ESCALATION_PENALTY = 1_000_000.0
"""Per-item penalty when LP can't fit an item into any carrier's capacity.
Large enough that escalation is always worse than any feasible
assignment. Configurable per-call for tenants with unusual cost scales."""


@dataclass(frozen=True)
class BalanceResult:
    """Per-call summary."""

    constrained_plan_id: int
    """The new TransportationPlan header id with
    `plan_version='constrained_live'`."""

    items_cloned: int
    """Total `TransportationPlanItem` rows in the constrained plan."""

    constraints_applied: int = 0
    """Phase 1: 0. Phase 2B: number of items whose carrier assignment
    changed by LP-projection (Phase 2A's pick exceeded capacity, so the
    LP reassigned to a fallback carrier)."""

    items_escalated: int = 0
    """Phase 1: 0. Phase 2B: items the LP couldn't fit into any
    carrier's capacity. Status set to ``CANCELLED``; downstream consumers
    re-tender at spot rates."""

    optimization_method: str = "CLONE_PHASE_1"
    """``CLONE_PHASE_1`` (no capacity), ``LP_PROJECTION_PHASE_2B``
    (capacity enforced via LP), or ``LP_INFEASIBLE_FALLBACK`` (LP failed;
    fell back to clone)."""

    capacity_utilization_per_carrier: Dict[int, float] = field(default_factory=dict)
    """Phase 2B post-LP utilisation per carrier
    (``loads_assigned / capacity``). Phase 1: empty."""


class IntegratedBalancerService:
    """L3 Constrained Balanced Plan service.

    - Phase 1 (clone-only): omit ``carrier_capacity`` → 1:1 clone of
      unconstrained plan.
    - Phase 2B (LP-projection): pass ``carrier_capacity={carrier_id:
      max_loads}`` → solve a transportation LP that respects per-carrier
      capacity, minimises total cost, escalates items that don't fit.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def balance_plan(
        self,
        *,
        unconstrained_plan_id: int,
        carrier_capacity: Optional[Dict[int, float]] = None,
        cascade_run_id: Optional[str] = None,
        escalation_penalty: float = _DEFAULT_ESCALATION_PENALTY,
    ) -> BalanceResult:
        """Project the unconstrained plan onto the carrier-capacity
        constraint set.

        - ``carrier_capacity is None`` → Phase 1 clone-only stub
          (``optimization_method='CLONE_PHASE_1'``).
        - ``carrier_capacity`` provided → Phase 2B LP-projection.
        - LP solve fails → fall back to clone with
          ``optimization_method='LP_INFEASIBLE_FALLBACK'``.

        Caller commits.
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

        source_items = (
            self.db.query(TransportationPlanItem)
            .filter(TransportationPlanItem.plan_id == unconstrained_plan_id)
            .all()
        )

        # Phase 1 fallback: no capacity → clone-only stub.
        if carrier_capacity is None:
            return self._clone_only(
                source_plan=source_plan,
                source_items=source_items,
                cascade_run_id=cascade_run_id,
                optimization_method="CLONE_PHASE_1",
            )

        # Phase 2B LP-projection.
        try:
            assignments, escalated, utilisation = self._solve_lp_projection(
                items=source_items,
                carrier_capacity=carrier_capacity,
                escalation_penalty=escalation_penalty,
            )
        except (RuntimeError, ValueError):
            # LP infeasible / numerical failure → fall back to clone with
            # an explicit marker so consumers can detect the degraded path.
            result = self._clone_only(
                source_plan=source_plan,
                source_items=source_items,
                cascade_run_id=cascade_run_id,
                optimization_method="LP_INFEASIBLE_FALLBACK",
            )
            return BalanceResult(
                constrained_plan_id=result.constrained_plan_id,
                items_cloned=result.items_cloned,
                constraints_applied=0,
                items_escalated=0,
                optimization_method="LP_INFEASIBLE_FALLBACK",
                capacity_utilization_per_carrier={},
            )

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
            optimization_method="LP_PROJECTION_PHASE_2B",
            generated_by="AGENT",
            cascade_run_id=cascade_run_id,
        )
        self.db.add(constrained_plan)
        self.db.flush()

        constraints_applied = 0
        items_escalated = 0
        cloned = 0
        total_cost = 0.0

        for src in source_items:
            new_carrier_id = assignments.get(src.id, src.carrier_id)
            if (
                new_carrier_id != src.carrier_id
                and src.carrier_id is not None
                and src.id not in escalated
            ):
                constraints_applied += 1

            if src.id in escalated:
                items_escalated += 1
                status = PlanItemStatus.CANCELLED
                new_carrier_id = None
                cost = None
                cost_per_mile = None
                rate_id = None
            else:
                status = src.status
                # When LP reassigned to a different carrier, the original
                # rate_id no longer applies. Phase 3 will re-resolve via
                # the Phase 2A rate-card lookup; Phase 2B leaves rate_id
                # NULL on reassigned items.
                if new_carrier_id == src.carrier_id:
                    rate_id = src.rate_id
                    cost = src.estimated_cost
                    cost_per_mile = src.estimated_cost_per_mile
                else:
                    rate_id = None
                    # Phase 2B simplification: 50% surcharge on fallback
                    # carriers (mirrors the LP cost model).
                    cost = (
                        round(src.estimated_cost * 1.5, 2)
                        if src.estimated_cost is not None else None
                    )
                    cost_per_mile = (
                        round(src.estimated_cost_per_mile * 1.5, 4)
                        if src.estimated_cost_per_mile is not None else None
                    )

            cloned_item = TransportationPlanItem(
                plan_id=constrained_plan.id,
                tenant_id=src.tenant_id,
                origin_site_id=src.origin_site_id,
                destination_site_id=src.destination_site_id,
                lane_id=src.lane_id,
                mode=src.mode,
                equipment_type=src.equipment_type,
                carrier_id=new_carrier_id,
                rate_id=rate_id,
                status=status,
                planned_pickup_date=src.planned_pickup_date,
                planned_delivery_date=src.planned_delivery_date,
                shipment_count=src.shipment_count,
                total_weight=src.total_weight,
                total_volume=src.total_volume,
                total_pallets=src.total_pallets,
                utilization_pct=src.utilization_pct,
                estimated_cost=cost,
                estimated_cost_per_mile=cost_per_mile,
                distance_miles=src.distance_miles,
                stops=src.stops,
                is_multi_stop=src.is_multi_stop,
            )
            self.db.add(cloned_item)
            cloned += 1
            if cost is not None:
                total_cost += cost

        # Summary metrics
        constrained_plan.total_planned_loads = cloned
        constrained_plan.total_planned_shipments = cloned
        if total_cost > 0:
            constrained_plan.total_estimated_cost = round(total_cost, 2)
        if source_plan.total_estimated_miles and cloned > 0:
            ratio = (cloned - items_escalated) / cloned
            constrained_plan.total_estimated_miles = round(
                source_plan.total_estimated_miles * ratio, 2,
            )
        if (
            constrained_plan.total_estimated_cost
            and constrained_plan.total_estimated_miles
        ):
            constrained_plan.avg_cost_per_mile = round(
                constrained_plan.total_estimated_cost
                / constrained_plan.total_estimated_miles,
                4,
            )

        assigned_carriers = (
            self.db.query(TransportationPlanItem.carrier_id)
            .filter(
                TransportationPlanItem.plan_id == constrained_plan.id,
                TransportationPlanItem.carrier_id.isnot(None),
            )
            .distinct()
            .count()
        )
        constrained_plan.carrier_count = assigned_carriers

        if cloned:
            self.db.flush()

        return BalanceResult(
            constrained_plan_id=constrained_plan.id,
            items_cloned=cloned,
            constraints_applied=constraints_applied,
            items_escalated=items_escalated,
            optimization_method="LP_PROJECTION_PHASE_2B",
            capacity_utilization_per_carrier=utilisation,
        )

    # ------------------------------------------------------------------
    # Phase 1 clone (kept for backward compatibility)
    # ------------------------------------------------------------------

    def _clone_only(
        self,
        *,
        source_plan: TransportationPlan,
        source_items: Sequence[TransportationPlanItem],
        cascade_run_id: Optional[str],
        optimization_method: str,
    ) -> BalanceResult:
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
            optimization_method=optimization_method,
            generated_by="AGENT",
            cascade_run_id=cascade_run_id,
        )
        self.db.add(constrained_plan)
        self.db.flush()

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
            constraints_applied=0,
            items_escalated=0,
            optimization_method=optimization_method,
        )

    # ------------------------------------------------------------------
    # Phase 2B — LP-projection
    # ------------------------------------------------------------------

    def _solve_lp_projection(
        self,
        *,
        items: Sequence[TransportationPlanItem],
        carrier_capacity: Dict[int, float],
        escalation_penalty: float,
    ) -> Tuple[Dict[int, int], set, Dict[int, float]]:
        """Solve the transportation LP via ``scipy.optimize.linprog``.

        Returns ``(assignments, escalated, utilisation)`` where:

        - ``assignments``: ``{item_id: carrier_id}`` for items the LP
          assigned. Items not in the dict were escalated.
        - ``escalated``: set of ``item_id`` the LP couldn't fit.
        - ``utilisation``: ``{carrier_id: utilization_fraction}``
          post-solve (loads_assigned / capacity).

        Raises ``RuntimeError`` if the LP solver fails (infeasible or
        numerical issue).
        """
        from scipy.optimize import linprog

        if not items or not carrier_capacity:
            return {}, set(), {}

        carriers = sorted(carrier_capacity.keys())
        n_items = len(items)
        n_carriers = len(carriers)

        if n_carriers == 0:
            return {}, {item.id for item in items}, {}

        # Cost matrix:
        #   - cost[i,c] = item.estimated_cost when c == item.carrier_id
        #     (Phase 2A pick at the original rate).
        #   - cost[i,c] = item.estimated_cost × 1.5 for fallback carriers
        #     (Phase 2B simplification: 50% surcharge approximation; Phase
        #     3 will re-resolve rate cards per fallback carrier).
        #   - cost[i,c] = ESCALATION_PENALTY × 0.99 (high but below
        #     escalation) when item has no estimated_cost (Phase 1
        #     fallback path).
        BIG = escalation_penalty * 0.99
        cost_matrix = np.zeros((n_items, n_carriers))
        for i, item in enumerate(items):
            base_cost = item.estimated_cost or 0.0
            for j, carrier_id in enumerate(carriers):
                if carrier_id == item.carrier_id:
                    cost_matrix[i, j] = base_cost
                elif item.carrier_id is None:
                    cost_matrix[i, j] = base_cost if base_cost else BIG
                else:
                    cost_matrix[i, j] = (
                        base_cost * 1.5 if base_cost else BIG
                    )

        # Variables: x[i,c] flat-indexed as i*n_carriers + c, then
        # escalation slacks e[i] flat-indexed as n_items*n_carriers + i.
        n_vars = n_items * n_carriers + n_items
        c_obj = np.zeros(n_vars)
        for i in range(n_items):
            for j in range(n_carriers):
                c_obj[i * n_carriers + j] = cost_matrix[i, j]
        for i in range(n_items):
            c_obj[n_items * n_carriers + i] = escalation_penalty

        # Equality: for each item i, sum_c x[i,c] + e[i] = 1
        A_eq = np.zeros((n_items, n_vars))
        b_eq = np.ones(n_items)
        for i in range(n_items):
            for j in range(n_carriers):
                A_eq[i, i * n_carriers + j] = 1
            A_eq[i, n_items * n_carriers + i] = 1

        # Inequality: for each carrier c, sum_i x[i,c] <= capacity[c]
        A_ub = np.zeros((n_carriers, n_vars))
        b_ub = np.zeros(n_carriers)
        for j, carrier_id in enumerate(carriers):
            for i in range(n_items):
                A_ub[j, i * n_carriers + j] = 1
            b_ub[j] = float(carrier_capacity[carrier_id])

        bounds = [(0, 1)] * n_vars

        result = linprog(
            c_obj,
            A_ub=A_ub, b_ub=b_ub,
            A_eq=A_eq, b_eq=b_eq,
            bounds=bounds,
            method="highs",
        )
        if not result.success:
            raise RuntimeError(f"LP-projection failed: {result.message}")

        x = result.x

        # Round fractional solutions: each item gets argmax carrier OR
        # escalation if slack is largest.
        assignments: Dict[int, int] = {}
        escalated: set = set()
        for i, item in enumerate(items):
            row_x = x[i * n_carriers : (i + 1) * n_carriers]
            slack = x[n_items * n_carriers + i]
            best_j = int(np.argmax(row_x))
            if slack > row_x[best_j]:
                escalated.add(item.id)
            else:
                assignments[item.id] = carriers[best_j]

        loads_per_carrier: Dict[int, int] = {c: 0 for c in carriers}
        for _, carrier_id in assignments.items():
            loads_per_carrier[carrier_id] += 1
        utilisation = {
            c: round(loads_per_carrier[c] / carrier_capacity[c], 4)
            if carrier_capacity[c] > 0 else 0.0
            for c in carriers
        }

        return assignments, escalated, utilisation


__all__ = [
    "BalanceResult",
    "IntegratedBalancerService",
]
