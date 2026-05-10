"""Intermodal Ramp physics — model §4.7 of TMS_TWIN_PHYSICS_DESIGN.md.

**Question this model answers:** can a shipment use intermodal? Does
the origin ramp accept the tender? What's the rail transit time
(with conformal P10/P50/P90)? What's the all-in cost (drayage +
ramp fees + rail line-haul)?

This is the mode-economics evaluator for the **IntermodalTransfer**
TRM. The TRM scores ``intermodal-vs-truck`` on a per-shipment basis;
without this model it imitates the heuristic teacher (which today
keys off a static lane-cost lookup with no acceptance probability,
no transit variability, and no congestion modulation).

**Phase-1 scope notes:**

- **Ships as a standalone evaluator**, not as a step inside
  `LaneFlowSimulator`. The simulator is single-channel
  (one origin × destination × product) and intermodal involves
  two ramps + a rail leg between them — multi-leg geometry the
  Phase-1 simulator deliberately doesn't model. IntermodalTransfer
  TRM consumes the model directly to score mode options at
  decision time; a multi-mode simulator extension is a follow-up
  PR (out of PR-3.x scope).
- **No capacity-decrement state.** §4.7's `capacity_remaining`
  per ramp × bucket is Phase-2 (calibration drives it; Phase-1
  has no realised history to anchor capacity to). The model
  surfaces ``congestion_level`` as an input feature so the caller
  can inject a scenario-driven congestion AR(1).
- **PLAN_PRODUCTION mode** disables stochasticity per the
  twin invariant. Acceptance becomes deterministic (threshold at
  ``p_ramp_accept >= 0.5``), rail transit returns the mean.

**Why this matters for training data.** With a static lane-cost
lookup, IntermodalTransfer cannot learn:

- **When to wait for a less congested ramp** — without congestion
  modulation, the policy never sees the tradeoff between accepting
  a tender today vs. deferring 1–2 buckets.
- **When the rail-transit tail bites SLA** — without conformal
  bands, the policy treats every intermodal option as
  "1.5 × truck transit ± 0" and over-commits on tight deadlines.
- **When all-in cost crosses the truck-spot break-even** — without
  the four-component cost decomposition, drayage-heavy lanes look
  artificially cheap.

**Calibration source (deferred to PR-6 of TWIN_REWRITE_PLAN.md):**
vendor-specific BNSF / UP / CSX / NS rate APIs when populated; otherwise
derive from accepted-tender prices in an ``IntermodalRate`` table when
the data model lands. Rail transit calibration: EDI 322 (rail
ETA / actual) joined to ``Shipment.dispatch_at`` aggregated by
(origin_ramp × dest_ramp × season).

**Bootstrap prior** (this PR — design-doc §4.7 numbers; bumping any
of these constitutes a re-fit and should bump
``IntermodalRampParams.version``):

- ``p_ramp_accept = 0.92`` baseline
  (modulated down by ``1 - congestion_level``; hard zero if
  ``accepting_today=False``)
- Rail transit = ``truck_transit_buckets × rail_to_truck_ratio``
  (default 1.5) with σ/μ = 0.10 (lognormal noise — fatter tail
  than truck since service disruptions on a single rail link
  cascade further than a truck reroute)
- Drayage rate: ``$4.50/mile`` within a 50-mile radius;
  beyond 50 miles, intermodal is generally not economical
  but the model still computes it for completeness
- Ramp fee: ``$50/container`` per ramp (origin + destination)
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any


# ── Public types ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class IntermodalContext:
    """Per-shipment features the intermodal-evaluator distribution depends on.

    Phase-1 surfaces the seven features the bootstrap prior consumes:
    ramp identity (origin + destination), accepting flag, congestion
    level (0–1), per-leg distances, day-of-year (for rail-transit
    season modulation), truck-equivalent transit (so rail = truck × ratio).
    """

    origin_ramp_id: str
    """Origin intermodal ramp (canonical AWS SC ``site.id`` String(100))."""
    destination_ramp_id: str
    """Destination intermodal ramp."""
    accepting_today: bool = True
    """Operational status of the origin ramp. ``False`` typically comes
    from a scenario disruption (rail derailment, wildfire smoke, port
    congestion); the model returns ``accepted=False`` immediately."""
    congestion_level: float = 0.0
    """0 = clear, 1 = totally congested (every tender refused). Comes
    from a scenario-driven AR(1) on rail-network OTRI proxy in
    Phase 2; default 0 means baseline."""
    drayage_origin_miles: float = 25.0
    """Distance from the shipper to the origin ramp. > 50 miles is
    typically a mode-mismatch candidate (truck-only is usually
    cheaper); the model still evaluates it for completeness."""
    drayage_destination_miles: float = 25.0
    """Distance from the destination ramp to the consignee."""
    truck_transit_buckets: int = 2
    """Truck-equivalent transit time on this lane in simulator ticks.
    Used as the bootstrap mean for rail transit (rail = truck × 1.5
    by default). Phase-2 calibration estimates rail transit directly
    from EDI 322 history."""
    day_of_year: int = 0
    """1-366 (or 0 for season-agnostic). Drives season modulation on
    rail transit."""
    equipment_kind: str = "container_40hc"
    """One of {container_20, container_40hc, container_53, ...}.
    Surfaces in diagnostic ``info`` only in Phase 1; Phase 2 will
    bias rail transit + ramp fees by container type."""

    def __post_init__(self) -> None:
        if not self.origin_ramp_id:
            raise ValueError("origin_ramp_id must be non-empty")
        if not self.destination_ramp_id:
            raise ValueError("destination_ramp_id must be non-empty")
        if not (0.0 <= self.congestion_level <= 1.0):
            raise ValueError(
                f"congestion_level must be in [0, 1]; got {self.congestion_level}"
            )
        if self.drayage_origin_miles < 0:
            raise ValueError(
                "drayage_origin_miles must be >= 0; got "
                f"{self.drayage_origin_miles}"
            )
        if self.drayage_destination_miles < 0:
            raise ValueError(
                "drayage_destination_miles must be >= 0; got "
                f"{self.drayage_destination_miles}"
            )
        if self.truck_transit_buckets < 1:
            raise ValueError(
                "truck_transit_buckets must be >= 1; got "
                f"{self.truck_transit_buckets}"
            )
        if not (0 <= self.day_of_year <= 366):
            raise ValueError(
                f"day_of_year must be in [0, 366]; got {self.day_of_year}"
            )


@dataclass(frozen=True)
class IntermodalCostBreakdown:
    """Four-component cost breakdown for an intermodal tender.

    Surfaces alongside the all-in cost so the TRM can learn which
    leg dominates (drayage-heavy lanes look different from
    rail-heavy lanes; modelling them collapsed loses signal).
    """

    drayage_origin: float
    ramp_fee_origin: float
    rail: float
    ramp_fee_destination: float
    drayage_destination: float

    @property
    def all_in(self) -> float:
        return (
            self.drayage_origin
            + self.ramp_fee_origin
            + self.rail
            + self.ramp_fee_destination
            + self.drayage_destination
        )


@dataclass(frozen=True)
class IntermodalOutcome:
    """Per-tender intermodal-evaluation result.

    On accept: rail-transit + cost fields populated. On reject:
    only ``accepted`` and ``p_accept`` are meaningful — caller
    falls back to truck.
    """

    accepted: bool
    p_accept: float
    """Bootstrap-prior acceptance probability. Surfaced for telemetry
    and calibration-drift monitoring. Constant in Phase 1 modulo the
    ``congestion_level`` and ``accepting_today`` inputs; varies per
    (ramp × season) cell after PR-6 calibration."""
    reason_code: str
    """Short tag explaining the decision: ``accepted``,
    ``rejected:congested``, ``rejected:closed``, or ``rejected:bernoulli``
    (drew below the threshold). Useful for the training-corpus
    consumer to disambiguate why a tender was lost."""
    rail_transit_realised_buckets: int = 0
    rail_transit_mean_buckets: float = 0.0
    rail_transit_p10_buckets: float = 0.0
    rail_transit_p90_buckets: float = 0.0
    cost: IntermodalCostBreakdown | None = None
    """``None`` when ``accepted=False``."""


# ── Parameters ───────────────────────────────────────────────────────


@dataclass
class IntermodalRampParams:
    """Bootstrap-prior parameters for the intermodal-ramp model.

    Default values fixed by [§4.7 of the design doc](../../../../../docs/TMS_TWIN_PHYSICS_DESIGN.md);
    construct with overrides only when fitting against tenant history
    (PR-6) or when running ablation experiments.
    """

    base_p_accept: float = 0.92
    """Acceptance probability at zero congestion."""
    rail_to_truck_ratio: float = 1.5
    """Rail transit / truck transit. 1.5 is the design-doc baseline:
    rail is slower because of the dwell at each ramp + the line-haul
    speed + the schedule cadence (rail typically runs 1–2 trains per
    direction per day on most corridors)."""
    rail_sigma_ratio: float = 0.10
    """σ/μ for the rail-transit lognormal. 0.10 is the design-doc
    baseline. Higher → fatter tails."""
    rail_season_amplitude: float = 0.05
    """Peak season multiplier (±5 %) on rail transit. Smaller than
    truck (±10 %) because rail is more weather-resilient — it
    takes a hurricane / blizzard / flood to slow rail materially."""
    drayage_rate_per_mile: float = 4.50
    """USD per mile for drayage, both ends."""
    ramp_fee_per_container: float = 50.0
    """USD per container, charged at both ramps."""
    rail_rate_per_bucket_per_container: float = 200.0
    """USD per simulator-bucket of rail line-haul per container.
    Coarse Phase-1 unit-cost model: a 7-day TACTICAL bucket × $200
    ≈ $1,400 for a typical 1-bucket leg, $2,100 for the 1.5-bucket
    rail expansion. Real rail rates vary by corridor + commodity;
    Phase-2 fits per (origin_ramp × dest_ramp × commodity_class)."""
    version: str = "phase1-bootstrap-2026-05-03"

    def __post_init__(self) -> None:
        if not (0.0 <= self.base_p_accept <= 1.0):
            raise ValueError(
                f"base_p_accept must be in [0, 1]; got {self.base_p_accept}"
            )
        if self.rail_to_truck_ratio <= 0:
            raise ValueError(
                "rail_to_truck_ratio must be > 0; got "
                f"{self.rail_to_truck_ratio}"
            )
        if self.rail_sigma_ratio <= 0:
            raise ValueError(
                f"rail_sigma_ratio must be > 0; got {self.rail_sigma_ratio}"
            )
        if self.rail_season_amplitude < 0:
            raise ValueError(
                "rail_season_amplitude must be >= 0; got "
                f"{self.rail_season_amplitude}"
            )
        if self.drayage_rate_per_mile < 0:
            raise ValueError(
                "drayage_rate_per_mile must be >= 0; got "
                f"{self.drayage_rate_per_mile}"
            )
        if self.ramp_fee_per_container < 0:
            raise ValueError(
                "ramp_fee_per_container must be >= 0; got "
                f"{self.ramp_fee_per_container}"
            )
        if self.rail_rate_per_bucket_per_container < 0:
            raise ValueError(
                "rail_rate_per_bucket_per_container must be >= 0; got "
                f"{self.rail_rate_per_bucket_per_container}"
            )


# ── Model ────────────────────────────────────────────────────────────


class IntermodalRampModel:
    """Bootstrap-prior intermodal-ramp + rail-transit + all-in-cost physics.

    Lifecycle mirrors the other §4 physics models (carrier acceptance,
    lane transit, exception generator):

    >>> from app.services.digital_twin.physics import (
    ...     IntermodalRampModel, IntermodalRampParams, IntermodalContext,
    ... )
    >>> model = IntermodalRampModel(IntermodalRampParams())
    >>> model.reset(scenario_seed=42)
    >>> ctx = IntermodalContext(
    ...     origin_ramp_id="ramp:chicago",
    ...     destination_ramp_id="ramp:los_angeles",
    ...     truck_transit_buckets=4,
    ...     drayage_origin_miles=15,
    ...     drayage_destination_miles=20,
    ... )
    >>> outcome = model.step(ctx)
    >>> isinstance(outcome.accepted, bool)
    True

    Single-shot per evaluation: the IntermodalTransfer TRM calls
    ``step(ctx)`` once per intermodal option it's scoring. PLAN_PRODUCTION
    mode returns deterministic accept/reject thresholded at
    ``p_accept >= 0.5`` and uses the mean rail transit (no lognormal draw).
    """

    def __init__(self, params: IntermodalRampParams | None = None) -> None:
        self.params = params or IntermodalRampParams()
        self._rng: random.Random = random.Random()
        self._twin_mode: Any = None
        self._reset_called = False

    def reset(
        self,
        *,
        scenario_seed: int = 42,
        twin_mode: Any = None,
    ) -> None:
        self._rng = random.Random(scenario_seed)
        self._twin_mode = twin_mode
        self._reset_called = True

    def step(
        self,
        context: IntermodalContext,
        *,
        t: int | None = None,  # noqa: ARG002 — protocol-level param
    ) -> IntermodalOutcome:
        if not self._reset_called:
            raise RuntimeError("IntermodalRampModel.step called before reset()")

        # 1. Acceptance probability.
        if not context.accepting_today:
            return IntermodalOutcome(
                accepted=False,
                p_accept=0.0,
                reason_code="rejected:closed",
            )
        p_accept = self.params.base_p_accept * (1.0 - context.congestion_level)
        p_accept = max(0.0, min(1.0, p_accept))

        is_pp = self._is_plan_production_mode()
        if is_pp:
            accepted = p_accept >= 0.5
        else:
            accepted = self._rng.random() < p_accept

        if not accepted:
            reason = (
                "rejected:congested"
                if context.congestion_level > 0.5
                else "rejected:bernoulli"
            )
            return IntermodalOutcome(
                accepted=False,
                p_accept=p_accept,
                reason_code=reason,
            )

        # 2. Rail transit.
        rail_mean, rail_p10, rail_p90, rail_realised = self._sample_rail_transit(
            truck_transit=context.truck_transit_buckets,
            day_of_year=context.day_of_year,
            is_plan_production=is_pp,
        )

        # 3. Cost decomposition.
        cost = self._all_in_cost(context, rail_realised)

        return IntermodalOutcome(
            accepted=True,
            p_accept=p_accept,
            reason_code="accepted",
            rail_transit_realised_buckets=rail_realised,
            rail_transit_mean_buckets=rail_mean,
            rail_transit_p10_buckets=rail_p10,
            rail_transit_p90_buckets=rail_p90,
            cost=cost,
        )

    # ------------------------------------------------------------------
    # Rail transit
    # ------------------------------------------------------------------

    def _sample_rail_transit(
        self,
        *,
        truck_transit: int,
        day_of_year: int,
        is_plan_production: bool,
    ) -> tuple[float, float, float, int]:
        """Return ``(mean_buckets, p10, p90, realised_buckets)`` for rail.

        Mirrors LaneTransitModel's analytical lognormal — preserves the
        same conformal-band shape so consumers can read either model
        the same way.
        """
        # Season factor: sin around day 15 (winter peak) — same shape as
        # LaneTransitModel. Smaller amplitude (±5 % vs ±10 %) per design doc.
        if day_of_year > 0 and self.params.rail_season_amplitude > 0:
            phase = 2.0 * math.pi * (day_of_year - 15) / 365.0
            season_factor = self.params.rail_season_amplitude * math.cos(phase)
        else:
            season_factor = 0.0

        mean = (
            float(truck_transit)
            * self.params.rail_to_truck_ratio
            * (1.0 + season_factor)
        )
        sigma = self.params.rail_sigma_ratio * mean

        if is_plan_production:
            realised = max(1, int(round(mean)))
        elif mean > 0 and sigma > 0:
            sigma_log = math.sqrt(math.log(1.0 + (sigma / mean) ** 2))
            mu_log = math.log(mean) - 0.5 * sigma_log ** 2
            draw = math.exp(self._rng.gauss(mu_log, sigma_log))
            realised = max(1, int(round(draw)))
        else:
            realised = max(1, int(round(mean)))

        if mean > 0:
            sigma_log = math.sqrt(math.log(1.0 + (sigma / mean) ** 2))
            mu_log = math.log(mean) - 0.5 * sigma_log ** 2
            p10 = math.exp(mu_log - 1.2816 * sigma_log)
            p90 = math.exp(mu_log + 1.2816 * sigma_log)
        else:
            p10 = mean
            p90 = mean

        return mean, p10, p90, realised

    # ------------------------------------------------------------------
    # Cost decomposition
    # ------------------------------------------------------------------

    def _all_in_cost(
        self,
        context: IntermodalContext,
        rail_realised_buckets: int,
    ) -> IntermodalCostBreakdown:
        return IntermodalCostBreakdown(
            drayage_origin=(
                context.drayage_origin_miles * self.params.drayage_rate_per_mile
            ),
            ramp_fee_origin=self.params.ramp_fee_per_container,
            rail=(
                float(rail_realised_buckets)
                * self.params.rail_rate_per_bucket_per_container
            ),
            ramp_fee_destination=self.params.ramp_fee_per_container,
            drayage_destination=(
                context.drayage_destination_miles
                * self.params.drayage_rate_per_mile
            ),
        )

    # ------------------------------------------------------------------

    def _is_plan_production_mode(self) -> bool:
        if self._twin_mode is None:
            return False
        v = getattr(self._twin_mode, "value", self._twin_mode)
        return str(v).lower() == "plan_production"


__all__ = [
    "IntermodalContext",
    "IntermodalCostBreakdown",
    "IntermodalOutcome",
    "IntermodalRampModel",
    "IntermodalRampParams",
]
