"""Lane-flow simulator — TMS twin's transition function.

Per Autonomy-Core/docs/architecture/TWIN_AND_ENVELOPES.md §4, the
simulator's exogenous input is a ``ShipmentGenerator``-emitted
``TransferOrderEnvelope`` arrival stream. The simulator decides how
carriers, docks, and equipment respond — lane queues, dock dynamics,
carrier capacity, equipment flow.

Phase 1 (this module) is a **single-channel** simulator: one
``(origin × destination × product)`` channel per instance. Multi-channel
training instantiates N simulators in parallel. Per-channel scope
keeps the physics tractable for the Phase-1 "rough but unbiased"
quality bar (TWIN_AND_ENVELOPES.md §6.1) — Phase 2/3 layer richer
multi-channel coupling on top.

Replaces the role of ``app/services/dag_simpy_simulator.py`` (a
1,252-line clone of SCP's inventory simulator that the carrier-flow
plane never needed). The legacy file stays in place during PR-3 and
is deleted in PR-5.
"""
from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Mapping

from azirella_data_model.conformal import ConformalBand
from azirella_data_model.digital_twin.twin_interface import (
    TwinMode,
    assert_plan_production_is_deterministic,
)
from azirella_data_model.ml.outcome import OutcomeEvent
from azirella_demand_planning_contract import Tier

from .observations import LaneFlowAction, LaneFlowObservation, LaneFlowReward
from .shipment_generator import ShipmentGenerator, lane_series_key

# Producer signature stamped on §3.31 OutcomeEvents emitted by the
# Phase-1 lane-flow simulator. Bump when emission semantics change so
# downstream training corpus / audit consumers can route on origin.
OUTCOME_PRODUCER_SIGNATURE = "tms:lane_flow_simulator:v0.1.0"

# Producer signature carried on ConformalBands the simulator constructs
# from envelope rows before sampling. The envelope itself was produced
# by a ShipmentGenerator (Phase 1 / 2 / 3); this signature identifies
# the simulator-side wrapping step, not the upstream producer.
CONFORMAL_BAND_PRODUCER_SIGNATURE = "tms:lane_flow_simulator:envelope_row:v0.1.0"

# Outcome callback type — the simulator emits OutcomeEvents through
# this sink when shipments dispatch and arrive. Sinks are responsible
# for routing (training corpus collection, BSC reward attribution,
# audit logs).
OutcomeSink = Callable[[OutcomeEvent], None]

# Tier → bucket size in days. Mirrors the ladder in shipment_generator.py.
_BUCKET_DAYS: dict[Tier, int] = {
    Tier.STRATEGIC: 30,
    Tier.TACTICAL: 7,
    Tier.EXECUTION: 1,
}


# ── Configuration dataclasses ────────────────────────────────────────


@dataclass(frozen=True)
class CarrierProfile:
    """Per-carrier physics parameters.

    Phase 1: a small parametric profile sufficient to give the policy
    distinct learning signal across carrier choice. Phase 2 fits these
    from the tenant's realised carrier history.
    """

    carrier_id: str
    cost_per_load: float
    """Carrier rate per load on this lane."""
    on_time_rate: float
    """Probability a load arrives on time. In ``[0, 1]``."""
    capacity_per_bucket: int
    """Maximum loads this carrier accepts per bucket."""

    def __post_init__(self) -> None:
        if not (0.0 <= self.on_time_rate <= 1.0):
            raise ValueError(
                f"on_time_rate must be in [0, 1]; got {self.on_time_rate}"
            )
        if self.capacity_per_bucket < 0:
            raise ValueError(
                f"capacity_per_bucket must be >= 0; got {self.capacity_per_bucket}"
            )


@dataclass(frozen=True)
class EquipmentProfile:
    """Per-equipment-archetype physics parameters."""

    equipment_kind: str
    load_capacity_units: float
    """Line-item units per load (e.g. a 53-ft dry van might carry 26
    cases). Phase 1 keeps it as a single scalar; Phase 2 may resolve
    from product master."""

    def __post_init__(self) -> None:
        if self.load_capacity_units <= 0:
            raise ValueError(
                f"load_capacity_units must be > 0; got {self.load_capacity_units}"
            )


@dataclass(frozen=True)
class LanePhysicsParams:
    """Static lane parameters consumed by ``LaneFlowSimulator``."""

    origin_site_id: str
    destination_site_id: str
    product_id: str
    transit_buckets: int
    """Bucket-grain transit time. A load dispatched in bucket ``t``
    arrives in bucket ``t + transit_buckets``."""
    initial_equipment: int
    """Equipment units at the origin terminal at ``reset`` time."""
    dock_capacity_per_bucket: int
    """Destination dock throughput target — used by the reward's
    utilisation term."""
    carriers: Mapping[str, CarrierProfile]
    equipment_kinds: Mapping[str, EquipmentProfile]
    cost_target_per_load: float
    """Tenant cost-per-load budget. Used to normalise the reward's
    cost term so it sits in roughly ``[0, 2]`` around 1.0."""

    def __post_init__(self) -> None:
        if self.transit_buckets < 1:
            raise ValueError(
                f"transit_buckets must be >= 1; got {self.transit_buckets}"
            )
        if self.initial_equipment < 0:
            raise ValueError(
                f"initial_equipment must be >= 0; got {self.initial_equipment}"
            )
        if not self.carriers:
            raise ValueError("carriers must contain at least one carrier")
        if not self.equipment_kinds:
            raise ValueError("equipment_kinds must contain at least one kind")
        if self.cost_target_per_load <= 0:
            raise ValueError(
                f"cost_target_per_load must be > 0; got {self.cost_target_per_load}"
            )


# ── Internal state ───────────────────────────────────────────────────


@dataclass
class _InFlightLoad:
    """A dispatched load awaiting arrival.

    ``decision_id`` and ``carrier_id`` are carried so the §3.31
    OutcomeEvent emitted on arrival can be joined back to the
    dispatch tender that produced it.
    """

    arrival_bucket: int
    on_time: bool
    decision_id: str = ""
    carrier_id: str = ""


@dataclass
class _SimulatorState:
    """Mutable internal state. Reset on every ``reset()`` call."""

    bucket: int = 0
    """Current bucket index (0..horizon-1)."""
    bucket_start: date | None = None
    in_flight: list[_InFlightLoad] = field(default_factory=list)
    equipment_available: int = 0
    dock_queue_depth: int = 0
    last_action: LaneFlowAction | None = None
    recent_otd: deque[float] = field(default_factory=lambda: deque(maxlen=4))
    recent_cost_per_load: deque[float] = field(default_factory=lambda: deque(maxlen=4))
    rng: random.Random = field(default_factory=random.Random)
    # Cached envelope reference produced at reset time. Phase 1 calls
    # the generator once per rollout and indexes per bucket; Phase 3
    # may re-query each bucket if the producer is online.
    cached_envelope: Any = None  # TransferOrderEnvelope


# ── Reward callable type ─────────────────────────────────────────────


RewardFn = Callable[
    [LaneFlowObservation, LaneFlowAction, dict[str, Any]],
    LaneFlowReward,
]


# ── Simulator ────────────────────────────────────────────────────────


class LaneFlowSimulator:
    """Carrier-flow physics: lane queues, equipment flow, dock dynamics.

    Single-channel: one ``(origin × destination × product)`` per
    instance. Multi-channel training instantiates N simulators.

    Determinism: pinned by ``scenario_seed`` passed to ``reset``. All
    stochastic decisions (demand realisation from envelope, on-time
    Bernoulli) draw from a single seeded ``random.Random``.

    TwinMode: ``TwinMode.TRAINING`` (default) enables stochasticity;
    ``TwinMode.PLAN_PRODUCTION`` validates the configuration is
    deterministic via Core's
    :func:`assert_plan_production_is_deterministic` and uses point
    estimates everywhere.
    """

    DEFAULT_HORIZON_BUCKETS = 12
    DEFAULT_OTD_BASELINE = 0.85
    DEFAULT_OTD_TARGET = 0.98
    DEFAULT_DOCK_UTIL_TARGET = 0.70
    DEFAULT_LATE_DISPATCH_HOURS = 12.0
    DEFAULT_REWARD_WEIGHTS: dict[str, float] = {
        "otd": 1.0,
        "cost": 0.30,
        "dock_util": 0.10,
        "equipment_balance": 0.10,
        "churn": 0.05,
    }

    def __init__(
        self,
        *,
        generator: ShipmentGenerator,
        tenant_id: int,
        config_id: int,
        lane_params: LanePhysicsParams,
        tier: Tier = Tier.TACTICAL,
        horizon_buckets: int = DEFAULT_HORIZON_BUCKETS,
        mode: TwinMode = TwinMode.TRAINING,
        demand_stochastic: bool = True,
        on_time_stochastic: bool = True,
        reward_fn: RewardFn | None = None,
        outcome_sink: OutcomeSink | None = None,
    ):
        self._generator = generator
        self.tenant_id = int(tenant_id)
        self.config_id = int(config_id)
        self.lane_params = lane_params
        self.tier = tier
        self.horizon_buckets = int(horizon_buckets)
        self.mode = mode
        self.demand_stochastic = bool(demand_stochastic)
        self.on_time_stochastic = bool(on_time_stochastic)
        self._reward_fn: RewardFn = reward_fn or self._default_reward
        self._outcome_sink: OutcomeSink | None = outcome_sink

        if self.horizon_buckets < 1:
            raise ValueError(
                f"horizon_buckets must be >= 1; got {self.horizon_buckets}"
            )

        # PLAN_PRODUCTION enforces determinism via Core's validator.
        if self.mode is TwinMode.PLAN_PRODUCTION:
            assert_plan_production_is_deterministic(
                enable_flags={
                    "demand_stochastic": self.demand_stochastic,
                    "on_time_stochastic": self.on_time_stochastic,
                },
            )

        self._state = _SimulatorState()
        self._reset_called = False

    # ------------------------------------------------------------------
    # RL interface
    # ------------------------------------------------------------------

    def reset(
        self,
        *,
        scenario_seed: int,
        anchor_date: date | None = None,
    ) -> LaneFlowObservation:
        """Initialise the simulator at ``bucket=0`` and return the first
        observation. Idempotent — every call rebuilds state from the
        seed.
        """
        bucket_size = self._bucket_days()
        anchor = anchor_date or date.today()

        envelope = self._generator.generate_envelope(
            tenant_id=self.tenant_id,
            config_id=self.config_id,
            tier=self.tier,
            anchor_date=anchor,
            horizon_buckets=self.horizon_buckets,
        )

        self._state = _SimulatorState(
            bucket=0,
            bucket_start=anchor,
            in_flight=[],
            equipment_available=self.lane_params.initial_equipment,
            dock_queue_depth=0,
            last_action=None,
            recent_otd=deque(maxlen=4),
            recent_cost_per_load=deque(maxlen=4),
            rng=random.Random(scenario_seed),
            cached_envelope=envelope,
        )
        self._reset_called = True

        # Initial observation has zero arrivals "this period" because
        # nothing has been simulated yet — the first step samples
        # the bucket-0 arrivals.
        return LaneFlowObservation(
            transportation_lane_id=lane_series_key(
                self.lane_params.origin_site_id,
                self.lane_params.destination_site_id,
            ),
            period=0,
            in_flight_loads=0,
            arrivals_this_period=0,
            carrier_capacity_remaining=float(self._max_carrier_capacity()),
            equipment_available=self._state.equipment_available,
            dock_queue_depth=0,
            on_time_pct_trailing=1.0,
            cost_per_load_trailing=0.0,
            plan_date=anchor,
            as_of=anchor,
        )

    def step(
        self,
        action: LaneFlowAction,
    ) -> tuple[LaneFlowObservation, LaneFlowReward, bool, dict[str, Any]]:
        """Apply ``action``, advance one bucket, return
        ``(next_obs, reward, done, info)``.
        """
        if not self._reset_called:
            raise RuntimeError(
                "LaneFlowSimulator.step called before reset(); call "
                "reset(scenario_seed=...) first."
            )
        carrier = self._resolve_carrier(action.carrier_id)
        equipment = self._resolve_equipment(action.equipment_kind)
        prev_obs = self._observe_current(action)

        # 1. Sample arrivals (line items) at this bucket from the envelope.
        arrivals = self._sample_arrivals(self._state.bucket)

        # 2. Compute loads-needed from arrivals + this carrier's equipment.
        loads_needed = self._loads_needed(arrivals, equipment)

        # 3. Capacity-bound the dispatch.
        allowed = min(
            loads_needed,
            carrier.capacity_per_bucket,
            self._state.equipment_available,
        )
        unmet = max(0, loads_needed - allowed)

        # 4. Dispatch: equipment leaves origin, loads enter the in-flight queue.
        for load_idx in range(allowed):
            on_time = self._sample_on_time(carrier, action)
            arrival_bucket = self._state.bucket + self.lane_params.transit_buckets
            self._state.in_flight.append(
                _InFlightLoad(
                    arrival_bucket=arrival_bucket,
                    on_time=on_time,
                    decision_id=self._make_decision_id(load_idx),
                    carrier_id=carrier.carrier_id,
                )
            )
        # §3.31 OutcomeEvents — tender_accepted per dispatched load,
        # tender_declined per unmet (capacity-rejected) load. Emitted
        # at decision time so downstream training corpus / BSC reward
        # attribution can correlate with the action that produced them.
        for load_idx in range(allowed):
            self._emit_outcome(
                decision_id=self._make_decision_id(load_idx),
                decision_type="load_dispatch",
                outcome_kind="tender_accepted",
                payload={
                    "carrier_id": carrier.carrier_id,
                    "equipment_kind": equipment.equipment_kind,
                    "bucket": self._state.bucket,
                    "transportation_lane_id": lane_series_key(
                        self.lane_params.origin_site_id,
                        self.lane_params.destination_site_id,
                    ),
                },
            )
        for unmet_idx in range(unmet):
            self._emit_outcome(
                decision_id=self._make_decision_id(allowed + unmet_idx),
                decision_type="load_dispatch",
                outcome_kind="tender_declined",
                payload={
                    "carrier_id": carrier.carrier_id,
                    "reason": "capacity_or_equipment_exhausted",
                    "bucket": self._state.bucket,
                    "transportation_lane_id": lane_series_key(
                        self.lane_params.origin_site_id,
                        self.lane_params.destination_site_id,
                    ),
                },
            )
        self._state.equipment_available -= allowed
        self._state.dock_queue_depth += unmet

        # 5. Resolve in-flight loads scheduled to arrive at the *new* bucket.
        next_bucket = self._state.bucket + 1
        arriving = [l for l in self._state.in_flight if l.arrival_bucket == next_bucket]
        self._state.in_flight = [
            l for l in self._state.in_flight if l.arrival_bucket > next_bucket
        ]
        on_time_count = sum(1 for l in arriving if l.on_time)
        # §3.31 OutcomeEvents — shipment_delivered / shipment_late per
        # arriving load. ``decision_id`` carries the dispatch-side id
        # so consumers can join arrival outcomes back to the dispatch
        # tender that produced them.
        for load in arriving:
            self._emit_outcome(
                decision_id=load.decision_id,
                decision_type="load_dispatch",
                outcome_kind=(
                    "shipment_delivered" if load.on_time else "shipment_late"
                ),
                payload={
                    "carrier_id": load.carrier_id,
                    "arrival_bucket": load.arrival_bucket,
                    "transportation_lane_id": lane_series_key(
                        self.lane_params.origin_site_id,
                        self.lane_params.destination_site_id,
                    ),
                },
            )
        # Equipment returns at destination — Phase 1 collapses dwell to zero,
        # so it's available again next bucket. Phase 2 layers dwell + reposition.
        self._state.equipment_available += len(arriving)

        # 6. Step metrics.
        loads_dispatched = allowed
        cost_total = float(loads_dispatched) * carrier.cost_per_load
        cost_per_load = (
            cost_total / loads_dispatched if loads_dispatched > 0 else 0.0
        )
        otd_step = on_time_count / max(1, len(arriving)) if arriving else 1.0
        self._state.recent_otd.append(otd_step)
        if loads_dispatched > 0:
            self._state.recent_cost_per_load.append(cost_per_load)

        # 7. Build the post-step observation.
        next_obs = self._build_next_observation(
            arrivals=arrivals,
            carrier=carrier,
        )

        # 8. Compute reward.
        info: dict[str, Any] = {
            "loads_needed": loads_needed,
            "loads_dispatched": loads_dispatched,
            "loads_unmet": unmet,
            "loads_arrived": len(arriving),
            "on_time_count": on_time_count,
            "otd_step": otd_step,
            "cost_total": cost_total,
            "cost_per_load": cost_per_load,
            "arrivals_this_period": arrivals,
            "carrier": carrier,
        }
        reward = self._reward_fn(prev_obs, action, info)

        # 9. Advance bucket + done check.
        self._state.bucket += 1
        self._state.last_action = action
        self._state.bucket_start = (
            self._state.bucket_start + timedelta(days=self._bucket_days())
            if self._state.bucket_start is not None
            else None
        )
        done = self._state.bucket >= self.horizon_buckets
        return next_obs, reward, done, info

    # ------------------------------------------------------------------
    # Sampling — gated by TwinMode + stochastic flags
    # ------------------------------------------------------------------

    def _sample_arrivals(self, bucket_idx: int) -> int:
        """Sample line-item arrivals for a bucket from the cached envelope."""
        envelope = self._state.cached_envelope
        # The Phase-1 generator emits one row per (channel × bucket); we
        # find the row matching this channel at this bucket.
        target_date = self._state.bucket_start
        if target_date is None or envelope is None:
            return 0
        for row in envelope.rows:
            if (
                row.bucket_start == target_date
                and row.origin_site_id == self.lane_params.origin_site_id
                and row.destination_site_id == self.lane_params.destination_site_id
                and row.product_id == self.lane_params.product_id
            ):
                # §3.31 ConformalBand: wire-format wrapper around the
                # raw triple. Construction validates p10 <= p50 <= p90;
                # if a producer ever emits an out-of-order envelope,
                # this surfaces here at the simulator boundary instead
                # of silently sampling from a malformed distribution.
                band = ConformalBand(
                    p10=float(row.p10),
                    p50=float(row.p50),
                    p90=float(row.p90),
                    producer_signature=CONFORMAL_BAND_PRODUCER_SIGNATURE,
                )
                return self._realise_envelope_row(band)
        return 0

    def _realise_envelope_row(self, band: ConformalBand) -> int:
        """Map a :class:`ConformalBand` into a realised line-item count.

        - PLAN_PRODUCTION (or ``demand_stochastic=False``): use ``band.p50``.
        - TRAINING with stochasticity: triangular sample on
          ``(band.p10, band.p50, band.p90)``.
        """
        if not self.demand_stochastic or self.mode is TwinMode.PLAN_PRODUCTION:
            return max(0, int(round(band.p50)))
        # Triangular sample using the seeded rng — keeps determinism.
        u = self._state.rng.random()
        # Standard inverse-CDF for triangular(low=p10, mode=p50, high=p90).
        # Avoid degenerate denominators when bands collapse.
        low, mode, high = band.p10, band.p50, band.p90
        if high <= low:
            return max(0, int(round(band.p50)))
        f_mode = (mode - low) / (high - low) if high > low else 0.5
        if u < f_mode:
            value = low + ((u * (high - low) * (mode - low)) ** 0.5)
        else:
            value = high - (((1.0 - u) * (high - low) * (high - mode)) ** 0.5)
        return max(0, int(round(value)))

    def _sample_on_time(
        self, carrier: CarrierProfile, action: LaneFlowAction
    ) -> bool:
        """Decide whether a single dispatched load arrives on time."""
        # Late dispatch shrinks the on-time rate proportionally.
        late_penalty = 0.0
        if action.dispatch_offset_hours > self.DEFAULT_LATE_DISPATCH_HOURS:
            late_penalty = min(
                0.5,
                (action.dispatch_offset_hours - self.DEFAULT_LATE_DISPATCH_HOURS) / 24.0,
            )
        adjusted_rate = max(0.0, carrier.on_time_rate - late_penalty)
        if not self.on_time_stochastic or self.mode is TwinMode.PLAN_PRODUCTION:
            # Deterministic: round to nearest integer fraction; equivalent to
            # treating ``adjusted_rate`` as expected value across many loads.
            return adjusted_rate >= 0.5
        return self._state.rng.random() < adjusted_rate

    # ------------------------------------------------------------------
    # Observation builders
    # ------------------------------------------------------------------

    def _observe_current(self, action: LaneFlowAction) -> LaneFlowObservation:
        """Snapshot of state BEFORE the step's physics runs.

        Used as the (s) in the (s, a, r, s') transition handed to the
        reward function and the trajectory.
        """
        carrier_cap = self._capacity_remaining_for_action(action)
        return LaneFlowObservation(
            transportation_lane_id=lane_series_key(
                self.lane_params.origin_site_id,
                self.lane_params.destination_site_id,
            ),
            period=self._state.bucket,
            in_flight_loads=len(self._state.in_flight),
            arrivals_this_period=0,  # not yet sampled
            carrier_capacity_remaining=carrier_cap,
            equipment_available=self._state.equipment_available,
            dock_queue_depth=self._state.dock_queue_depth,
            on_time_pct_trailing=self._mean(self._state.recent_otd, default=1.0),
            cost_per_load_trailing=self._mean(self._state.recent_cost_per_load, default=0.0),
            plan_date=self._state.bucket_start,
            as_of=self._state.bucket_start,
        )

    def _build_next_observation(
        self,
        *,
        arrivals: int,
        carrier: CarrierProfile,
    ) -> LaneFlowObservation:
        # bucket_start gets advanced after this method; here it still points at
        # the bucket whose arrivals we just sampled.
        next_period = self._state.bucket + 1
        next_date = (
            self._state.bucket_start + timedelta(days=self._bucket_days())
            if self._state.bucket_start is not None
            else None
        )
        return LaneFlowObservation(
            transportation_lane_id=lane_series_key(
                self.lane_params.origin_site_id,
                self.lane_params.destination_site_id,
            ),
            period=next_period,
            in_flight_loads=len(self._state.in_flight),
            arrivals_this_period=arrivals,
            carrier_capacity_remaining=float(carrier.capacity_per_bucket),
            equipment_available=self._state.equipment_available,
            dock_queue_depth=self._state.dock_queue_depth,
            on_time_pct_trailing=self._mean(self._state.recent_otd, default=1.0),
            cost_per_load_trailing=self._mean(self._state.recent_cost_per_load, default=0.0),
            plan_date=next_date,
            as_of=next_date,
        )

    # ------------------------------------------------------------------
    # Reward
    # ------------------------------------------------------------------

    def _default_reward(
        self,
        observation: LaneFlowObservation,
        action: LaneFlowAction,
        ctx: dict[str, Any],
    ) -> LaneFlowReward:
        """BSC-shaped per-step reward.

        Components:
          - ``on_time_score``: maps the step's OTD via baseline /
            target into ``[-1, 1]``.
          - ``cost_per_load``: realised cost per dispatched load.
          - ``dock_utilization``: distance from the dock-utilisation
            target, in ``[0, 1]``.
          - ``equipment_balance``: deviation from initial equipment
            count, normalised by initial.
          - ``override_churn``: 1 when the carrier choice changed
            from the previous action (proxy for the override-churn
            signal used in the live-system reward), 0 otherwise.

        ``total`` is positive when service quality dominates, negative
        when cost / utilisation / churn dominate.
        """
        otd_step = float(ctx.get("otd_step", 1.0))
        loads_arrived = int(ctx.get("loads_arrived", 0))
        cost_per_load_step = float(ctx.get("cost_per_load", 0.0))
        arrivals = int(ctx.get("arrivals_this_period", 0))

        # OTD score: linearly map [baseline, target] → [0, 1]; clip into [-1, 1].
        baseline = self.DEFAULT_OTD_BASELINE
        target = self.DEFAULT_OTD_TARGET
        if loads_arrived == 0:
            on_time_score = 0.0
        else:
            raw = (otd_step - baseline) / (target - baseline) if target != baseline else 0.0
            on_time_score = max(-1.0, min(1.0, raw))

        # Dock utilisation: arrivals vs. dock_capacity_per_bucket; distance from target.
        dock_cap = max(1, self.lane_params.dock_capacity_per_bucket)
        dock_util = arrivals / dock_cap
        util_distance = min(1.0, abs(dock_util - self.DEFAULT_DOCK_UTIL_TARGET))

        # Equipment balance: |E_now - E_init| / E_init.
        e_init = max(1, self.lane_params.initial_equipment)
        e_now = self._state.equipment_available
        equipment_balance = min(1.0, abs(e_now - e_init) / e_init)

        # Churn: 1 if the carrier choice changed since last action.
        prev = self._state.last_action
        churn = 0.0 if prev is None or prev.carrier_id == action.carrier_id else 1.0

        # Combine.
        w = self.DEFAULT_REWARD_WEIGHTS
        cost_norm = (
            cost_per_load_step / self.lane_params.cost_target_per_load
            if cost_per_load_step > 0
            else 0.0
        )
        total = (
            w["otd"] * on_time_score
            - w["cost"] * cost_norm
            - w["dock_util"] * util_distance
            - w["equipment_balance"] * equipment_balance
            - w["churn"] * churn
        )

        return LaneFlowReward(
            total=total,
            on_time_score=on_time_score,
            cost_per_load=cost_per_load_step,
            dock_utilization=util_distance,
            equipment_balance=equipment_balance,
            override_churn=churn,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bucket_days(self) -> int:
        try:
            return _BUCKET_DAYS[self.tier]
        except KeyError as exc:
            raise ValueError(f"Unknown tier {self.tier!r}") from exc

    def _resolve_carrier(self, carrier_id: str) -> CarrierProfile:
        try:
            return self.lane_params.carriers[carrier_id]
        except KeyError as exc:
            raise KeyError(
                f"Unknown carrier_id {carrier_id!r}; known: "
                f"{list(self.lane_params.carriers.keys())}"
            ) from exc

    def _resolve_equipment(self, equipment_kind: str) -> EquipmentProfile:
        try:
            return self.lane_params.equipment_kinds[equipment_kind]
        except KeyError as exc:
            raise KeyError(
                f"Unknown equipment_kind {equipment_kind!r}; known: "
                f"{list(self.lane_params.equipment_kinds.keys())}"
            ) from exc

    def _max_carrier_capacity(self) -> int:
        return max(c.capacity_per_bucket for c in self.lane_params.carriers.values())

    def _capacity_remaining_for_action(self, action: LaneFlowAction) -> float:
        try:
            carrier = self.lane_params.carriers[action.carrier_id]
            return float(carrier.capacity_per_bucket)
        except KeyError:
            return float(self._max_carrier_capacity())

    @staticmethod
    def _loads_needed(arrivals: int, equipment: EquipmentProfile) -> int:
        if arrivals <= 0:
            return 0
        # Round up — a partial-load truck still counts as one truck.
        from math import ceil

        return int(ceil(arrivals / equipment.load_capacity_units))

    @staticmethod
    def _mean(values: deque[float], default: float = 0.0) -> float:
        if not values:
            return default
        return sum(values) / len(values)

    # ------------------------------------------------------------------
    # §3.31 OutcomeEvent emission
    # ------------------------------------------------------------------

    def _make_decision_id(self, load_idx: int) -> str:
        """Stable decision-id for a load dispatched (or denied) in the
        current bucket. Joins arrival outcomes back to dispatch tenders.
        """
        return (
            f"tenant={self.tenant_id}|config={self.config_id}|"
            f"lane={self.lane_params.origin_site_id}->"
            f"{self.lane_params.destination_site_id}|"
            f"product={self.lane_params.product_id}|"
            f"bucket={self._state.bucket}|load={load_idx}"
        )

    def _emit_outcome(
        self,
        *,
        decision_id: str,
        decision_type: str,
        outcome_kind: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit one :class:`OutcomeEvent` to the registered sink.

        Silent no-op when no sink is registered — the simulator is
        usable headless (training-without-collection) and only pays
        the construction cost of OutcomeEvent when a sink will read
        it. Sink exceptions propagate; if a sink can fail the caller
        wraps it.
        """
        if self._outcome_sink is None:
            return
        event = OutcomeEvent.now(
            decision_id=decision_id,
            decision_type=decision_type,
            outcome_kind=outcome_kind,
            payload=payload,
            tenant_id=self.tenant_id,
            producer=OUTCOME_PRODUCER_SIGNATURE,
        )
        self._outcome_sink(event)


__all__ = [
    "CarrierProfile",
    "CONFORMAL_BAND_PRODUCER_SIGNATURE",
    "EquipmentProfile",
    "LaneFlowSimulator",
    "LanePhysicsParams",
    "OUTCOME_PRODUCER_SIGNATURE",
    "OutcomeSink",
    "RewardFn",
]
