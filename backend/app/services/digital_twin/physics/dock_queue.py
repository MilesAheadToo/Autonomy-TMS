"""Dock Queue physics — model §4.3 of TMS_TWIN_PHYSICS_DESIGN.md.

**Question this model answers:** given an appointment of type T with
carrier C and equipment E, how long does it dwell at the dock?

This is the **per-appointment dwell-time draw** — distinct from the
full per-facility queue dynamics envisioned in §4.3. Phase-1B keeps
the dwell distribution as a stateless physics model; the full
queue-state machine (door pool, FIFO queue, tick-decremented
appointments) is deferred until DockScheduling TRM training requires
it. Until then, the simulator pairs this model's dwell-time draw
with its existing ``dock_queue_depth`` counter.

**Bootstrap prior** (this PR — what tenants without ``Appointment``
checkout history get):

- **live_load**:    Gamma(α=2, β=45 min) → mean 90 min, mode 45 min
- **live_unload**:  Gamma(α=2, β=30 min) → mean 60 min, mode 30 min
- **drop_hook**:    Gamma(α=3, β=15 min) → mean 45 min, mode 30 min

These are point-in-time priors; per-carrier reputation shift
(``CarrierScorecard.avg_dwell``) lands when calibration arrives in PR-6.

**Detention** = max(0, dwell − free_time) × detention_rate. Default
free time = 120 min, detention rate = $50/hour (industry baseline).
Both configurable per tenant via ``DockQueueParams``.

**TwinMode discipline.** PLAN_PRODUCTION returns the Gamma mean (α×β),
not a sample.
"""
from __future__ import annotations

import enum
import random
from dataclasses import dataclass
from typing import Any


class AppointmentType(str, enum.Enum):
    """The three appointment patterns covered by §4.3."""

    LIVE_LOAD = "live_load"
    """Trailer is loaded with the driver waiting; longest baseline
    dwell."""
    LIVE_UNLOAD = "live_unload"
    """Trailer is unloaded with the driver waiting."""
    DROP_HOOK = "drop_hook"
    """Driver drops a loaded trailer and picks up a different empty;
    shortest baseline dwell."""


@dataclass(frozen=True)
class AppointmentContext:
    """Per-appointment features the dwell distribution depends on."""

    carrier_id: str
    equipment_kind: str
    appointment_type: AppointmentType
    free_time_minutes: float = 120.0
    """Tenant-negotiated free dwell window before detention starts.
    Default 120 min (industry baseline). Tune per contract."""
    detention_rate_per_hour: float = 50.0
    """Detention rate $/hour beyond free time. Default $50/hr
    (industry baseline)."""

    def __post_init__(self) -> None:
        if not self.carrier_id:
            raise ValueError("carrier_id must be non-empty")
        if not self.equipment_kind:
            raise ValueError("equipment_kind must be non-empty")
        if self.free_time_minutes < 0:
            raise ValueError(
                f"free_time_minutes must be >= 0; got {self.free_time_minutes}"
            )
        if self.detention_rate_per_hour < 0:
            raise ValueError(
                "detention_rate_per_hour must be >= 0; got "
                f"{self.detention_rate_per_hour}"
            )


@dataclass(frozen=True)
class AppointmentOutcome:
    """Per-appointment result.

    ``dwell_minutes`` is the realised dwell. ``mean_minutes`` is the
    Gamma mean (α×β); ``detention_minutes_over_free`` is the time
    beyond ``free_time_minutes``; ``detention_cost`` is that × the
    rate. The simulator surfaces these on the ``shipment_delivered``
    OutcomeEvent payload.
    """

    dwell_minutes: float
    mean_minutes: float
    detention_minutes_over_free: float
    detention_cost: float


@dataclass
class DockQueueParams:
    """Bootstrap-prior Gamma parameters per appointment type.

    Values are fixed by [§4.3 of the design doc](../../../../../docs/TMS_TWIN_PHYSICS_DESIGN.md);
    construct with overrides only when fitting against tenant
    Appointment checkout history (PR-6) or running ablations.
    """

    live_load_alpha: float = 2.0
    live_load_beta: float = 45.0  # minutes
    live_unload_alpha: float = 2.0
    live_unload_beta: float = 30.0
    drop_hook_alpha: float = 3.0
    drop_hook_beta: float = 15.0
    version: str = "phase1-bootstrap-2026-05-03"

    def __post_init__(self) -> None:
        for name, val in [
            ("live_load_alpha", self.live_load_alpha),
            ("live_load_beta", self.live_load_beta),
            ("live_unload_alpha", self.live_unload_alpha),
            ("live_unload_beta", self.live_unload_beta),
            ("drop_hook_alpha", self.drop_hook_alpha),
            ("drop_hook_beta", self.drop_hook_beta),
        ]:
            if val <= 0:
                raise ValueError(
                    f"{name} must be > 0; got {val}"
                )

    def for_type(self, appointment_type: AppointmentType) -> tuple[float, float]:
        return {
            AppointmentType.LIVE_LOAD:
                (self.live_load_alpha, self.live_load_beta),
            AppointmentType.LIVE_UNLOAD:
                (self.live_unload_alpha, self.live_unload_beta),
            AppointmentType.DROP_HOOK:
                (self.drop_hook_alpha, self.drop_hook_beta),
        }[appointment_type]


class DockQueueModel:
    """Stateless dwell-time draw per appointment.

    Lifecycle mirrors :class:`CarrierAcceptanceModel`:

    >>> from app.services.digital_twin.physics import (
    ...     DockQueueModel, DockQueueParams,
    ...     AppointmentContext, AppointmentType,
    ... )
    >>> model = DockQueueModel(DockQueueParams())
    >>> model.reset(scenario_seed=42)
    >>> ctx = AppointmentContext(
    ...     carrier_id="acme", equipment_kind="dry_van_53",
    ...     appointment_type=AppointmentType.LIVE_LOAD,
    ... )
    >>> outcome = model.step(ctx)
    >>> outcome.dwell_minutes > 0
    True

    Per-appointment outcomes carry detention math; the simulator
    aggregates them into shipment_delivered OutcomeEvent payloads
    for downstream training-corpus / DockScheduling reward.
    """

    def __init__(self, params: DockQueueParams | None = None) -> None:
        self.params = params or DockQueueParams()
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
        context: AppointmentContext,
        *,
        t: int | None = None,  # noqa: ARG002 — protocol-level param
    ) -> AppointmentOutcome:
        if not self._reset_called:
            raise RuntimeError("DockQueueModel.step called before reset()")

        alpha, beta = self.params.for_type(context.appointment_type)
        mean_minutes = alpha * beta

        if self._is_plan_production_mode():
            dwell = mean_minutes
        else:
            # Python's gammavariate uses the (alpha, beta) shape-scale
            # parameterisation matching scipy.stats.gamma — same Gamma
            # this docstring describes.
            dwell = self._rng.gammavariate(alpha, beta)

        detention_minutes = max(0.0, dwell - context.free_time_minutes)
        detention_cost = detention_minutes * (context.detention_rate_per_hour / 60.0)

        return AppointmentOutcome(
            dwell_minutes=dwell,
            mean_minutes=mean_minutes,
            detention_minutes_over_free=detention_minutes,
            detention_cost=detention_cost,
        )

    def _is_plan_production_mode(self) -> bool:
        if self._twin_mode is None:
            return False
        v = getattr(self._twin_mode, "value", self._twin_mode)
        return str(v).lower() == "plan_production"
