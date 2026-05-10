"""Tracking Event Generator physics — model §4.8 of TMS_TWIN_PHYSICS_DESIGN.md.

**Question this model answers:** for a dispatched shipment, which
tracking pings does the carrier emit, when, and with what jitter?
Some events are dropped — which ones?

This is the per-shipment event-stream synthesizer for the
**ShipmentTracking** TRM. The TRM trains on tracking-event freshness
+ ETA-recompute signals; without realistic event timing it imitates
the heuristic teacher's "no news is bad news" escalation rule
verbatim.

**Phase-1 scope notes:**

- **Ships as a standalone evaluator**, not as a step inside
  `LaneFlowSimulator`. Tracking event generation is a per-shipment
  time series in continuous time; the lane-flow simulator runs in
  discrete buckets at TACTICAL=weekly / EXECUTION=daily granularity.
  Embedding hourly tracking pings in those buckets either explodes
  the simulator state (per-event tick state) or collapses the
  signal (one event per bucket = same as no model). Phase-1 keeps
  the model addressable from the TRM directly.
- **One-shot draw per shipment.** The model takes a transit window
  and emits the full event sequence in one call. The TRM consumes
  the sequence as a feature window; stale-tracking detection is a
  reward-side concern, not a generation concern.
- **PLAN_PRODUCTION mode** disables stochasticity per the twin
  invariant. Events are emitted on a uniform deterministic schedule
  (every ``1 / rate_per_hour`` hours) with no jitter, no drops.

**Why this matters for training data.** With a static "every
4 hours, exactly" event schedule, ShipmentTracking can't learn:

- **When stale-tracking is meaningful vs. routine.** Real carriers
  have variable cadence; a 6-hour gap from a premium carrier (whose
  baseline is 4h) is a weak signal, but the same 6-hour gap from a
  budget carrier (12h baseline) is well within normal. Without
  jitter + drop noise the policy treats every gap identically.
- **When to escalate.** The reward signal is shaped by missed-event
  detection vs. the carrier's true cadence; without realistic
  drops the policy never sees the cost of false positives.

**Calibration source (deferred to PR-6 of TWIN_REWRITE_PLAN.md):**
historical ``TrackingEvent`` density per carrier × lane × season,
plus ``TrackingEvent.expected_at`` vs ``TrackingEvent.received_at``
delta to fit the jitter distribution. Phase-2 fits per-carrier
``λ`` from EDI 214 / API tracking history.

**Bootstrap prior** (this PR — design-doc §4.8 numbers):

- ``λ_premium = 1 event / 4 hours`` (premium carriers — major LTL
  + large parcel networks + premium-tier truckload brokers with
  EDI 214 + ELD geofencing)
- ``λ_budget = 1 event / 12 hours`` (smaller / spot carriers
  with weaker tracking instrumentation)
- Jitter: ``N(0, 30 minutes)`` around the expected event time —
  the carrier might be in a dead zone, in a meeting, or just
  late to ping
- Drop probability: ``p_drop = 0.02`` (2 % of expected events
  are missing — typically because the GPS unit's offline or
  the carrier's hand-off-the-trailer protocol broke)
"""
from __future__ import annotations

import enum
import math
import random
from dataclasses import dataclass, field
from typing import Any


# ── Public types ─────────────────────────────────────────────────────


class CarrierTrackingTier(str, enum.Enum):
    """Per-carrier tracking-quality tier.

    Drives the bootstrap-prior event rate. Phase-2 calibration
    replaces this categorical with a per-carrier fitted λ; this
    enum stays as a fallback when no fitted value is available.
    """

    PREMIUM = "premium"
    """λ = 1 event / 4 hours. Major LTL + large parcel + premium TL
    with EDI 214 + ELD geofencing."""
    BUDGET = "budget"
    """λ = 1 event / 12 hours. Smaller / spot carriers with weaker
    tracking instrumentation."""


@dataclass(frozen=True)
class TrackingContext:
    """Per-shipment features the tracking-event distribution depends on.

    Phase-1 surfaces the four features the bootstrap prior consumes:
    carrier identity (for telemetry), tracking tier (drives base
    rate), transit window length (defines how many events are
    sampled), and an optional override of ``λ`` for ablation /
    Phase-2 calibration overrides.
    """

    carrier_id: str
    tracking_tier: CarrierTrackingTier = CarrierTrackingTier.PREMIUM
    transit_hours: float = 48.0
    """Total transit window in hours. The model emits events spread
    across ``[0, transit_hours]``. Most simulator callers will pass
    ``simulator_bucket_hours × transit_buckets``."""
    rate_per_hour_override: float | None = None
    """When set, used as the Poisson rate instead of the tier
    default. Phase-2 calibration injects per-carrier fitted rates
    here."""

    def __post_init__(self) -> None:
        if not self.carrier_id:
            raise ValueError("carrier_id must be non-empty")
        if self.transit_hours <= 0:
            raise ValueError(
                f"transit_hours must be > 0; got {self.transit_hours}"
            )
        if self.rate_per_hour_override is not None and self.rate_per_hour_override <= 0:
            raise ValueError(
                "rate_per_hour_override must be > 0 when set; got "
                f"{self.rate_per_hour_override}"
            )


@dataclass(frozen=True)
class TrackingEvent:
    """One emitted tracking ping.

    ``location_pct`` is a [0, 1] fraction along the lane; consumers
    (ShipmentTracking) interpolate against the lane's geometry to
    surface a "last known location" lat/lon for UI / ETA recompute.
    """

    emitted_at_hours: float
    """Hours after dispatch the event was emitted (includes jitter)."""
    expected_at_hours: float
    """Hours after dispatch the event was *expected* to arrive
    (the un-jittered grid point). Used by stale-tracking detectors."""
    location_pct: float
    """Carrier's progress along the lane at emission time, in [0, 1]."""


@dataclass(frozen=True)
class TrackingOutcome:
    """Per-shipment tracking-event sequence.

    ``events`` is the realised, drops-removed sequence the consumer
    sees. ``expected_count`` is what the schedule called for; the
    delta tells consumers how many events were dropped this
    shipment.
    """

    events: tuple[TrackingEvent, ...]
    expected_count: int
    """Number of grid events scheduled before drops."""
    rate_per_hour: float
    """λ used for this draw (after override / tier resolution).
    Surfaced for telemetry."""

    @property
    def drop_count(self) -> int:
        return self.expected_count - len(self.events)


# ── Parameters ───────────────────────────────────────────────────────


_TIER_RATES: dict[CarrierTrackingTier, float] = {
    CarrierTrackingTier.PREMIUM: 1.0 / 4.0,   # 0.25 events/hour
    CarrierTrackingTier.BUDGET: 1.0 / 12.0,   # 0.0833 events/hour
}


@dataclass
class TrackingEventParams:
    """Bootstrap-prior parameters for the tracking-event generator.

    Default values fixed by [§4.8 of the design doc](../../../../../docs/TMS_TWIN_PHYSICS_DESIGN.md);
    construct with overrides only when fitting against tenant
    history (PR-6) or when running ablation experiments.
    """

    tier_rates_per_hour: dict[CarrierTrackingTier, float] = field(
        default_factory=lambda: dict(_TIER_RATES)
    )
    jitter_std_minutes: float = 30.0
    """Standard deviation of the per-event jitter, in minutes.
    Default 30 min per design doc."""
    p_drop: float = 0.02
    """Probability each expected event is missing. Default 2 %
    per design doc."""
    version: str = "phase1-bootstrap-2026-05-03"

    def __post_init__(self) -> None:
        if not self.tier_rates_per_hour:
            raise ValueError("tier_rates_per_hour must be non-empty")
        for tier, rate in self.tier_rates_per_hour.items():
            if rate <= 0:
                raise ValueError(
                    f"tier_rates_per_hour[{tier}] must be > 0; got {rate}"
                )
        if self.jitter_std_minutes < 0:
            raise ValueError(
                f"jitter_std_minutes must be >= 0; got {self.jitter_std_minutes}"
            )
        if not (0.0 <= self.p_drop < 1.0):
            raise ValueError(
                f"p_drop must be in [0, 1); got {self.p_drop}"
            )


# ── Model ────────────────────────────────────────────────────────────


class TrackingEventModel:
    """Bootstrap-prior tracking-event-stream physics.

    Lifecycle mirrors the other §4 physics models:

    >>> from app.services.digital_twin.physics import (
    ...     TrackingEventModel, TrackingEventParams,
    ...     TrackingContext, CarrierTrackingTier,
    ... )
    >>> model = TrackingEventModel(TrackingEventParams())
    >>> model.reset(scenario_seed=42)
    >>> ctx = TrackingContext(
    ...     carrier_id="carrier:fedex",
    ...     tracking_tier=CarrierTrackingTier.PREMIUM,
    ...     transit_hours=48,
    ... )
    >>> outcome = model.step(ctx)
    >>> outcome.expected_count > 0
    True

    One-shot per shipment: ``step(ctx)`` returns the full event
    sequence for the transit window. The TRM consumes the sequence
    as features.

    PLAN_PRODUCTION mode emits events on a deterministic uniform
    grid with no jitter and no drops — the plan-of-record uses
    expected event timestamps for ETA computation, not stochastic
    realisations.
    """

    def __init__(self, params: TrackingEventParams | None = None) -> None:
        self.params = params or TrackingEventParams()
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
        context: TrackingContext,
        *,
        t: int | None = None,  # noqa: ARG002 — protocol-level param
    ) -> TrackingOutcome:
        if not self._reset_called:
            raise RuntimeError("TrackingEventModel.step called before reset()")

        # 1. Resolve λ.
        rate_per_hour = self._resolve_rate(context)

        # 2. Build the expected event grid.
        # Use a regular grid at intervals of 1/λ rather than a Poisson
        # process — fixed cadence is what real carrier APIs report
        # against ("scheduled ping every N hours"). The Poisson process
        # appears in literature as the source of timing noise; real
        # carrier instrumentation is grid-based with jitter.
        interval_hours = 1.0 / rate_per_hour
        # The first event lands at interval_hours after dispatch; the
        # last at the largest multiple of interval_hours <= transit_hours.
        n_expected = int(math.floor(context.transit_hours / interval_hours))
        if n_expected == 0:
            return TrackingOutcome(
                events=(),
                expected_count=0,
                rate_per_hour=rate_per_hour,
            )

        is_pp = self._is_plan_production_mode()
        events: list[TrackingEvent] = []
        for i in range(1, n_expected + 1):
            expected_at = i * interval_hours
            # Drop draw — skip in PLAN_PRODUCTION.
            if not is_pp and self._rng.random() < self.params.p_drop:
                continue
            # Jitter — zero in PLAN_PRODUCTION; gaussian otherwise.
            if is_pp or self.params.jitter_std_minutes <= 0:
                emitted_at = expected_at
            else:
                jitter_hours = self._rng.gauss(
                    0.0, self.params.jitter_std_minutes / 60.0
                )
                emitted_at = max(0.0, expected_at + jitter_hours)
            location_pct = min(1.0, expected_at / context.transit_hours)
            events.append(
                TrackingEvent(
                    emitted_at_hours=emitted_at,
                    expected_at_hours=expected_at,
                    location_pct=location_pct,
                )
            )

        return TrackingOutcome(
            events=tuple(events),
            expected_count=n_expected,
            rate_per_hour=rate_per_hour,
        )

    # ------------------------------------------------------------------

    def _resolve_rate(self, context: TrackingContext) -> float:
        if context.rate_per_hour_override is not None:
            return float(context.rate_per_hour_override)
        try:
            return self.params.tier_rates_per_hour[context.tracking_tier]
        except KeyError as exc:
            raise KeyError(
                f"No tier_rates_per_hour entry for {context.tracking_tier!r}; "
                f"known tiers: {list(self.params.tier_rates_per_hour.keys())}"
            ) from exc

    def _is_plan_production_mode(self) -> bool:
        if self._twin_mode is None:
            return False
        v = getattr(self._twin_mode, "value", self._twin_mode)
        return str(v).lower() == "plan_production"


__all__ = [
    "CarrierTrackingTier",
    "TrackingContext",
    "TrackingEvent",
    "TrackingEventModel",
    "TrackingEventParams",
    "TrackingOutcome",
]
