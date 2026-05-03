"""Spot-Rate Market physics — model §4.5 of TMS_TWIN_PHYSICS_DESIGN.md.

**Question this model answers:** what's the current spot rate on
lane L, and how tight is the market?

State per lane × bucket: ``spot_rate`` ($/mile) and ``tightness``
(0–1; OTRI proxy: outbound load count / outbound truck count).

Transition (per §4.5):

    spot_rate[t]  = β · spot_rate[t-1]
                    + (1-β) · (contract_rate × (1 + κ · tightness[t]))
                    + ε
    tightness[t]  = α · tightness[t-1]
                    + (1-α) · season_factor[t]
                    + shock[t]

with ``α = 0.85``, ``β = 0.7``, ``κ = 0.4``, and ``ε`` drawn from
``Normal(0, σ_ε)`` where ``σ_ε = 0.05 × contract_rate``.

Shocks come from scenario disruption injection
(``DisruptionKind.CARRIER_STRIKE``, ``DisruptionKind.WEATHER_EVENT``);
the model accepts an optional per-step shock value via the request
context.

Season factor: sinusoidal seasonal pattern. Q4 retail surge is the
peak, Q1 trough, produce-season Q2 secondary peak, baseline Q3.

Bootstrap initial state: ``spot_rate = contract_rate``,
``tightness = 0.5``.

**Dependent TRMs** (§4.5):
- BrokerRouting (premium decision: pay spot vs hold contracted slot)
- CapacityBuffer (buffer size scales with tightness)
- IntermodalTransfer (truck-vs-intermodal economics depend on spot)

The model also feeds the existing ``CarrierAcceptance`` model
(PR-3.A) — its ``market_tightness`` input is what this model
produces dynamically. Until they're plumbed together (deferred to
PR-3.G or later integration), the simulator continues to use the
constant ``scenario_market_tightness`` it already accepts.

**TwinMode discipline.** PLAN_PRODUCTION → no shocks, no ε, returns
the deterministic recursion (single seasonal trajectory).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SpotRateContext:
    """Per-step features the spot-rate transition consumes."""

    contract_rate: float
    """Tenant's contracted carrier rate ($/mile or $/load — units
    consistent with whatever the consumer reads). Anchors the
    long-run mean of the spot rate."""
    day_of_year: int = 1
    """1-365 inclusive. Drives ``season_factor[t]`` in the
    tightness transition. Default 1 (Jan 1) gives a Q1-trough start
    if you forget to thread real time through."""
    shock_tightness: float = 0.0
    """Additive shock to tightness this step. Comes from scenario
    DisruptionKind.CARRIER_STRIKE / WEATHER_EVENT injection. 0 =
    no shock; positive shock raises tightness (capacity scarce);
    typical scenario shocks ∈ [0.1, 0.4]."""

    def __post_init__(self) -> None:
        if self.contract_rate <= 0:
            raise ValueError(
                f"contract_rate must be > 0; got {self.contract_rate}"
            )
        if not (0 <= self.day_of_year <= 366):
            raise ValueError(
                f"day_of_year must be in [0, 366]; got {self.day_of_year}"
            )


@dataclass(frozen=True)
class SpotRateOutcome:
    """Per-step result. The simulator can stamp these onto outcome
    events or surface them as observation features."""

    spot_rate: float
    tightness: float
    season_factor: float
    epsilon: float
    """The realised noise term applied to spot_rate this step. Useful
    diagnostic for downstream reward attribution."""


@dataclass
class SpotRateParams:
    """AR(1) parameters per §4.5."""

    alpha: float = 0.85
    """tightness AR(1) coefficient."""
    beta: float = 0.7
    """spot_rate AR(1) coefficient."""
    kappa: float = 0.4
    """tightness → spot_rate elasticity."""
    sigma_epsilon_ratio: float = 0.05
    """ε ~ Normal(0, sigma_epsilon_ratio × contract_rate)."""
    initial_tightness: float = 0.5
    season_amplitude: float = 0.10
    """Baseline ±0.10 seasonal swing on tightness. Higher amplitude
    increases peak/trough contrast — tune per industry."""
    version: str = "phase1-bootstrap-2026-05-03"

    def __post_init__(self) -> None:
        for name, val in [
            ("alpha", self.alpha),
            ("beta", self.beta),
        ]:
            if not (0.0 <= val <= 1.0):
                raise ValueError(
                    f"{name} must be in [0, 1]; got {val}"
                )
        if self.kappa < 0:
            raise ValueError(f"kappa must be >= 0; got {self.kappa}")
        if self.sigma_epsilon_ratio < 0:
            raise ValueError(
                "sigma_epsilon_ratio must be >= 0; got "
                f"{self.sigma_epsilon_ratio}"
            )
        if not (0.0 <= self.initial_tightness <= 1.0):
            raise ValueError(
                "initial_tightness must be in [0, 1]; got "
                f"{self.initial_tightness}"
            )


class SpotRateModel:
    """AR(1) spot-rate-and-tightness simulator.

    Stateful — carries ``spot_rate`` and ``tightness`` across step()
    calls. ``reset()`` re-seeds the RNG and re-initialises both to
    their starting values. The first ``step()`` call needs a
    ``contract_rate`` context to anchor the spot starting value
    (since a brand-new lane has no historical spot rate to recurse
    from).

    >>> from app.services.digital_twin.physics import (
    ...     SpotRateModel, SpotRateParams, SpotRateContext,
    ... )
    >>> model = SpotRateModel(SpotRateParams())
    >>> model.reset(scenario_seed=42)
    >>> outcome = model.step(SpotRateContext(
    ...     contract_rate=2.50, day_of_year=300, shock_tightness=0.0,
    ... ))
    >>> outcome.spot_rate > 0
    True
    """

    def __init__(self, params: SpotRateParams | None = None) -> None:
        self.params = params or SpotRateParams()
        self._rng: random.Random = random.Random()
        self._twin_mode: Any = None
        self._reset_called = False
        self._spot_rate: float = 0.0
        self._tightness: float = 0.0

    def reset(
        self,
        *,
        scenario_seed: int = 42,
        twin_mode: Any = None,
        initial_spot_rate: float | None = None,
    ) -> None:
        self._rng = random.Random(scenario_seed)
        self._twin_mode = twin_mode
        self._tightness = self.params.initial_tightness
        # Spot starts undefined; first step() anchors it to
        # contract_rate when the caller hasn't supplied an explicit
        # initial.
        self._spot_rate = (
            initial_spot_rate if initial_spot_rate is not None else 0.0
        )
        self._reset_called = True

    @property
    def spot_rate(self) -> float:
        return self._spot_rate

    @property
    def tightness(self) -> float:
        return self._tightness

    def step(
        self,
        context: SpotRateContext,
        *,
        t: int | None = None,  # noqa: ARG002
    ) -> SpotRateOutcome:
        if not self._reset_called:
            raise RuntimeError("SpotRateModel.step called before reset()")

        # Anchor spot to contract on the very first step (or whenever
        # the caller has zeroed it out).
        if self._spot_rate <= 0:
            self._spot_rate = context.contract_rate

        # Step 1: season factor (sinusoidal — peaks in Q4, trough Q1).
        # Phase: shift so Q4 (Oct-Dec, day 274-365) is the seasonal
        # high. Use a single sine cycle per year, peak at day 320.
        phase = 2.0 * math.pi * (context.day_of_year - 320) / 365.0
        season_factor = self.params.season_amplitude * math.cos(phase)

        # Step 2: tightness AR(1) recursion.
        new_tightness = (
            self.params.alpha * self._tightness
            + (1.0 - self.params.alpha) * (0.5 + season_factor)
            + context.shock_tightness
        )
        new_tightness = max(0.0, min(1.0, new_tightness))

        # Step 3: spot-rate AR(1) recursion with noise.
        is_plan_production = self._is_plan_production_mode()
        long_run_mean = context.contract_rate * (
            1.0 + self.params.kappa * new_tightness
        )
        if is_plan_production:
            epsilon = 0.0
        else:
            sigma = self.params.sigma_epsilon_ratio * context.contract_rate
            epsilon = self._rng.gauss(0.0, sigma)
        new_spot = (
            self.params.beta * self._spot_rate
            + (1.0 - self.params.beta) * long_run_mean
            + epsilon
        )
        new_spot = max(0.0, new_spot)  # never negative

        self._tightness = new_tightness
        self._spot_rate = new_spot

        return SpotRateOutcome(
            spot_rate=new_spot,
            tightness=new_tightness,
            season_factor=season_factor,
            epsilon=epsilon,
        )

    def _is_plan_production_mode(self) -> bool:
        if self._twin_mode is None:
            return False
        v = getattr(self._twin_mode, "value", self._twin_mode)
        return str(v).lower() == "plan_production"
