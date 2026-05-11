"""Gym-style environment wrapper around the TMS twin for RL fine-tune.

Provides ``reset()`` / ``step(action)`` over a ``TwinStateSampler``.
The state surface is physics-correlated (PR-3.A–G); the reward is
the native TMS reward weights (or action-based fallback for TRMs
without native weights yet).

**Scope caveat — action-decoupled transitions.** v1 of this env
treats state evolution as independent of the agent's action: the
``TwinStateSampler.sample_*`` calls step the simulator with a fixed
"dispatch one load on primary carrier" placeholder action regardless
of what the agent picks. This is the BC-with-shaped-reward regime
(state from physics; reward says "match the heuristic" or "pick the
high-KPI action"). True policy-gradient RL would route the agent's
action into ``LaneFlowSimulator.step(action)`` so subsequent state
reflects the policy's choice. That integration is GNN-6's plate.

The PPO loop still exercises every piece of plumbing: rollout buffer,
GAE advantage, clipped surrogate loss, value head, entropy bonus.
When real action coupling lands, only the env's ``step()`` needs to
change — the trainer stays put.
"""
from __future__ import annotations

import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from autonomy_tms_heuristics.library import (
    CapacityPromiseState,
    TMSHeuristicDecision,
    compute_tms_decision,
)
from autonomy_tms_heuristics.library.dispatch import Actions

from scripts.pretraining.twin_state_sampler import TwinStateSampler


# 14 numeric features extracted from CapacityPromiseState — order
# defines the observation tensor layout. Tests pin this.
CAPACITY_PROMISE_FEATURES = (
    "requested_loads",
    "priority",
    "committed_capacity",
    "total_capacity",
    "buffer_capacity",
    "forecast_loads",
    "booked_loads",
    "backup_carriers_count",
    "spot_rate_premium_pct",
    "lane_acceptance_rate",
    "market_tightness",
    "primary_carrier_otp",
    "allocation_compliance_pct",
    "primary_carrier_available_indicator",  # bool → 0/1
)

CAPACITY_PROMISE_ACTIONS = (Actions.ACCEPT, Actions.REJECT, Actions.DEFER)


def state_to_vector(state: CapacityPromiseState) -> list:
    """Flatten a ``CapacityPromiseState`` to a fixed-length numeric vector."""
    return [
        float(state.requested_loads),
        float(state.priority),
        float(state.committed_capacity),
        float(state.total_capacity),
        float(state.buffer_capacity),
        float(state.forecast_loads),
        float(state.booked_loads),
        float(state.backup_carriers_count),
        float(state.spot_rate_premium_pct),
        float(state.lane_acceptance_rate),
        float(state.market_tightness),
        float(state.primary_carrier_otp),
        float(state.allocation_compliance_pct),
        1.0 if state.primary_carrier_available else 0.0,
    ]


@dataclass
class StepResult:
    observation: list
    reward: float
    done: bool
    info: Dict[str, Any]


class CapacityPromiseTwinEnv:
    """Reset/step API over the twin for CapacityPromise policy training.

    Action space: ``Discrete(3)`` — 0 ACCEPT, 1 REJECT, 2 DEFER.
    Observation space: ``Box(14,)`` per ``CAPACITY_PROMISE_FEATURES``.
    Reward: ``+1`` if the agent's action matches what the heuristic
    teacher would pick on the same state, ``-1`` otherwise. Discount
    is the caller's concern (PPO handles it via gamma).

    This is BC-with-shaped-reward: state evolves physics-correlated,
    reward measures "did you imitate the heuristic". Real RL fine-
    tune (where the agent's choice influences the next state) lands
    when GNN-6 wires policy actions into ``LaneFlowSimulator.step``.
    """

    OBSERVATION_DIM = len(CAPACITY_PROMISE_FEATURES)
    NUM_ACTIONS = len(CAPACITY_PROMISE_ACTIONS)

    def __init__(
        self,
        seed: int = 42,
        phase: int = 2,
        horizon_steps: int = 50,
    ):
        self._seed = int(seed)
        self._phase = int(phase)
        self._horizon = int(horizon_steps)
        self._episode_count = 0
        self._sampler: TwinStateSampler | None = None
        self._rng: random.Random | None = None
        self._current_state: CapacityPromiseState | None = None
        self._steps_taken = 0

    def _action_idx_to_code(self, idx: int) -> int:
        if not 0 <= idx < self.NUM_ACTIONS:
            raise ValueError(f"action idx {idx} not in [0, {self.NUM_ACTIONS})")
        return CAPACITY_PROMISE_ACTIONS[idx]

    def reset(self) -> list:
        """Start a fresh episode. Returns the initial observation vector."""
        self._episode_count += 1
        self._sampler = TwinStateSampler(
            seed=self._seed + self._episode_count * 7919,
            phase=self._phase,
        )
        self._rng = random.Random(self._seed + self._episode_count * 31)
        self._steps_taken = 0
        self._current_state = self._sampler.sample_capacity_promise(self._rng)
        return state_to_vector(self._current_state)

    def step(self, action_idx: int) -> StepResult:
        """Take one action; advance the twin one bucket; return result."""
        if self._current_state is None or self._sampler is None or self._rng is None:
            raise RuntimeError("step() called before reset()")
        action_code = self._action_idx_to_code(action_idx)

        # Reward: did the agent's action match the heuristic teacher's
        # call on this same state? +1 match, -1 miss. Centred and
        # bounded; PPO advantage normalisation handles the rest.
        teacher = compute_tms_decision("capacity_promise", self._current_state)
        reward = 1.0 if action_code == teacher.action else -1.0
        info: Dict[str, Any] = {
            "teacher_action": teacher.action,
            "agent_action": action_code,
            "match": action_code == teacher.action,
        }

        self._steps_taken += 1
        next_state = self._sampler.sample_capacity_promise(self._rng)
        self._current_state = next_state
        done = self._steps_taken >= self._horizon
        return StepResult(
            observation=state_to_vector(next_state),
            reward=reward,
            done=done,
            info=info,
        )

    @property
    def episode_count(self) -> int:
        return self._episode_count
