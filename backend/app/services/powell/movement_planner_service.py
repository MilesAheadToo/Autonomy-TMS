"""MovementPlannerService — §3.38 Phase 2A (carrier assignment via rate-card)
+ §3.41 Phase 3.4 (GraphSAGE re-ranker under feature flag).

The L3 Unconstrained Movement Plan service per
``docs/TMS_DECISION_HIERARCHY.md`` §4.2. Phase 2A adds **carrier
assignment + cost estimation** on top of Phase 1's load fan-out:

1. Read `LaneVolumePlan` rows (the §3.37 L3 Demand Potential output).
2. Fan out one `TransportationPlanItem` per load, distributed across
   the period (Phase 1 behaviour, unchanged).
3. **NEW (Phase 2A)**: for each item, query Core's `RateCard` substrate
   joined with `Contract` + `Carrier`, find candidate rate cards
   matching the item's lane + equipment + active effective window,
   compute estimated cost per candidate using the rate's basis +
   distance, and pick the **cheapest** one. Set `carrier_id`,
   `rate_id`, `estimated_cost`, `estimated_cost_per_mile`, and
   `distance_miles` on the item.
4. Falls back to NULL if no rate card matches (graceful degradation
   for tenants without seeded rate-cards).

§3.41 Phase 3.4 — optional GraphSAGE re-ranker:

5. After the heuristic produces per-item drafts, if a
   :class:`GraphSAGEMovementPlannerModel` is passed via ``model=...``
   the planner builds a single :class:`GraphSAGEPredictionInput` for
   the full draft batch and asks the model to score (item, carrier)
   pairs.
6. For each item where the model emits ``confidence ≥
   model_confidence_threshold`` and a non-null carrier, the heuristic's
   carrier is **overridden** with the model's choice. Low-confidence
   items keep the heuristic assignment (graceful fallback).
7. Per-call A/B counters (``items_via_model``,
   ``items_via_heuristic_fallback``, ``model_heuristic_agreements``,
   ``model_heuristic_overrides``) land on :class:`PlanResult` and a
   summary blob is stashed on the plan header's
   ``optimization_metadata`` so consumers can audit which method
   produced which plan.

Phase 2A scope explicitly excludes:

- **GraphSAGE network optimisation** for mode-substitution / multi-
  stop consolidation. Phase 2A keeps the L1 forecast row's mode on
  its plan items unchanged. Mode optimisation is Phase 3.
- **Full lane-filter geographic resolution**. Phase 2A handles two
  shapes: catch-all `lane_filter={}` and explicit `{"lane_id": <id>}`.
  Geographic shapes (`origin_geo_id`, `origin_state`, `origin_zip3`,
  etc.) are Phase 2B — they require deeper integration with Core's
  geography hierarchy.
- **Fuel surcharge / accessorials**. Phase 2A computes only the
  linehaul rate × basis. Full ChargeCalculator integration with fuel
  + accessorials is Phase 2B.
- **Min/max charge clamps not actively used**. Phase 2A computes
  the raw rate-basis amount; clamping happens at settlement time
  (§3.29 Phase 3B `ChargeCalculator`).

Distance lookup (Phase 2A): queries TMS-side `LaneProfile.distance_miles`.
If LaneProfile has no row for the lane, falls back to a tenant-config
default (5 0 miles placeholder); Phase 2B will add haversine fallback
from origin/destination site lat/lng.

The `optimization_method` field signals which planner variant ran:
- ``HEURISTIC_PHASE_1`` — fan-out only, no carrier (only used when
  ``carrier_assignment_enabled=False`` for testing)
- ``HEURISTIC_PHASE_2A`` — fan-out + cheapest-carrier from rate-card
  (default; also used when a model was passed but every item fell
  back below the confidence threshold)
- ``GRAPHSAGE_HEURISTIC_HYBRID`` — model overrode some items; the rest
  used the heuristic
- ``GRAPHSAGE_PHASE_3_4`` — model overrode every assignable item
- ``GRAPHSAGE_UNCONSTRAINED`` — Phase 3.5 reserved (full GraphSAGE
  re-plan with mode-substitution, not yet wired)

Plane-module placement: this is TMS-plane decision policy (the *service*
that decides how to plan + assign carriers). The substrate it reads
(`LaneVolumePlan`, `RateCard`, `Contract`, `Carrier`) and writes
(`TransportationPlan` + `TransportationPlanItem`) is canonical state.
`LaneProfile` is TMS-internal (TMS-specific lane attributes like
`distance_miles`).
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
    PlanItemStatus,
    PlanStatus,
    TransportationPlan,
    TransportationPlanItem,
)

from app.services.powell.graphsage_movement_planner import (
    GraphSAGEMovementPlannerModel,
    GraphSAGEPredictionInput,
)


_PLAN_NAME_PATTERN = "L3 Unconstrained Movement Plan {period_start:%Y-%m-%d}"
_GENERATED_BY = "MovementPlannerService"

# Phase 2A default placeholders. Replace with real values once
# LaneProfile.distance_miles + tenant-config wiring lands in §3.38 Phase 2B.
_DEFAULT_DISTANCE_MILES_FALLBACK = 500.0
"""Used when LaneProfile.distance_miles is unset for a lane. Phase 2B will
add haversine fallback from origin/destination site coordinates."""


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
class RateAssignment:
    """The cheapest matching rate-card + carrier for an item.

    All fields are ``None`` when no rate card matches the item's
    (lane, mode, equipment, period) — callers should treat that as
    "leave Phase 1's NULL fields in place."
    """

    carrier_id: Optional[int] = None
    rate_id: Optional[int] = None
    rate_basis: Optional[str] = None
    """``FLAT`` / ``PER_MILE`` / ``PER_PALLET`` / ``PER_HUNDREDWEIGHT`` /
    ``PER_HOUR``. Used by ``_compute_item_cost`` to scale the rate."""

    base_rate: Optional[float] = None
    """The rate-card's base_rate (in carrier's currency)."""

    distance_miles: Optional[float] = None
    """Lane distance used to compute the cost. Recorded so consumers
    can audit the cost derivation."""

    rate_card: Optional[object] = field(default=None, compare=False, repr=False)
    """Reference to the resolved RateCard ORM row. Carried so
    ChargeCalculator integration (Phase 2A.1 / §3.38 item 2) can
    invoke the canonical math path without a re-fetch. Excluded from
    equality / repr to keep the dataclass lightweight."""

    contract: Optional[object] = field(default=None, compare=False, repr=False)
    """Reference to the rate card's parent Contract ORM row. Carried
    for ChargeCalculator wiring."""

    accessorials: tuple = field(default=(), compare=False, repr=False)
    """Optional tuple of Accessorial ORM rows the contract has.
    Phase 2A.1 always passes ``()`` since live accessorial-conditions
    aren't available at planning time. Phase 3 will populate this
    when ChargeCalculator's accessorial path matures."""

    fuel_formula: Optional[object] = field(default=None, compare=False, repr=False)
    """Optional FuelSurchargeFormula ORM row. Same Phase 2A.1 caveat
    as ``accessorials``: live fuel-index data isn't fetched at planning
    time; Phase 3 will populate it."""


_NULL_ASSIGNMENT = RateAssignment()
"""Sentinel for "no carrier matched". Phase 1 fallback uses this."""


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

    items_with_carrier: int = 0
    """Phase 2A: items that got a carrier_id assigned via rate-card
    lookup. Phase 1 (carrier_assignment_enabled=False): always 0."""

    items_without_carrier: int = 0
    """Phase 2A: items where no rate card matched and ``carrier_id``
    stayed ``None``. Phase 1: equals ``items_written``."""

    items_via_model: int = 0
    """§3.41 Phase 3.4: items whose final carrier came from the
    GraphSAGE model's high-confidence prediction (kept the heuristic
    pick when ``model_carrier == heuristic_carrier``; overrode when
    they differed). Always 0 when ``model=None``."""

    items_via_heuristic_fallback: int = 0
    """§3.41 Phase 3.4: items where the model abstained (low confidence
    or null prediction) and the heuristic's choice was kept. When
    ``model=None``, equals ``items_with_carrier`` (every item used
    the heuristic). When the model is provided but untrained,
    typically equals ``items_with_carrier`` (the untrained sentinel
    emits ``confidence=0.0`` for every row)."""

    model_heuristic_agreements: int = 0
    """§3.41 Phase 3.4: items where the model and heuristic picked the
    same carrier (subset of ``items_via_model``)."""

    model_heuristic_overrides: int = 0
    """§3.41 Phase 3.4: items where the model picked a different
    carrier than the heuristic and we accepted the model's choice
    (subset of ``items_via_model``)."""

    model_version: Optional[str] = None
    """§3.41 Phase 3.4: the trained model's version stamp (when a
    model was passed). ``None`` when no model was used. Persisted on
    the plan header's ``optimization_metadata`` for audit."""


class MovementPlannerService:
    """L3 Unconstrained Movement Plan service — Phase 2A heuristic.

    Phase 2A adds carrier assignment + cost estimation via rate-card
    lookup on top of Phase 1's load fan-out. Set
    ``carrier_assignment_enabled=False`` on ``plan_movement()`` to keep
    Phase 1's NULL-carrier behaviour (used for tests that don't seed
    rate cards).
    """

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
        carrier_assignment_enabled: bool = True,
        model: Optional[GraphSAGEMovementPlannerModel] = None,
        model_confidence_threshold: float = 0.5,
    ) -> PlanResult:
        """Read LaneVolumePlan rows for the given (tenant, config,
        period_start) and produce a TransportationPlan with items.

        Phase 2A behaviour (default ``carrier_assignment_enabled=True``):
        one plan item per round(forecast_loads_p50) load, pickup dates
        distributed evenly across the period, **plus** carrier + cost +
        distance assigned per item via cheapest-rate-card lookup.

        Phase 1 fallback (``carrier_assignment_enabled=False``): keeps
        Phase 1's NULL-carrier behaviour for tests / tenants without
        seeded rate cards.

        §3.41 Phase 3.4 — when ``model`` is supplied, the heuristic
        runs first as the baseline assignment; the model is then asked
        to re-rank the full draft batch in a single ``predict()`` call,
        and per-item picks override the heuristic when
        ``confidence ≥ model_confidence_threshold``. Untrained models
        return ``confidence=0.0`` for every row, so passing an
        untrained ``TorchGraphSAGEMovementPlanner`` is equivalent to
        Phase 2A — the heuristic is preserved and the model is
        instrumented for monitoring only.

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

        # 2. Build the TransportationPlan header (optimization_method
        # is patched after pass 3 once we know how the model performed).
        plan = TransportationPlan(
            config_id=config_id,
            tenant_id=tenant_id,
            plan_version="unconstrained_reference",
            plan_name=_PLAN_NAME_PATTERN.format(period_start=period_start),
            status=PlanStatus.DRAFT,
            plan_start_date=period_start,
            plan_end_date=period_start + timedelta(days=period_days - 1),
            planning_horizon_days=period_days,
            optimization_method=(
                "HEURISTIC_PHASE_2A"
                if carrier_assignment_enabled
                else "HEURISTIC_PHASE_1"
            ),
            generated_by="AGENT",
            cascade_run_id=cascade_run_id,
        )
        self.db.add(plan)
        self.db.flush()  # populate plan.id

        # 3. Pass 1 — heuristic fan-out into pending-item drafts.
        items_per_lane: dict = {}
        skipped_zero_loads = 0
        pending: List[dict] = []
        # Track candidate carriers seen across the plan; used as the
        # available_carriers list for the GraphSAGE re-ranker. Keyed
        # by (carrier_id, rate_id) to dedupe.
        candidate_carriers: dict = {}

        for row in forecast_rows:
            if not _is_leaf_row(row, forecast_rows):
                continue

            n_items = round(row.forecast_loads_p50)
            if n_items <= 0:
                skipped_zero_loads += 1
                continue

            origin_id, destination_id = self._lane_endpoints(row.lane_id)

            distance_miles = (
                self._lane_distance_miles(row.lane_id)
                if carrier_assignment_enabled
                else None
            )
            assignment = (
                self._select_carrier_and_rate(
                    tenant_id=tenant_id,
                    period_start=period_start,
                    mode=row.mode,
                    equipment_type=row.equipment_type,
                    lane_id=row.lane_id,
                    distance_miles=distance_miles,
                )
                if carrier_assignment_enabled
                else _NULL_ASSIGNMENT
            )

            if assignment.carrier_id is not None and assignment.rate_id is not None:
                candidate_carriers.setdefault(
                    (assignment.carrier_id, assignment.rate_id),
                    {
                        "carrier_id": assignment.carrier_id,
                        "rate_card_id": assignment.rate_id,
                        "base_rate": assignment.base_rate or 0.0,
                        "equipment_type": row.equipment_type,
                        "rate_basis": assignment.rate_basis,
                        "capacity_remaining": 0,
                    },
                )

            for i in range(n_items):
                pickup_dt = self._distribute_pickup(
                    period_start=period_start,
                    period_days=period_days,
                    item_index=i,
                    total_items=n_items,
                )
                delivery_dt = pickup_dt + timedelta(days=1)

                per_item_weight = self._derive_weight(row, n_items)
                per_item_volume = self._derive_volume(row, n_items)
                per_item_cost = (
                    self._compute_item_cost(
                        assignment=assignment,
                        weight=per_item_weight,
                    )
                    if carrier_assignment_enabled and assignment.rate_id is not None
                    else None
                )

                pending.append({
                    "_row_lane_id": row.lane_id,
                    "_heuristic_carrier_id": assignment.carrier_id,
                    "_heuristic_rate_id": assignment.rate_id,
                    "_heuristic_cost": per_item_cost,
                    "_distance_miles": distance_miles,
                    "_kwargs": {
                        "plan_id": plan.id,
                        "tenant_id": tenant_id,
                        "origin_site_id": origin_id,
                        "destination_site_id": destination_id,
                        "lane_id": row.lane_id,
                        "mode": row.mode,
                        "equipment_type": row.equipment_type,
                        "carrier_id": assignment.carrier_id,
                        "rate_id": assignment.rate_id,
                        "status": PlanItemStatus.PLANNED,
                        "planned_pickup_date": pickup_dt,
                        "planned_delivery_date": delivery_dt,
                        "shipment_count": 1,
                        "total_weight": per_item_weight,
                        "total_volume": per_item_volume,
                        "estimated_cost": per_item_cost,
                        "estimated_cost_per_mile": (
                            round(per_item_cost / distance_miles, 4)
                            if per_item_cost is not None and distance_miles
                            else None
                        ),
                        "distance_miles": (
                            distance_miles
                            if carrier_assignment_enabled
                            else None
                        ),
                        "is_multi_stop": False,
                    },
                })

        # 4. Pass 2 — optional GraphSAGE re-rank.
        items_via_model = 0
        items_via_heuristic_fallback = 0
        agreements = 0
        overrides = 0
        model_version: Optional[str] = None

        if model is not None and pending and carrier_assignment_enabled:
            model_version = model.model_version()
            predictions = self._predict_with_model(
                model=model,
                tenant_id=tenant_id,
                config_id=config_id,
                period_start=period_start,
                period_days=period_days,
                pending=pending,
                candidate_carriers=candidate_carriers,
            )
            for idx, p in enumerate(pending):
                pred = predictions.get(idx)
                heuristic_carrier = p["_heuristic_carrier_id"]
                if (
                    pred is None
                    or pred.confidence < model_confidence_threshold
                    or pred.carrier_id is None
                ):
                    if heuristic_carrier is not None:
                        items_via_heuristic_fallback += 1
                    continue
                items_via_model += 1
                if pred.carrier_id == heuristic_carrier:
                    agreements += 1
                else:
                    overrides += 1
                    p["_kwargs"]["carrier_id"] = pred.carrier_id
                    if pred.rate_id is not None:
                        p["_kwargs"]["rate_id"] = pred.rate_id
                    if pred.estimated_cost is not None:
                        new_cost = round(float(pred.estimated_cost), 2)
                        p["_kwargs"]["estimated_cost"] = new_cost
                        dist = p["_distance_miles"]
                        p["_kwargs"]["estimated_cost_per_mile"] = (
                            round(new_cost / dist, 4) if dist else None
                        )
        else:
            for p in pending:
                if p["_heuristic_carrier_id"] is not None:
                    items_via_heuristic_fallback += 1

        # 5. Pass 3 — write items.
        items_written = 0
        items_with_carrier = 0
        items_without_carrier = 0
        total_estimated_cost = 0.0
        total_distance = 0.0

        for p in pending:
            kwargs = p["_kwargs"]
            self.db.add(TransportationPlanItem(**kwargs))
            items_written += 1
            items_per_lane[p["_row_lane_id"]] = (
                items_per_lane.get(p["_row_lane_id"], 0) + 1
            )
            if kwargs["carrier_id"] is not None:
                items_with_carrier += 1
            else:
                items_without_carrier += 1
            if kwargs["estimated_cost"] is not None:
                total_estimated_cost += kwargs["estimated_cost"]
            if kwargs["distance_miles"]:
                total_distance += kwargs["distance_miles"]

        # 6. Update plan-header summary metrics + optimization_method.
        plan.total_planned_loads = items_written
        plan.total_planned_shipments = items_written
        if total_estimated_cost > 0:
            plan.total_estimated_cost = round(total_estimated_cost, 2)
        if total_distance > 0:
            plan.total_estimated_miles = round(total_distance, 2)
            if total_estimated_cost > 0:
                plan.avg_cost_per_mile = round(
                    total_estimated_cost / total_distance, 4,
                )
        if items_with_carrier > 0:
            assigned_carriers = (
                self.db.query(TransportationPlanItem.carrier_id)
                .filter(
                    TransportationPlanItem.plan_id == plan.id,
                    TransportationPlanItem.carrier_id.isnot(None),
                )
                .distinct()
                .count()
            )
            plan.carrier_count = assigned_carriers

        plan.optimization_method = self._derive_optimization_method(
            carrier_assignment_enabled=carrier_assignment_enabled,
            model_provided=model is not None,
            items_via_model=items_via_model,
            items_assignable=items_with_carrier,
        )
        if model is not None:
            plan.optimization_metadata = {
                "graphsage_model_version": model_version,
                "model_confidence_threshold": model_confidence_threshold,
                "items_via_model": items_via_model,
                "items_via_heuristic_fallback": items_via_heuristic_fallback,
                "model_heuristic_agreements": agreements,
                "model_heuristic_overrides": overrides,
            }

        if items_written:
            self.db.flush()

        return PlanResult(
            plan_id=plan.id,
            items_written=items_written,
            items_per_lane=items_per_lane,
            skipped_zero_loads=skipped_zero_loads,
            items_with_carrier=items_with_carrier,
            items_without_carrier=items_without_carrier,
            items_via_model=items_via_model,
            items_via_heuristic_fallback=items_via_heuristic_fallback,
            model_heuristic_agreements=agreements,
            model_heuristic_overrides=overrides,
            model_version=model_version,
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

    # ------------------------------------------------------------------
    # Phase 2A — rate-card resolver + lane distance + cost compute
    # ------------------------------------------------------------------

    def _select_carrier_and_rate(
        self,
        *,
        tenant_id: int,
        period_start: date,
        mode: str,
        equipment_type: Optional[str],
        lane_id: int,
        distance_miles: Optional[float],
    ) -> RateAssignment:
        """Pick the cheapest matching rate card for the (lane, equipment,
        period) combo and return its carrier + rate id.

        Phase 2A matching rules:

        - ``RateCard.tenant_id == tenant_id``
        - ``RateCard.effective_from <= period_start`` AND
          (``effective_to IS NULL`` OR ``effective_to >= period_start``)
        - ``RateCard.equipment_type IS NULL`` (any equipment)
          OR ``RateCard.equipment_type == equipment_type``
        - ``RateCard.lane_filter`` matches via :meth:`_lane_filter_matches`
          (Phase 2A: catch-all `{}` or explicit `{"lane_id": <id>}`).

        Cost ranking uses :meth:`_estimate_card_cost` with a unit weight
        (1 lb / 1 pallet placeholder) so PER_PALLET / PER_HUNDREDWEIGHT
        cards rank consistently against PER_MILE / FLAT cards even when
        per-item weight isn't known yet.

        Returns a :class:`RateAssignment` with NULL fields when no
        candidate exists. Caller treats that as "leave Phase 1 NULL
        fields in place."
        """
        try:
            from azirella_data_model.settlement.entities import (
                Contract,
                RateCard,
            )
        except ImportError:
            # Settlement substrate unavailable in this deployment;
            # graceful no-op.
            return _NULL_ASSIGNMENT

        period_dt = datetime.combine(period_start, datetime.min.time())
        candidates = (
            self.db.query(RateCard)
            .filter(
                RateCard.tenant_id == tenant_id,
                RateCard.effective_from <= period_dt,
            )
            .all()
        )

        # Filter: effective window (NULL effective_to = open-ended)
        candidates = [
            rc for rc in candidates
            if rc.effective_to is None or rc.effective_to >= period_dt
        ]
        # Filter: equipment match
        candidates = [
            rc for rc in candidates
            if rc.equipment_type is None or rc.equipment_type == equipment_type
        ]
        # Filter: lane filter
        candidates = [
            rc for rc in candidates
            if self._lane_filter_matches(rc.lane_filter or {}, lane_id)
        ]

        if not candidates:
            return _NULL_ASSIGNMENT

        # Rank by estimated cost; pick cheapest.
        scored = []
        for rc in candidates:
            cost = self._estimate_card_cost(
                rate_basis=rc.rate_basis,
                base_rate=float(rc.base_rate),
                distance_miles=distance_miles,
            )
            if cost is None:
                continue
            scored.append((cost, rc))
        if not scored:
            return _NULL_ASSIGNMENT

        scored.sort(key=lambda pair: pair[0])
        _, best = scored[0]

        # Resolve the carrier through the contract.
        contract = (
            self.db.query(Contract)
            .filter(Contract.id == best.contract_id)
            .first()
        )
        carrier_id = contract.carrier_id if contract else None

        return RateAssignment(
            carrier_id=carrier_id,
            rate_id=best.id,
            rate_basis=best.rate_basis,
            base_rate=float(best.base_rate),
            distance_miles=distance_miles,
            rate_card=best,
            contract=contract,
            # Phase 2A.1 — accessorials + fuel formula intentionally left
            # empty. ChargeCalculator handles None gracefully (returns 0
            # for those parts). Phase 3 will populate from contract.
            accessorials=(),
            fuel_formula=None,
        )

    def _lane_filter_matches(self, lane_filter: dict, lane_id: int) -> bool:
        """Lane-filter matcher.

        Phase 2A: catch-all empty filter or explicit ``lane_id`` match.
        Phase 2A.2 / §3.38 item 3: geographic shapes — supports
        ``origin_geo_id`` / ``dest_geo_id`` / ``origin_state`` /
        ``dest_state`` / ``origin_zip3`` / ``dest_zip3`` / ``mode``.
        Multiple shapes in one filter are AND-combined.

        Resolution: requires the TransportationLane row + the lane's
        from-/to-Site rows + their Geography rows. When the lookup
        chain breaks (test fixtures, partial deployments), returns
        ``False`` for unrecognised shapes (safer to miss than over-
        match).
        """
        if not lane_filter:
            return True  # catch-all

        # Direct lane_id match (also serves as the Phase 2A pattern)
        if "lane_id" in lane_filter:
            if lane_filter["lane_id"] != lane_id:
                return False

        # Geographic shapes — defer the lookup until we know we need it.
        geo_keys = {
            "origin_geo_id", "dest_geo_id",
            "origin_state", "dest_state",
            "origin_zip3", "dest_zip3",
            "mode",
        }
        needs_geo_resolution = any(k in lane_filter for k in geo_keys)
        if not needs_geo_resolution:
            # Only the lane_id shape was specified (already handled above).
            return True

        meta = self._resolve_lane_geography(lane_id)
        if meta is None:
            return False  # fail-closed

        for key, expected in lane_filter.items():
            if key == "lane_id":
                continue  # already checked
            actual = meta.get(key)
            if actual is None or actual != expected:
                return False
        return True

    def _resolve_lane_geography(self, lane_id: int) -> Optional[dict]:
        """Resolve geographic metadata for a lane via shared resolver.

        Delegates to :class:`LaneGeographyResolver` (extracted to a
        sibling module in §3.42 Phase 3 so `IntegratedBalancerService`
        can reuse the same logic).
        """
        if not hasattr(self, "_lane_geo_resolver"):
            from app.services.powell.lane_geography import LaneGeographyResolver
            self._lane_geo_resolver = LaneGeographyResolver(self.db)
        return self._lane_geo_resolver.resolve(lane_id)

    @staticmethod
    def _estimate_card_cost(
        *,
        rate_basis: str,
        base_rate: float,
        distance_miles: Optional[float],
    ) -> Optional[float]:
        """Compute a per-load cost estimate for ranking rate cards.

        Phase 2A handles the 4 rate bases that don't require live
        accessorial / fuel data:

        - ``FLAT``: ``base_rate``
        - ``PER_MILE``: ``base_rate × distance_miles`` (or None when
          distance unknown)
        - ``PER_HUNDREDWEIGHT``: ``base_rate × 100`` (per-1000lb
          placeholder; real per-item weight applied in
          ``_compute_item_cost``)
        - ``PER_PALLET``: ``base_rate`` per pallet (1-pallet placeholder
          for ranking; real count applied in ``_compute_item_cost``)
        - ``PER_HOUR``: ``base_rate × 8`` (8-hour day placeholder)

        Returns None if the basis can't be ranked (e.g., PER_MILE with
        no distance). The caller filters those out.
        """
        if rate_basis == "FLAT":
            return base_rate
        if rate_basis == "PER_MILE":
            if not distance_miles:
                return None
            return base_rate * distance_miles
        if rate_basis == "PER_HUNDREDWEIGHT":
            # Rank assuming a 1000-lb load (placeholder). Real cost
            # uses per-item weight in _compute_item_cost.
            return base_rate * 10.0
        if rate_basis == "PER_PALLET":
            # Rank assuming a 1-pallet load (placeholder).
            return base_rate
        if rate_basis == "PER_HOUR":
            # Rank assuming an 8-hour load (placeholder).
            return base_rate * 8.0
        return None

    @staticmethod
    def _compute_item_cost(
        *,
        assignment: RateAssignment,
        weight: Optional[float],
    ) -> Optional[float]:
        """Apply the assigned rate basis to per-item parameters.

        Phase 2A.1 / §3.38 item 2: routes through Core's
        ``ChargeCalculator`` (the canonical pure-math freight-charge
        calculator from §3.29 Phase 3B) when the resolved rate card
        and contract ORM refs are available. ChargeCalculator handles
        the 5 rate bases consistently, applies min/max clamps, and
        returns a structured ``ChargeBreakdown``.

        Phase 2A.1 always passes ``accessorials=[]`` and
        ``fuel_surcharge_formula=None`` because live accessorial-
        conditions and fuel-index data aren't available at planning
        time. ChargeCalculator returns 0 for those parts; the
        linehaul amount equals what the inline math would compute.
        Phase 3 will fetch real accessorial / fuel data from contract
        relationships.

        Falls back to the inline rate-basis math when ChargeCalculator
        / Core's settlement substrate isn't importable (test fixtures,
        partial deployments).
        """
        if assignment.rate_basis is None or assignment.base_rate is None:
            return None

        # Try ChargeCalculator path (canonical Phase 2A.1 math).
        if assignment.rate_card is not None and assignment.contract is not None:
            try:
                from azirella_data_model.settlement.charge_calculator import (
                    ChargeCalculator,
                )

                calc = ChargeCalculator()
                # ChargeCalculator expects weight in pounds (LTL convention).
                # TransportationPlanItem.total_weight is also pounds (TMS
                # convention; see app/models/tms_planning.py).
                breakdown = calc.calculate(
                    contract=assignment.contract,
                    rate_card=assignment.rate_card,
                    accessorials=list(assignment.accessorials) or [],
                    fuel_surcharge_formula=assignment.fuel_formula,
                    distance_miles=float(assignment.distance_miles or 0.0),
                    weight_pounds=float(weight) if weight else None,
                    pallet_count=None,  # Phase 2A.1: pallet count not tracked
                    hours_on_trip=None,  # Phase 2A.1: hours not tracked
                    fuel_index_value=None,  # Phase 3 wires real fuel data
                    accessorial_conditions=None,  # Phase 3 wires real conditions
                )
                return float(breakdown.total_amount)
            except Exception:
                # ChargeCalculator unavailable / threw — fall through to
                # inline math. This keeps Phase 2A.1 robust in test
                # fixtures and partial deployments.
                pass

        # Inline fallback (matches the Phase 2A math).
        rb = assignment.rate_basis
        rate = assignment.base_rate
        if rb == "FLAT":
            return round(rate, 2)
        if rb == "PER_MILE":
            if not assignment.distance_miles:
                return None
            return round(rate * assignment.distance_miles, 2)
        if rb == "PER_HUNDREDWEIGHT":
            if not weight:
                return round(rate * 10.0, 2)
            return round(rate * (weight / 100.0), 2)
        if rb == "PER_PALLET":
            return round(rate, 2)
        if rb == "PER_HOUR":
            return round(rate * 8.0, 2)
        return None

    def _lane_distance_miles(self, lane_id: int) -> Optional[float]:
        """Look up `LaneProfile.distance_miles` for the lane.

        Returns the configured value when present; otherwise the
        Phase 2A fallback (``_DEFAULT_DISTANCE_MILES_FALLBACK``).
        """
        try:
            from app.models.transportation_config import LaneProfile
        except ImportError:
            return _DEFAULT_DISTANCE_MILES_FALLBACK

        profile = (
            self.db.query(LaneProfile)
            .filter(LaneProfile.lane_id == lane_id)
            .first()
        )
        if profile is None or profile.distance_miles is None:
            return _DEFAULT_DISTANCE_MILES_FALLBACK
        return float(profile.distance_miles)

    # ------------------------------------------------------------------
    # §3.41 Phase 3.4 — GraphSAGE re-rank helpers
    # ------------------------------------------------------------------

    def _predict_with_model(
        self,
        *,
        model: GraphSAGEMovementPlannerModel,
        tenant_id: int,
        config_id: int,
        period_start: date,
        period_days: int,
        pending: List[dict],
        candidate_carriers: dict,
    ) -> dict:
        """Build a single :class:`GraphSAGEPredictionInput` from the
        heuristic pending-item drafts and call ``model.predict``.

        Returns ``{pending_index: GraphSAGEPredictionOutput}`` so the
        caller can index by draft order. ``model.predict`` failures
        downgrade to an empty dict (the heuristic stays in force).
        """
        lane_volume_forecasts = [
            {
                "item_id": idx,
                "lane_id": p["_row_lane_id"],
                "mode": p["_kwargs"]["mode"],
                "equipment_type": p["_kwargs"]["equipment_type"],
                "forecast_loads_p50": 1.0,
            }
            for idx, p in enumerate(pending)
        ]
        available_carriers = list(candidate_carriers.values())
        if not available_carriers:
            return {}

        inputs = GraphSAGEPredictionInput(
            tenant_id=tenant_id,
            config_id=config_id,
            period_start=period_start,
            period_days=period_days,
            lane_volume_forecasts=lane_volume_forecasts,
            available_carriers=available_carriers,
        )
        try:
            outputs = model.predict(inputs)
        except Exception:
            return {}
        return {o.item_id: o for o in outputs}

    @staticmethod
    def _derive_optimization_method(
        *,
        carrier_assignment_enabled: bool,
        model_provided: bool,
        items_via_model: int,
        items_assignable: int,
    ) -> str:
        """Pick the ``optimization_method`` label from the per-call
        counters. Encodes the Phase 3.4 A/B vocabulary documented at
        the top of this module."""
        if not carrier_assignment_enabled:
            return "HEURISTIC_PHASE_1"
        if not model_provided or items_via_model == 0:
            return "HEURISTIC_PHASE_2A"
        if items_via_model >= items_assignable:
            return "GRAPHSAGE_PHASE_3_4"
        return "GRAPHSAGE_HEURISTIC_HYBRID"


__all__ = [
    "MovementPlannerService",
    "PlanResult",
    "RateAssignment",
]
