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
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from sqlalchemy.orm import Session


# Type alias for the capacity dict. Backward-compat: integer keys mean
# "carrier-wide cap" (treated as (carrier_id, None)). Phase 3.2 (§3.42)
# adds tuple keys (carrier_id, equipment_type) for per-equipment caps.
_CapacityKey = Union[int, Tuple[int, Optional[str]]]
_CapacityDict = Dict[_CapacityKey, float]


def _normalize_capacity_dict(
    capacity: Optional[_CapacityDict],
) -> Dict[Tuple[int, Optional[str]], float]:
    """Normalize ``Dict[int, float]`` (Phase 1 / 2B) and tuple-keyed
    ``Dict[Tuple[int, Optional[str]], float]`` (Phase 3.2) shapes
    into one canonical tuple-keyed dict.

    Integer keys ``c`` are treated as ``(c, None)`` — meaning
    "carrier-wide cap, any equipment." Tuple keys pass through.
    """
    if not capacity:
        return {}
    normalized: Dict[Tuple[int, Optional[str]], float] = {}
    for key, value in capacity.items():
        if isinstance(key, int):
            normalized[(key, None)] = float(value)
        elif isinstance(key, tuple) and len(key) == 2:
            carrier_id, equipment_type = key
            normalized[(int(carrier_id), equipment_type)] = float(value)
        else:
            raise ValueError(
                f"Invalid capacity-dict key {key!r}; "
                "expected int or (int, str|None) tuple."
            )
    return normalized

from azirella_data_model.transport_plan import (
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


_GEO_KEYS = {
    "origin_geo_id", "dest_geo_id",
    "origin_state", "dest_state",
    "origin_zip3", "dest_zip3",
}


def _commitment_matches_item(commitment, item, lane_geo_resolver=None) -> bool:
    """Does this commitment plausibly serve this item?

    All three scopes (``lane_filter`` / ``equipment_type`` / ``mode``)
    are AND-combined per item.

    Phase 2 (§3.42): supports `{}` catch-all + `{"lane_id": <id>}` only;
    geographic shapes fail-closed.

    Phase 3 (§3.42): geographic shapes (`origin_state`, `origin_zip3`,
    `origin_geo_id`, `dest_*`) resolved via ``lane_geo_resolver``
    (a :class:`LaneGeographyResolver`) when provided.
    """
    if commitment.equipment_type is not None:
        if commitment.equipment_type != item.equipment_type:
            return False
    if commitment.mode is not None:
        if commitment.mode != item.mode:
            return False

    lane_filter = commitment.lane_filter or {}
    if not lane_filter:
        return True
    if "lane_id" in lane_filter:
        if lane_filter["lane_id"] != item.lane_id:
            return False

    # Phase 3: geographic shapes via resolver.
    geo_constraints = {k: v for k, v in lane_filter.items() if k in _GEO_KEYS}
    if geo_constraints:
        if lane_geo_resolver is None:
            return False  # Phase 2 fail-closed when no resolver passed
        meta = lane_geo_resolver.resolve(item.lane_id)
        if meta is None:
            return False
        for key, expected in geo_constraints.items():
            actual = meta.get(key)
            if actual is None or actual != expected:
                return False
    return True


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

    - **Phase 1 (clone-only)**: omit ``carrier_capacity`` and
      ``resolve_capacity_from_db=False`` → 1:1 clone of unconstrained plan.
    - **Phase 2B (LP-projection, dict API)**: pass
      ``carrier_capacity={carrier_id: max_loads}`` → solve a transportation
      LP that respects per-carrier capacity, minimises total cost,
      escalates items that don't fit.
    - **§3.42 (LP-projection, DB API)**: pass
      ``resolve_capacity_from_db=True`` → query Core's
      :class:`~azirella_data_model.settlement.CarrierCapacityCommitment`
      table and build the capacity dict from canonical commitment rows.
      Production-ready replacement for the ad-hoc dict path.
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
        carrier_capacity: Optional[_CapacityDict] = None,
        resolve_capacity_from_db: bool = False,
        cascade_run_id: Optional[str] = None,
        escalation_penalty: float = _DEFAULT_ESCALATION_PENALTY,
    ) -> BalanceResult:
        """Project the unconstrained plan onto the carrier-capacity
        constraint set.

        - ``carrier_capacity is None`` AND ``resolve_capacity_from_db
          is False`` → Phase 1 clone-only stub
          (``optimization_method='CLONE_PHASE_1'``).
        - ``carrier_capacity`` provided → Phase 2B LP-projection (dict API).
        - ``resolve_capacity_from_db=True`` → §3.42 LP-projection. Reads
          ``CarrierCapacityCommitment`` rows for the source plan's
          (tenant, period_start, period_end) window, builds the capacity
          dict, then runs the same LP. Empty result → clone fallback.
        - LP solve fails → fall back to clone with
          ``optimization_method='LP_INFEASIBLE_FALLBACK'``.

        Both ``carrier_capacity`` and ``resolve_capacity_from_db``
        provided is supported: the dict OVERRIDES the DB query (useful
        for tests / what-if scenarios where the operator wants to override
        a specific carrier's commitment).

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

        # §3.42 — DB-resolved capacity. Runs first so an explicit
        # `carrier_capacity` dict can override specific carriers (test
        # / what-if pattern).
        # Phase 2 (§3.42): pass source_items so commitment rows are
        # filtered by lane + equipment + mode scope.
        # Phase 3.2 (§3.42): tuple keys (carrier_id, equipment_type)
        # support per-equipment caps; integer keys still mean carrier-
        # wide.
        if resolve_capacity_from_db:
            db_capacity = self._resolve_capacity_from_db(
                tenant_id=source_plan.tenant_id,
                period_start=source_plan.plan_start_date,
                period_end=source_plan.plan_end_date,
                items=source_items,
            )
            if db_capacity:
                if carrier_capacity is not None:
                    # Override merge in normalised tuple-key space:
                    # the dict wins per (carrier, equipment) tuple.
                    merged = _normalize_capacity_dict(db_capacity)
                    merged.update(_normalize_capacity_dict(carrier_capacity))
                    carrier_capacity = merged
                else:
                    carrier_capacity = db_capacity
            # If db_capacity is empty AND no dict override → fall through
            # to the Phase 1 clone path below.

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
        carrier_capacity: _CapacityDict,
        escalation_penalty: float,
    ) -> Tuple[Dict[int, int], set, Dict[int, float]]:
        """Solve the transportation LP via ``scipy.optimize.linprog``.

        Phase 3.2 (§3.42): supports per-(carrier × equipment) capacity
        constraints. Capacity dict keys are normalised to
        ``(carrier_id, Optional[equipment_type])`` tuples — integer
        keys (Phase 1 / 2B) become ``(carrier_id, None)`` meaning
        "carrier-wide cap, any equipment". Per-tuple constraints are
        added: ``sum_{i: matching equipment} x[i, carrier] ≤
        capacity[(carrier, equipment)]``. Both per-carrier and per-
        (carrier, equipment) constraints can coexist; the LP enforces
        all of them.

        Returns ``(assignments, escalated, utilisation)`` where:

        - ``assignments``: ``{item_id: carrier_id}``.
        - ``escalated``: set of ``item_id``.
        - ``utilisation``: ``{carrier_id: utilization_fraction}``
          post-solve, computed against the carrier-wide cap or the sum
          of per-equipment caps when no carrier-wide cap exists.

        Raises ``RuntimeError`` on solver failure.
        """
        from scipy.optimize import linprog

        if not items or not carrier_capacity:
            return {}, set(), {}

        # Normalise to tuple-key shape.
        normalised = _normalize_capacity_dict(carrier_capacity)

        # Collect distinct carrier IDs from the capacity dict.
        carriers = sorted({c for (c, _e) in normalised.keys()})
        n_items = len(items)
        n_carriers = len(carriers)
        if n_carriers == 0:
            return {}, {item.id for item in items}, {}

        # Cost matrix (same Phase 2B logic).
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

        # Equality: per item, sum_c x[i,c] + e[i] = 1.
        A_eq = np.zeros((n_items, n_vars))
        b_eq = np.ones(n_items)
        for i in range(n_items):
            for j in range(n_carriers):
                A_eq[i, i * n_carriers + j] = 1
            A_eq[i, n_items * n_carriers + i] = 1

        # Phase 3.2 inequality: one row per (carrier, equipment) tuple.
        # When equipment_type is None, the row sums all items on that
        # carrier (carrier-wide cap). When equipment_type is set, the
        # row sums only items matching that equipment.
        capacity_keys = sorted(normalised.keys())  # deterministic order
        n_capacity_constraints = len(capacity_keys)
        A_ub = np.zeros((n_capacity_constraints, n_vars))
        b_ub = np.zeros(n_capacity_constraints)
        carrier_idx_by_id = {c: idx for idx, c in enumerate(carriers)}
        for k, (carrier_id, equipment_type) in enumerate(capacity_keys):
            j = carrier_idx_by_id[carrier_id]
            for i, item in enumerate(items):
                if equipment_type is None or item.equipment_type == equipment_type:
                    A_ub[k, i * n_carriers + j] = 1
            b_ub[k] = float(normalised[(carrier_id, equipment_type)])

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

        # Utilisation: compute against carrier-wide cap when present,
        # else sum per-equipment caps.
        loads_per_carrier: Dict[int, int] = {c: 0 for c in carriers}
        for _, carrier_id in assignments.items():
            loads_per_carrier[carrier_id] += 1

        utilisation: Dict[int, float] = {}
        for carrier_id in carriers:
            # Prefer the explicit carrier-wide cap if present.
            cap = normalised.get((carrier_id, None))
            if cap is None:
                # Sum per-equipment caps as the carrier's effective cap.
                cap = sum(
                    v for (cid, _e), v in normalised.items()
                    if cid == carrier_id
                )
            if cap and cap > 0:
                utilisation[carrier_id] = round(
                    loads_per_carrier[carrier_id] / cap, 4,
                )
            else:
                utilisation[carrier_id] = 0.0

        return assignments, escalated, utilisation

    # ------------------------------------------------------------------
    # §3.42 — DB-resolved capacity from CarrierCapacityCommitment
    # ------------------------------------------------------------------

    def _resolve_capacity_from_db(
        self,
        *,
        tenant_id: int,
        period_start,
        period_end,
        items: Optional[Sequence] = None,
    ) -> Dict[Tuple[int, Optional[str]], float]:
        """Build the ``{carrier_id: max_loads}`` dict by querying
        ``CarrierCapacityCommitment`` rows that overlap the plan's
        ``[period_start, period_end]`` window.

        Resolution rules:

        - Match rows where ``commitment.period_start <= plan.period_end``
          AND ``commitment.period_end >= plan.period_start`` (overlap).
        - Match rows where ``effective_from <= today`` AND
          ``effective_to IS NULL OR effective_to >= today``.
        - **Phase 2 (§3.42):** when ``items`` is provided, also filter by
          :meth:`_capacity_filter_matches` — the commitment's
          ``lane_filter`` + ``equipment_type`` + ``mode`` must match at
          least one plan item. Without this filter (Phase 1), a contract's
          REEFER-on-FL-NY commitment would boost the carrier's pool even
          when the plan has no REEFER FL-NY items. Phase 2 closes that
          over-permissiveness gap.
        - Per ``contract.id``, sum ``commit_volume`` across all matching
          rows. The carrier id resolves through ``Contract.carrier_id``.
        - Multiple commitments for the same carrier across different
          contracts roll up additively.

        Returns empty dict when no commitments match. Caller treats that
        as "no capacity data → fall through to clone-only path".

        ``items`` is optional for backward compatibility. When omitted,
        Phase 1 behaviour (no per-item filter) applies.

        Still NOT in Phase 2 (deferred to Phase 3 of §3.42):
        - Per-period-granularity prorating (``WEEKLY`` rows still
          contribute their full ``commit_volume`` regardless of how the
          plan period slices the commitment's coverage).
        - Per-(carrier × equipment) capacity dict — the LP shape is
          still ``{carrier_id: total_loads}``, so a single carrier's
          DRY_VAN + REEFER commitments still aggregate. Per-equipment
          LP constraints would require changing the LP shape.
        - Geographic ``lane_filter`` shapes (``origin_state``,
          ``origin_zip3``, ``origin_geo_id``, etc.) — Phase 2 supports
          catch-all ``{}`` and ``{"lane_id": <id>}`` only; geographic
          shapes need the same ``_resolve_lane_geography`` walk that
          ``MovementPlannerService`` does.
        """
        try:
            from azirella_data_model.settlement import (
                CarrierCapacityCommitment,
                Contract,
            )
        except ImportError:
            return {}

        from datetime import datetime
        now = datetime.utcnow()

        rows = (
            self.db.query(CarrierCapacityCommitment, Contract.carrier_id)
            .join(Contract, Contract.id == CarrierCapacityCommitment.contract_id)
            .filter(
                CarrierCapacityCommitment.tenant_id == tenant_id,
                # Period overlap: commitment ends after plan starts AND
                # commitment starts before plan ends.
                CarrierCapacityCommitment.period_end >= period_start,
                CarrierCapacityCommitment.period_start <= period_end,
                # Effective-window check.
                CarrierCapacityCommitment.effective_from <= now,
            )
            .all()
        )

        # Phase 3.2 (§3.42): return per-(carrier_id, equipment_type) keys.
        # Commitments with `equipment_type IS NULL` contribute to the
        # carrier-wide cap key `(carrier_id, None)`; equipment-specific
        # commitments contribute to `(carrier_id, "DRY_VAN")` etc.
        # The LP enforces all per-tuple constraints.
        capacity: Dict[Tuple[int, Optional[str]], float] = {}
        for commitment, carrier_id in rows:
            if commitment.effective_to is not None and commitment.effective_to < now:
                continue
            if carrier_id is None:
                continue
            # Phase 2 (§3.42): scope filter.
            if items is not None and not self._capacity_filter_matches(
                commitment, items,
            ):
                continue
            # Phase 3.3 (§3.42): prorate by period overlap. A WEEKLY
            # commitment that spans Q2 (13 weeks) only contributes
            # ~1/13 of its commit_volume to a single-week plan window.
            volume = self._prorate_commitment_volume(
                commitment, period_start, period_end,
            )
            key = (carrier_id, commitment.equipment_type)
            capacity[key] = capacity.get(key, 0.0) + volume

        # Phase 1 backward-compat: when the caller doesn't pass items,
        # collapse equipment-specific keys to carrier-wide totals so
        # legacy callers see the simple Dict[int, float] shape.
        if items is None:
            collapsed: Dict[Tuple[int, Optional[str]], float] = {}
            for (carrier_id, _equipment), volume in capacity.items():
                key = (carrier_id, None)
                collapsed[key] = collapsed.get(key, 0.0) + volume
            return collapsed
        return capacity

    def _capacity_filter_matches(self, commitment, items: Sequence) -> bool:
        """Phase 2+3 (§3.42) capacity-filter matcher.

        Returns ``True`` if at least one item in ``items`` matches the
        commitment's ``lane_filter`` + ``equipment_type`` + ``mode``
        scope. The match is OR across items — a commitment counts if
        any plan item could plausibly use it.

        Rules (per item, AND-combined):

        - **lane_filter**: empty dict matches all; ``{"lane_id": <id>}``
          matches when ``item.lane_id`` equals it. **Phase 3 (§3.42):**
          geographic shapes (``origin_state``, ``origin_zip3``,
          ``origin_geo_id``, ``dest_*``) resolved via the shared
          :class:`LaneGeographyResolver`.
        - **equipment_type**: ``None`` matches any; otherwise must
          equal ``item.equipment_type``.
        - **mode**: ``None`` matches any; otherwise must equal
          ``item.mode``.
        """
        if not hasattr(self, "_lane_geo_resolver"):
            from app.services.powell.lane_geography import LaneGeographyResolver
            self._lane_geo_resolver = LaneGeographyResolver(self.db)
        for item in items:
            if not _commitment_matches_item(
                commitment, item,
                lane_geo_resolver=self._lane_geo_resolver,
            ):
                continue
            return True
        return False


    @staticmethod
    def _prorate_commitment_volume(commitment, plan_start, plan_end) -> float:
        """Phase 3.3 (§3.42): prorate ``commit_volume`` by the fraction
        of the commitment's period that overlaps the plan window.

        Granularity-specific behaviour:

        - **FLAT**: total ``commit_volume`` applies to the whole period
          regardless of plan-window overlap. Returns full volume; no
          proration.
        - **WEEKLY / MONTHLY / QUARTERLY**: prorate by overlap-day-
          fraction. A WEEKLY commitment over Q2 (91 days) overlapping
          a 7-day plan contributes ``commit_volume × 7 / 91`` to the
          resolved cap.

        ``plan_start`` / ``plan_end`` are inclusive dates from
        ``TransportationPlan``.
        """
        from datetime import date as _date, timedelta as _timedelta

        full_volume = float(commitment.commit_volume)

        granularity = (commitment.period_granularity or "WEEKLY").upper()
        if granularity == "FLAT":
            return full_volume

        # Compute overlap in days (inclusive bounds).
        overlap_start = max(commitment.period_start, plan_start)
        overlap_end = min(commitment.period_end, plan_end)
        if overlap_end < overlap_start:
            return 0.0  # no overlap (defensive — caller already filtered)
        overlap_days = (overlap_end - overlap_start).days + 1

        commitment_days = (commitment.period_end - commitment.period_start).days + 1
        if commitment_days <= 0:
            return full_volume  # defensive: degenerate period
        ratio = overlap_days / commitment_days
        return round(full_volume * ratio, 4)


__all__ = [
    "BalanceResult",
    "IntegratedBalancerService",
]
